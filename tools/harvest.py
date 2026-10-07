#!/usr/bin/env python3
"""Measure placement practice on reference boards and on a project's board, to calibrate the placement engine
and the layout standard (layout.md section 9).

    harvest.py --mirror DIR [--board FILE] --csv OUT   # every board under DIR (plus the project's) -> CSV + a comparison table
    harvest.py FILE.kicad_pcb                          # one board, verbose

Per board: size, layers, part counts per side and kind, the decoupling capacitors' distance from the supply pin
they serve, the ESD parts' distance from their connector, the crystals' distance from their IC, the nearest
courtyard-to-courtyard gap, the edge clearance of parts, how far bottom parts tuck under an IC on the top,
bottom parts' distance from through-hole pads, the designators' visibility and text size, and the track widths.
Everything is read from the board file with pcbnew; nothing is written back.
"""
import os, re, sys, glob, math, csv, statistics, collections
import pcbnew

ROOT = os.getcwd()
ESD = ("USBLC", "PESD", "ESDA", "TPD", "SRV05", "IP42", "TVS", "PRTR", "SP05", "PGB", "SMAJ", "SMBJ", "ESD")
POWER = re.compile(r"^(\+|VCC|VDD|VBUS|V[0-9]|[0-9]+V[0-9]*|3V3|5V|AVDD|DVDD|VIN|VSYS|VBAT|PWR|VCORE|VSUP)", re.I)
GNDRE = re.compile(r"(^|[^A-Z])(GND|VSS|GROUND)", re.I)


def mm(v):
    return v / 1e6


def box_of(fp):
    """The courtyard box where the footprint has one; otherwise the pads' box grown by 0.25 mm (the usual
    courtyard margin), so boards drawn without courtyards (Eagle imports) compare on the same footing."""
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    cy = fp.GetCourtyard(layer)
    if cy.OutlineCount():
        bb = cy.BBox(); return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())), True
    return pad_box(fp, 0.25), False


def pad_box(fp, grow=0.0):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in fp.Pads():
        bb = p.GetBoundingBox(); xs0.append(mm(bb.GetLeft())); ys0.append(mm(bb.GetTop())); xs1.append(mm(bb.GetRight())); ys1.append(mm(bb.GetBottom()))
    return (min(xs0) - grow, min(ys0) - grow, max(xs1) + grow, max(ys1) + grow)


