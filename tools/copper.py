#!/usr/bin/env python3
"""The copper that comes after routing (layout.md sections 4 and 5): ground floods on the outer layers from the
directives' FLOODS, and ground stitching vias on a grid, clear of everything already on the board.

    copper.py BOARD.kicad_pcb DIRECTIVES.py [--no-stitch]

Runs over the routed board in place (idempotent: a flood that exists by name is kept, stitching skips what is
there), writes the board in canonical order and runs the DRC gate. Directives:

    FLOODS = [("GND_F", "GND", "F.Cu", outline), ("GND_B", "GND", "B.Cu", outline), ("PSU_GND_F", "PSU_GND", "F.Cu", island), ...]
    STITCH = [{"net": "GND", "pitch": 5.0, "keep_out": [(x0, y0, x1, y1), ...], "margin": 1.5},
              {"net": "PSU_GND", "pitch": 5.0, "inside": [(x0, y0, x1, y1), ...]}]
    STITCH_VIA = (0.6, 0.3)
"""
import os, re, sys, math, argparse, importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew
import placer

MM = pcbnew.VECTOR2I_MM


def add_flood(board, name, net, layer, outline, netinfo):
    zn = pcbnew.ZONE(board); zn.SetLayer(board.GetLayerID(layer)); zn.SetNet(netinfo[net]); zn.SetZoneName(name)
    zn.SetMinThickness(pcbnew.FromMM(0.25)); zn.SetLocalClearance(pcbnew.FromMM(0.3)); zn.SetAssignedPriority(0)
    ol = zn.Outline(); ol.NewOutline()
    for x, y in outline:
        ol.Append(MM(x, y))
    board.Add(zn); return zn


def seg_dist(p, a, b):
    (ax, ay), (bx, by), (px, py) = a, b, p
    dx, dy = bx - ax, by - ay; l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def stitch(board, L, cfg, netinfo, via_dia, via_drill, placed):
    """Ground vias on a grid: every spot inside the directive's rectangles (the board less the margin by default)
    that clears pads, tracks, vias, the keep-outs that forbid vias, the directive's keep-out rectangles, crystals
    and the edge margin. Idempotent: a via of the net already within half a pitch counts as the grid point."""
    net = cfg["net"]; pitch = cfg["pitch"]; margin = cfg.get("margin", 1.5)
    W, H = L.BOARD
    inside = [tuple(k) for k in cfg.get("inside", [(margin, margin, W - margin, H - margin)])]
    r = via_dia / 2
    pads, tracks, vias, courts = [], [], [], []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            bb = p.GetBoundingBox(); pads.append((bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6))
        if str(fp.GetReference()).startswith("Y"):                   # nothing under a crystal
            cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
            if cy.OutlineCount():
                bb = cy.BBox(); courts.append((bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6))
    own = []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition(); vias.append((p.x / 1e6, p.y / 1e6, t.GetWidth(pcbnew.F_Cu) / 2e6))
            if str(t.GetNetname()) == net:
                own.append(vias[-1])
        elif t.GetClass() == "PCB_TRACK":
            s, e = t.GetStart(), t.GetEnd(); tracks.append((s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6, t.GetWidth() / 2e6))
    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    others = []                                                        # copper zones of other nets: no sliver left between a via and the zone's edge
    for z in board.Zones():
        if not z.GetIsRuleArea() and str(z.GetNetname()) != net:
            ol = z.Outline().Outline(0); pts = [(ol.CPoint(i).x / 1e6, ol.CPoint(i).y / 1e6) for i in range(ol.PointCount())]
            others.append((z, [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]))
    rects = [tuple(k) for k in cfg.get("keep_out", [])]
    def clear(x, y):
        if not any(x0 + r <= x <= x1 - r and y0 + r <= y <= y1 - r for x0, y0, x1, y1 in inside):
            return False
        for x0, y0, x1, y1 in rects + courts:
            if x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r:
                return False
        for x0, y0, x1, y1 in pads:
            if x0 - r - 0.3 <= x <= x1 + r + 0.3 and y0 - r - 0.3 <= y <= y1 + r + 0.3:
                return False
        for vx, vy, vr in vias:
            if math.hypot(x - vx, y - vy) < r + vr + 0.3:
                return False
        for ax, ay, bx, by, hw in tracks:
            if seg_dist((x, y), (ax, ay), (bx, by)) < r + hw + 0.3:
                return False
        pt = MM(x, y)
        for z, edges in others:
            d = min(seg_dist((x, y), a, b) for a, b in edges)
            if d < r + 0.3 + (0.5 if z.Outline().Contains(pt) else 0):   # inside: the fill between via and edge must keep the min width
                return False
        return not any(z.Outline().Collide(pt, pcbnew.FromMM(r + 0.01)) for z in keepouts)   # the via's body, not just its centre
    step = 0.5; reach = pitch / 2 - step
    nearby = sorted({(round(i * step, 3), round(j * step, 3)) for i in range(-int(reach / step), int(reach / step) + 1)
                     for j in range(-int(reach / step), int(reach / step) + 1) if math.hypot(i * step, j * step) <= reach},
                    key=lambda d: (math.hypot(*d), d))
    y = margin + 1.0
    while y < H - margin:
        x = margin + 1.0
        while x < W - margin:
            if not any(math.hypot(x - vx, y - vy) < pitch / 2 for vx, vy, vr in own):
                for dx, dy in nearby:                                 # the grid point, else the nearest clear spot around it
                    if clear(x + dx, y + dy):
                        v = pcbnew.PCB_VIA(board); v.SetPosition(MM(x + dx, y + dy)); v.SetWidth(pcbnew.FromMM(via_dia)); v.SetDrill(pcbnew.FromMM(via_drill))
                        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(netinfo[net]); board.Add(v)
                        vias.append((x + dx, y + dy, r)); own.append((x + dx, y + dy, r)); placed.append(v)
                        break
            x += pitch
        y += pitch


