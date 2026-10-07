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
    routing(b, fps, kinds, pads, W, H, r)
    return r


PAIR_SUFFIX = re.compile(r"^(.*?)(_P|_N|_DP|_DN|\+|-|P|N)$")


def pair_base(n):
    """The pair a net belongs to, by KiCad's naming (_P/_N, +/-, P/N suffixes), and which side it is."""
    m = PAIR_SUFFIX.match(n)
    if not m or len(m.group(1)) < 2:
        return None, None
    base, suf = m.group(1), m.group(2)
    return base, ("P" if suf in ("_P", "_DP", "+", "P") else "N")


def seg_gap(a, bseg):
    """Edge-to-edge gap between two parallel, overlapping track segments, or None."""
    (ax0, ay0, ax1, ay1, aw), (bx0, by0, bx1, by1, bw) = a, bseg
    adx, ady = ax1 - ax0, ay1 - ay0; al = math.hypot(adx, ady)
    bdx, bdy = bx1 - bx0, by1 - by0; bl = math.hypot(bdx, bdy)
    if al < 0.3 or bl < 0.3:
        return None
    cos = abs(adx * bdx + ady * bdy) / (al * bl)
    if cos < math.cos(math.radians(8)):
        return None
    ux, uy = adx / al, ady / al                                   # along a; projections of b's ends onto a
    t0 = ((bx0 - ax0) * ux + (by0 - ay0) * uy); t1 = ((bx1 - ax0) * ux + (by1 - ay0) * uy)
    if max(t0, t1) < 0 or min(t0, t1) > al:
        return None
    d = abs((bx0 - ax0) * uy - (by0 - ay0) * ux)                  # perpendicular distance between the centre lines
    g = d - (aw + bw) / 2
    return g if 0 <= g < 2.0 else None