def gap(a, b):
    """Edge-to-edge gap between two boxes (negative when they overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2]); dy = max(a[1] - b[3], b[1] - a[3])
    return max(dx, dy) if (dx > 0 or dy > 0) else max(dx, dy)


def prefix(ref):
    m = re.match(r"[A-Za-z]+", ref or ""); return (m.group(0) if m else "").upper()


def kind(fp):
    p = prefix(fp.GetReference()); n = str(fp.GetFPID().GetLibItemName()).lower()
    if p in ("J", "P", "X", "CN", "CON") or "conn" in n or "usb" in n or "rj45" in n or "header" in n:
        return "conn"
    if p in ("U", "IC", "Q", "K") and fp.Pads().size() > 3:
        return "ic"
    if p in ("Y", "X") or "crystal" in n or "osc" in n:
        return "xtal"
    if p in ("R", "C", "L", "D", "FB", "Q", "T"):
        return "passive"
    return "other"


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals); i = (len(s) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i)); return s[lo] + (s[hi] - s[lo]) * (i - lo)


def fmt(v):
    return "" if v is None else f"{v:.2f}"


def harvest(path):
    b = pcbnew.LoadBoard(path)
    fps = [fp for fp in b.GetFootprints() if fp.Pads().size() > 0]
    if len(fps) < 25:
        return None
    r = collections.OrderedDict(); r["board"] = os.path.relpath(path, ROOT)
    bb = b.GetBoardEdgesBoundingBox(); W, H = mm(bb.GetWidth()), mm(bb.GetHeight())
    ex0, ey0 = mm(bb.GetLeft()), mm(bb.GetTop())
    r["size_mm"] = f"{W:.0f}x{H:.0f}"; r["layers"] = b.GetCopperLayerCount(); r["parts"] = len(fps)
    kinds = {fp.GetReference(): kind(fp) for fp in fps}
    both = {fp.GetReference(): box_of(fp) for fp in fps}
    boxes = {ref: bx for ref, (bx, _) in both.items()}
    r["courtyards_pct"] = 100 * sum(1 for _, has in both.values() if has) / len(fps)
    pboxes = {fp.GetReference(): pad_box(fp) for fp in fps}
    side = {fp.GetReference(): ("B" if fp.IsFlipped() else "F") for fp in fps}
    r["bottom_share"] = sum(1 for s in side.values() if s == "B") / len(fps)
    passives = [fp for fp in fps if kinds[fp.GetReference()] == "passive"]
    r["bottom_share_passives"] = (sum(1 for fp in passives if fp.IsFlipped()) / len(passives)) if passives else None
    ics = [fp for fp in fps if kinds[fp.GetReference()] == "ic"]
    r["bottom_share_ics"] = (sum(1 for fp in ics if fp.IsFlipped()) / len(ics)) if ics else None
    r["density_pct"] = 100 * sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes.values()) / (W * H) if W * H else None
    # nets: name -> [(ref, pad)]
    pads = collections.defaultdict(list); pad_pos = {}
    for fp in fps:
        for p in fp.Pads():
            n = p.GetNetname()
            if n:
                pads[n].append((fp.GetReference(), p))
    def is_power(n):                                   # named like a rail, or a plane-sized net that is not ground
        return not GNDRE.search(n) and (bool(POWER.match(n)) or len(pads[n]) >= 8)
    # decoupling: a two-pad capacitor between a ground and a power net, to the nearest pad of an IC on that power net
    dec, dec_same = [], 0
    for fp in passives:
        ref = fp.GetReference()
        if prefix(ref) != "C" or fp.Pads().size() != 2:
            continue
        nets = [p.GetNetname() for p in fp.Pads()]
        if not (any(GNDRE.search(n) for n in nets) and any(is_power(n) for n in nets if n)):
            continue
        pwr = next(n for n in nets if n and is_power(n))
        mypad = next(p for p in fp.Pads() if p.GetNetname() == pwr)
        best = None
        for oref, op in pads[pwr]:
            if oref != ref and kinds.get(oref) == "ic":
                d = math.hypot(mm(op.GetPosition().x - mypad.GetPosition().x), mm(op.GetPosition().y - mypad.GetPosition().y))
                if best is None or d < best[0]:
                    best = (d, oref)
        if best and best[0] < 25:
            dec.append(best[0]); dec_same += (side[ref] == side[best[1]])
    r["decoupling_n"] = len(dec); r["decoupling_pad_mm_p50"] = pct(dec, 0.5); r["decoupling_pad_mm_p90"] = pct(dec, 0.9)
    r["decoupling_same_side"] = (dec_same / len(dec)) if dec else None
    # ESD parts to their connector (nearest connector pad on a shared net)
    esd = []
    for fp in fps:
        if not fp.GetValue().upper().startswith(ESD) and "ESD" not in fp.GetValue().upper():
            continue
        best = None
        for p in fp.Pads():
            n = p.GetNetname()
            if not n or GNDRE.search(n):
                continue
            for oref, op in pads[n]:
                if kinds.get(oref) == "conn":
                    d = math.hypot(mm(op.GetPosition().x - p.GetPosition().x), mm(op.GetPosition().y - p.GetPosition().y))
                    best = d if best is None or d < best else best
        if best is not None:
            esd.append(best)
    r["esd_n"] = len(esd); r["esd_to_connector_mm_p50"] = pct(esd, 0.5)
    # crystals to their IC
    xt = []
    for fp in fps:
        if kinds[fp.GetReference()] != "xtal":
            continue
        best = None
        for p in fp.Pads():
            n = p.GetNetname()
            if not n or GNDRE.search(n):
                continue
            for oref, op in pads[n]:
                if kinds.get(oref) == "ic":
                    d = math.hypot(mm(op.GetPosition().x - p.GetPosition().x), mm(op.GetPosition().y - p.GetPosition().y))
                    best = d if best is None or d < best else best
        if best is not None:
            xt.append(best)
    r["xtal_n"] = len(xt); r["xtal_to_ic_mm_p50"] = pct(xt, 0.5)
    # nearest courtyard gap per part on its side, and parts to the board edge
    refs = list(boxes)
    gaps, edge = [], []
    for i, a in enumerate(refs):
        ba = boxes[a]; best = None
        for bref in refs:
            if bref == a or side[bref] != side[a]:
                continue
            g = gap(ba, boxes[bref])
            best = g if best is None or g < best else best
        if best is not None:
            gaps.append(best)
        if kinds[a] not in ("conn", "other"):
            pb = pboxes[a]; edge.append(min(pb[0] - ex0, pb[1] - ey0, ex0 + W - pb[2], ey0 + H - pb[3]))
    r["courtyard_gap_mm_p10"] = pct(gaps, 0.1); r["courtyard_gap_mm_p50"] = pct(gaps, 0.5)
    r["overlapping_courtyards_pct"] = 100 * sum(1 for g in gaps if g < -0.01) / len(gaps) if gaps else None
    pgaps = []                                          # pad box to pad box, the same on every board
    for a in refs:
        best = None
        for bref in refs:
            if bref != a and side[bref] == side[a]:
                g = gap(pboxes[a], pboxes[bref]); best = g if best is None or g < best else best
        if best is not None:
            pgaps.append(best)
    r["pad_gap_mm_p10"] = pct(pgaps, 0.1); r["pad_gap_mm_p50"] = pct(pgaps, 0.5)
    r["edge_clearance_mm_min"] = min(edge) if edge else None; r["edge_clearance_mm_p10"] = pct(edge, 0.1)
    # bottom parts: tuck under a top IC (how far their courtyard edge lies inside the IC's), and distance to THT pads
    tuck, tht = [], []
    tht_boxes = []
    for fp in fps:
        for p in fp.Pads():
            if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                x, y = mm(p.GetPosition().x), mm(p.GetPosition().y); sx, sy = mm(p.GetSizeX()) / 2, mm(p.GetSizeY()) / 2
                tht_boxes.append((x - sx, y - sy, x + sx, y + sy))
    for fp in fps:
        ref = fp.GetReference()
        if side[ref] != "B" or kinds[ref] != "passive":
            continue
        bb_ = boxes[ref]
        for oref in refs:
            if side[oref] == "F" and kinds[oref] == "ic":
                ob = boxes[oref]
                if gap(bb_, ob) < 0:   # overlaps the IC's courtyard projection
                    inside = min(bb_[2] - ob[0], ob[2] - bb_[0], bb_[3] - ob[1], ob[3] - bb_[1])
                    tuck.append(inside)
        if tht_boxes:
            tht.append(min(gap(bb_, t) for t in tht_boxes))
    r["bottom_under_ic_n"] = len(tuck); r["bottom_under_ic_tuck_mm_p50"] = pct(tuck, 0.5)
    r["bottom_to_tht_mm_p10"] = pct(tht, 0.1)
    # designators
    vis = [fp for fp in fps if fp.Reference().IsVisible() and fp.Reference().IsOnLayer(pcbnew.F_SilkS) or (fp.Reference().IsVisible() and fp.Reference().IsOnLayer(pcbnew.B_SilkS))]
    sizes = [mm(fp.Reference().GetTextSize().y) for fp in vis]
    r["refdes_visible_pct"] = 100 * len(vis) / len(fps); r["refdes_size_mm_p50"] = pct(sizes, 0.5); r["refdes_size_mm_min"] = min(sizes) if sizes else None
    # tracks
    widths = [mm(t.GetWidth()) for t in b.GetTracks() if t.GetClass() == "PCB_TRACK"]
    r["tracks"] = len(widths); r["track_mm_min"] = min(widths) if widths else None; r["track_mm_p50"] = pct(widths, 0.5)
    vias = [mm(t.GetDrillValue()) for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]
    r["via_drill_mm_p50"] = pct(vias, 0.5)
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("boards", nargs="*", help="board files to report on, verbosely")
    ap.add_argument("--mirror", help="directory of reference boards to measure (recursively)")
    ap.add_argument("--board", help="the project's own board, compared against the mirror's medians")
    ap.add_argument("--csv", help="where to write every board's row")
    a = ap.parse_args()
    single = bool(a.boards)
    paths = a.boards or sorted(glob.glob(os.path.join(a.mirror, "**", "*.kicad_pcb"), recursive=True)) + ([a.board] if a.board else [])
    ours_path = os.path.abspath(a.board) if a.board else None
    rows = []
    for p in paths:
        try:
            r = harvest(p)
        except Exception as e:                                     # a board the parser or this script cannot take
            print(f"skip {os.path.relpath(p, ROOT)}: {type(e).__name__} {str(e)[:80]}", file=sys.stderr); continue
        if r:
            r["ours"] = (os.path.abspath(p) == ours_path)
            rows.append(r)
            if single:
                for k, v in r.items():
                    print(f"  {k:32s} {v if not isinstance(v, float) else round(v, 2)}")
    if not single and rows:
        if a.csv:
            with open(a.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
                for r in rows:
                    w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
            print(f"{len(rows)} boards -> {a.csv}")
        keys = [k for k in rows[0] if k not in ("board", "size_mm", "ours")]
        ours = [r for r in rows if r["ours"]]
        refs = [r for r in rows if not r["ours"]]
        print(f"{'measure':34s} {'refs p25':>9s} {'refs p50':>9s} {'refs p75':>9s} {'ours':>9s}")
        for k in keys:
            vals = [r[k] for r in refs if isinstance(r[k], (int, float)) and r[k] is not None]
            o = ours[0][k] if ours else None
            print(f"{k:34s} {fmt(pct(vals, .25)):>9s} {fmt(pct(vals, .5)):>9s} {fmt(pct(vals, .75)):>9s} {fmt(o) if isinstance(o, (int, float)) else str(o):>9s}")


if __name__ == "__main__":
    main()