def islands(board, skip_nets):
    """{(zone name, layer): [(area mm2, bbox)]} of every filled outline of the copper zones of other nets."""
    out = {}
    for z in board.Zones():
        if z.GetIsRuleArea() or str(z.GetNetname()) in skip_nets:
            continue
        for l in z.GetLayerSet().Seq():
            polys = z.GetFilledPolysList(l); key = (str(z.GetZoneName()), l)
            for i in range(polys.OutlineCount()):
                o = polys.Outline(i); bb = o.BBox()
                out.setdefault(key, []).append((o.Area() / 1e12, (bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6)))
    return out


def prune(board, before, placed, skip_nets):
    """Fill, and drop every stitching via that cut a new island off a zone of another net (a sliver between the via,
    a pad and the zone's edge: not foreseeable without the fill). Returns the number removed."""
    removed = 0
    for _ in range(6):
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        after = islands(board, skip_nets); guilty = []
        for key, outs in after.items():
            old = before.get(key, [])
            if len(outs) <= len(old):
                continue
            for area, (x0, y0, x1, y1) in outs:                      # the new ones: no old outline with this bbox
                if any(abs(ox0 - x0) < 0.05 and abs(oy0 - y0) < 0.05 and abs(ox1 - x1) < 0.05 and abs(oy1 - y1) < 0.05 for _, (ox0, oy0, ox1, oy1) in old):
                    continue
                for v in placed:
                    p = v.GetPosition(); x, y = p.x / 1e6, p.y / 1e6
                    if x0 - 1.5 <= x <= x1 + 1.5 and y0 - 1.5 <= y <= y1 + 1.5 and v not in guilty:
                        guilty.append(v)
        if not guilty:
            break
        for v in guilty:
            board.Remove(v); placed.remove(v); removed += 1
        board.BuildConnectivity()
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board"); ap.add_argument("directives"); ap.add_argument("--no-stitch", action="store_true")
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("directives", a.directives); L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
    path = os.path.abspath(a.board)
    board = pcbnew.LoadBoard(path)
    netinfo = {str(n): item for n, item in board.GetNetsByName().items() if str(n)}
    have = {str(z.GetZoneName()) for z in board.Zones()}
    added = []
    for name, net, layer, outline in getattr(L, "FLOODS", []):
        if name in have:
            continue
        add_flood(board, name, net, layer, outline, netinfo); added.append(name)
    placed = []; pruned = 0
    if not a.no_stitch and getattr(L, "STITCH", []):
        nets = {cfg["net"] for cfg in L.STITCH}
        pcbnew.ZONE_FILLER(board).Fill(board.Zones()); before = islands(board, nets)
        via_dia, via_drill = getattr(L, "STITCH_VIA", (0.6, 0.3))
        for cfg in L.STITCH:
            stitch(board, L, cfg, netinfo, via_dia, via_drill, placed)
        pruned = prune(board, before, placed, nets)
    for z in board.Zones():                                           # fills are not kept in the file (the DRC gate refills)
        z.UnFill()
    pcbnew.SaveBoard(path, board, True)
    text = open(path, encoding="utf-8").read(); placer.PROJECT = os.path.splitext(os.path.basename(path))[0]
    open(path, "w", encoding="utf-8").write(placer.canonical(text))
    print(f"floods added: {' '.join(added) or '(already there)'}; stitching vias placed: {len(placed)}" + (f" ({pruned} more dropped for cutting an island off a rail)" if pruned else ""))
    placer.drc_gate(path)


if __name__ == "__main__":
    main()