def routing(b, fps, kinds, pads, W, H, r):
    """Routing practice: the rules the board carries, its tracks by role and layer, vias, differential pairs
    (gap, width, length match, layer changes), planes and pours, and how pads join zones."""
    ds = b.GetDesignSettings(); ns = ds.m_NetSettings
    try:
        d = ns.GetDefaultNetclass()
        r["rule_clearance_mm"] = mm(d.GetClearance()); r["rule_track_mm"] = mm(d.GetTrackWidth())
        r["rule_via_mm"] = mm(d.GetViaDiameter()); r["rule_via_drill_mm"] = mm(d.GetViaDrill())
        pairs_cls = [(str(n), mm(c.GetDiffPairWidth()), mm(c.GetDiffPairGap())) for n, c in ns.GetNetclasses().items()]
        r["rule_classes"] = len(pairs_cls) + 1
        named = [c for c in pairs_cls if re.search(r"(9[05]|100)\s*(R|OHM|Ω)|USB|DIFF|PAIR|LVDS|HDMI|ETH", c[0], re.I)]
        r["rule_pair_width_mm"] = pct([c[1] for c in named], 0.5); r["rule_pair_gap_mm"] = pct([c[2] for c in named], 0.5)
    except Exception:
        pass
    r["rule_min_clearance_mm"] = mm(ds.m_MinClearance) or None; r["rule_min_track_mm"] = mm(ds.m_TrackMinWidth) or None
    r["rule_min_drill_mm"] = mm(ds.m_MinThroughDrill) or None
    tracks = [t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]
    if not tracks:
        r["tracks"] = 0; return
    def is_power(n):
        return bool(n) and not GNDRE.search(n) and (bool(POWER.match(n)) or len(pads[n]) >= 8)
    widths = [mm(t.GetWidth()) for t in tracks]
    r["tracks"] = len(tracks); r["track_mm_min"] = min(widths); r["track_mm_p10"] = pct(widths, 0.1); r["track_mm_p50"] = pct(widths, 0.5)
    sig = [mm(t.GetWidth()) for t in tracks if not is_power(str(t.GetNetname())) and not GNDRE.search(str(t.GetNetname()))]
    pwr = [mm(t.GetWidth()) for t in tracks if is_power(str(t.GetNetname()))]
    r["signal_track_mm_p50"] = pct(sig, 0.5); r["power_track_mm_p50"] = pct(pwr, 0.5); r["power_track_mm_p90"] = pct(pwr, 0.9)
    total = sum(mm(t.GetLength()) for t in tracks) or 1.0
    by_layer = collections.Counter()
    for t in tracks:
        by_layer[str(t.GetLayerName())] += mm(t.GetLength())
    r["track_len_m"] = total / 1000
    r["bottom_track_share"] = by_layer.get("B.Cu", 0) / total
    r["inner_track_share"] = sum(v for k, v in by_layer.items() if k.startswith("In")) / total
    # vias
    r["vias"] = len(vias); r["vias_per_track_cm"] = len(vias) / (total / 10)
    if vias:
        r["via_drill_mm_p50"] = pct([mm(v.GetDrillValue()) for v in vias], 0.5)
        r["via_mm_p50"] = pct([mm(v.GetWidth(v.TopLayer())) for v in vias], 0.5)
        r["via_blind_micro_share"] = sum(1 for v in vias if v.GetViaType() != pcbnew.VIATYPE_THROUGH) / len(vias)
        gnd_vias = [v for v in vias if GNDRE.search(str(v.GetNetname()))]
        r["gnd_vias_per_cm2"] = len(gnd_vias) / (W * H / 100) if W * H else None
    # differential pairs
    segs = collections.defaultdict(list)
    for t in tracks:
        n = str(t.GetNetname())
        if n:
            segs[n].append((mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()), str(t.GetLayerName())))
    via_count = collections.Counter(str(v.GetNetname()) for v in vias)
    pairs = collections.defaultdict(dict)
    for n in segs:
        base, side = pair_base(n)
        if base:
            pairs[base][side] = n
    gaps, pw, mism, single, pvias, n_pairs = [], [], [], 0, [], 0
    for base, sides in pairs.items():
        if "P" not in sides or "N" not in sides:
            continue
        sp, sn = segs[sides["P"]], segs[sides["N"]]
        g = [x for a in sp for bseg in sn for x in [seg_gap(a[:5], bseg[:5])] if x is not None]
        if len(g) < 2:
            continue                                               # not routed as a pair (or not routed yet)
        n_pairs += 1
        gaps.append(pct(g, 0.5)); pw.append(pct([s[4] for s in sp + sn], 0.5))
        lp = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in sp); ln = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in sn)
        mism.append(abs(lp - ln)); single += (len({s[5] for s in sp + sn}) == 1)
        pvias.append(via_count[sides["P"]] + via_count[sides["N"]])
    r["pairs"] = n_pairs
    if n_pairs:
        r["pair_gap_mm_p50"] = pct(gaps, 0.5); r["pair_width_mm_p50"] = pct(pw, 0.5); r["pair_mismatch_mm_p50"] = pct(mism, 0.5)
        r["pair_mismatch_mm_p90"] = pct(mism, 0.9); r["pair_single_layer_share"] = single / n_pairs; r["pair_vias_p50"] = pct(pvias, 0.5)
    # planes and pours
    zones = [z for z in b.Zones() if not z.GetIsRuleArea() and z.GetNetname()]
    def zarea(z):
        a = mm(mm(z.GetFilledArea())) if hasattr(z, "GetFilledArea") else 0
        return a if a > 0 else mm(mm(z.Outline().Area()))
    board_area = W * H if W * H else 1.0
    r["copper_zones"] = len(zones)
    def zlayers(z):                                     # a zone's layers (GetLayerName is not reliable for inner layers)
        return [str(b.GetLayerName(l)) for l in z.GetLayerSet().Seq() if l <= pcbnew.B_Cu or str(b.GetLayerName(l)).endswith(".Cu")]
    inner = [z for z in zones if any(l.startswith("In") for l in zlayers(z))]
    outer = [z for z in zones if any(l in ("F.Cu", "B.Cu") for l in zlayers(z))]
    gnd_by_layer = collections.Counter()
    for z in inner:
        if GNDRE.search(str(z.GetNetname())):
            for l in zlayers(z):
                if l.startswith("In"):
                    gnd_by_layer[l] += zarea(z)
    if b.GetCopperLayerCount() > 2:
        r["inner_gnd_plane"] = any(a > 0.5 * board_area for a in gnd_by_layer.values())
        r["inner_gnd_on_in1"] = gnd_by_layer.get("In1.Cu", 0) > 0.5 * board_area
    r["outer_pours"] = len(outer); r["outer_gnd_pour_share"] = min(1.0, sum(zarea(z) for z in outer if GNDRE.search(str(z.GetNetname()))) / (2 * board_area))
    r["outer_power_pours"] = sum(1 for z in outer if not GNDRE.search(str(z.GetNetname())))
    if zones:
        r["zone_thermal_share"] = sum(1 for z in zones if z.GetPadConnection() in (pcbnew.ZONE_CONNECTION_THERMAL, pcbnew.ZONE_CONNECTION_THT_THERMAL)) / len(zones)
        r["zone_thermal_gap_mm_p50"] = pct([mm(z.GetThermalReliefGap()) for z in zones], 0.5)
        r["zone_spoke_mm_p50"] = pct([mm(z.GetThermalReliefSpokeWidth()) for z in zones], 0.5)
        r["zone_min_width_mm_p50"] = pct([mm(z.GetMinThickness()) for z in zones], 0.5)
        r["zone_clearance_mm_p50"] = pct([mm(z.GetLocalClearance()) for z in zones if z.GetLocalClearance() > 0], 0.5)


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
        fields = []
        for r in rows:                                             # the union of every board's columns, in first-seen order
            fields += [k for k in r if k not in fields]
        if a.csv:
            with open(a.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
                for r in rows:
                    w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
            print(f"{len(rows)} boards -> {a.csv}")
        keys = [k for k in fields if k not in ("board", "size_mm", "ours")]
        ours = [r for r in rows if r["ours"]]
        refs = [r for r in rows if not r["ours"]]
        print(f"{'measure':34s} {'refs p25':>9s} {'refs p50':>9s} {'refs p75':>9s} {'ours':>9s}")
        for k in keys:
            vals = [r.get(k) for r in refs if isinstance(r.get(k), (int, float)) and r.get(k) is not None]
            o = ours[0].get(k) if ours else None
            print(f"{k:34s} {fmt(pct(vals, .25)):>9s} {fmt(pct(vals, .5)):>9s} {fmt(pct(vals, .75)):>9s} {fmt(o) if isinstance(o, (int, float)) else str(o):>9s}")


if __name__ == "__main__":
    main()
