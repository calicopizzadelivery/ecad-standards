#!/usr/bin/env python3
"""Check the directives' lanes against a placed board the way placer.py lays them, in seconds and without a generation.

    tools/lanecheck.py BOARD.kicad_pcb DIRECTIVES_DIR [--lanes CANDIDATE.py] [--near X0 Y0 X1 Y1]

For every lane: the corridor of each leg (the class's track plus clearance plus LANE_MARGIN each side, clipped at the
courtyards of the parts the lane joins) against every part's courtyard on the leg's own side (layout.md 2.5), the box
around each via or via pair against both sides, a bottom leg under a crystal or a through-hole row on top, the escape
depth at each end, the P side at both ends (a pair laid with its members crossing), and lane-vs-lane centreline
crossings on one layer. --lanes takes a Python file defining LANES to try instead of the directives' own; --near lists
the parts whose courtyards touch a rectangle (mm), for drawing a path. The findings are the hand-placement report's
before the generator runs; the generator remains the authority (its escapes, bridges and bumps are not modelled here)."""
import sys, os, math, json, fnmatch, importlib.util, argparse

DEFAULTS = {"LANE_MARGIN": 0.25, "ESCAPE_LENGTH": 1.2, "THT_STUB": 1.0, "CHAMFER": 0.8, "VIA_PAIR_OFFSET": 0.45, "THT_MARGIN": 0.5, "EP_MARGIN": 0.6}


def overlap(a, c, margin=0.0):
    return a[0] < c[2] + margin and a[2] > c[0] - margin and a[1] < c[3] + margin and a[3] > c[1] - margin


def unit(a, c):
    dx, dy = c[0] - a[0], c[1] - a[1]; n = math.hypot(dx, dy) or 1.0
    return (dx / n, dy / n)


def cross(a, c):
    return a[0] * c[1] - a[1] * c[0]


def sides_of(layer):
    return {"F"} if layer == "F.Cu" else {"B"} if layer == "B.Cu" else set()


class Context:
    """The board and the directives, read once."""
    def __init__(self, board_path, gen_dir):
        import pcbnew
        sys.path.insert(0, os.path.abspath(gen_dir))
        import layout
        self.L = layout
        self.c = {k: getattr(layout, k, v) for k, v in DEFAULTS.items()}
        folder = os.path.dirname(os.path.abspath(board_path))
        pro = [f for f in os.listdir(folder) if f.endswith(".kicad_pro")]
        if not pro:
            raise SystemExit(f"no .kicad_pro beside {board_path} (the net classes come from it)")
        ns = json.load(open(os.path.join(folder, pro[0])))["net_settings"]
        self.classes = {cl["name"]: cl for cl in ns["classes"]}
        self.patterns = [(p["pattern"], p["netclass"]) for p in ns.get("netclass_patterns", [])]
        self.b = pcbnew.LoadBoard(board_path)
        self.fps = {fp.GetReference(): fp for fp in self.b.GetFootprints()}
        self.boxes, self.nets, self.under = {}, {}, {}
        for r, fp in self.fps.items():
            cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
            bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
            box = (bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6)
            self.boxes[r] = ("B" if fp.IsFlipped() else "F", box)
            blocks = []
            for p in fp.Pads():
                self.nets[(r, p.GetNumber())] = p.GetNetname()
                if not fp.IsFlipped() and p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    x, y = p.GetPosition().x / 1e6, p.GetPosition().y / 1e6; sx, sy = p.GetSizeX() / 2e6, p.GetSizeY() / 2e6; m = self.c["THT_MARGIN"]
                    blocks.append((x - sx - m, y - sy - m, x + sx + m, y + sy + m))
            if not fp.IsFlipped() and r.rstrip("0123456789").endswith("Y"):
                blocks.append(box)
            if blocks:
                self.under[r] = blocks
        self.pcbnew = pcbnew

    def class_of(self, net):
        for pat, cls in self.patterns:
            if fnmatch.fnmatchcase(net, pat) or fnmatch.fnmatchcase(net.split("/")[-1], pat):
                return self.classes[cls]
        return self.classes["Default"]

    def net_named(self, short):
        for n in set(self.nets.values()):
            if n == short or n.endswith("/" + short):
                return n
        raise KeyError(short)

    def pad_geom(self, ref, num):
        p = self.fps[ref].FindPadByNumber(num)
        if p is None:
            raise SystemExit(f"{ref} has no pad {num}")
        c = (p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
        sx, sy = p.GetSizeX() / 1e6, p.GetSizeY() / 1e6
        rot = math.radians(p.GetOrientationDegrees()) if hasattr(p, "GetOrientationDegrees") else math.radians(p.GetOrientation().AsDegrees())
        ax = (math.cos(rot), -math.sin(rot)) if sx >= sy else (math.sin(rot), math.cos(rot))
        return c, max(sx, sy) / 2, ax, p.GetAttribute() in (self.pcbnew.PAD_ATTRIB_PTH, self.pcbnew.PAD_ATTRIB_NPTH)

    def pad_tip(self, ref, num, d):
        c, half, ax, tht = self.pad_geom(ref, num)
        if tht:
            return c
        s = ax[0] * d[0] + ax[1] * d[1]
        if abs(s) < 0.5:
            return c
        k = half - 0.1 if s > 0 else -(half - 0.1)
        return (c[0] + ax[0] * k, c[1] + ax[1] * k)

    def near(self, q):
        return [(r, side, bx) for r, (side, bx) in sorted(self.boxes.items(), key=lambda kv: (kv[1][1][0], kv[1][1][1])) if overlap(q, bx)]


def walk(lane, ends_pads):
    pts, layers, switches, layer = [], [], [], lane.get("layer", "F.Cu")
    for item in lane["path"]:
        if item[0] == "layer":
            switches.append((len(pts) - 1, layer, item[1])); layer = item[1]; continue
        if item[0] in ("x", "y"):
            val = item[1]
            if isinstance(val, tuple):
                val = ends_pads(val)[0 if item[0] == "x" else 1]
            px, py = pts[-1]
            pts.append((val, py) if item[0] == "x" else (px, val))
        else:
            pts.append(ends_pads(item))
        layers.append(layer)
    return pts, layers, switches


def corridors(ctx, pts, layers, hw, ends):
    """(a, b, box, sides) per leg, clipped at the ends' courtyards as placer.add_corridor does."""
    out = []
    for i, (a, c) in enumerate(zip(pts, pts[1:])):
        box = [min(a[0], c[0]) - hw, min(a[1], c[1]) - hw, max(a[0], c[0]) + hw, max(a[1], c[1]) + hw]
        horizontal = abs(a[1] - c[1]) < 1e-6; skip = False
        for ref in ends:
            cb = ctx.boxes[ref][1]
            if not overlap(tuple(box), cb):
                continue
            lo, hi = (0, 2) if horizontal else (1, 3)
            if cb[lo] <= box[lo] and cb[hi] >= box[hi]:
                skip = True; break
            if cb[lo] <= box[lo]:
                box[lo] = cb[hi]
            elif cb[hi] >= box[hi]:
                box[hi] = cb[lo]
            else:
                if (c[0] if horizontal else c[1]) >= (cb[lo] + cb[hi]) / 2:
                    box[hi] = cb[lo]
                else:
                    box[lo] = cb[hi]
        if not skip and box[2] - box[0] > 0.1 and box[3] - box[1] > 0.1:
            out.append((a, c, tuple(box), sides_of(layers[i + 1])))
    return out


def via_boxes(ctx, pts, switches, spread, dia, clear, ends):
    out = []
    for k, _, _ in switches:
        p = pts[k]; r = spread + dia / 2 + clear + ctx.c["LANE_MARGIN"]
        box = [p[0] - r, p[1] - r, p[0] + r, p[1] + r]
        for ref in ends:
            cb = ctx.boxes[ref][1]
            if not overlap(tuple(box), cb):
                continue
            ox = min(box[2], cb[2]) - max(box[0], cb[0]); oy = min(box[3], cb[3]) - max(box[1], cb[1])
            if ox < oy:
                if p[0] >= (cb[0] + cb[2]) / 2: box[0] = cb[2]
                else: box[2] = cb[0]
            else:
                if p[1] >= (cb[1] + cb[3]) / 2: box[1] = cb[3]
                else: box[3] = cb[1]
        own = dia / 2 + clear
        if box[0] > p[0] - own or box[2] < p[0] + own or box[1] > p[1] - own or box[3] < p[1] + own:
            continue
        if box[2] - box[0] > 0.1 and box[3] - box[1] > 0.1:
            out.append((p, tuple(box)))
    return out


def check(ctx, LANES):
    """-> (findings, lines): findings are dicts {lane, kind, ref, depth, text}; lines is the printable report."""
    L, c = ctx.L, ctx.c
    findings, lines, segments = [], [], []
    holes = set(getattr(L, "HOLES", {}))
    def hit(lane, kind, ref, depth, text):
        findings.append({"lane": lane, "kind": kind, "ref": ref, "depth": depth, "text": text}); lines.append("    " + text)
    def parts_in(name, boxes_, ends):
        for a, b_, box, sides in boxes_:
            for r, (side, bx) in ctx.boxes.items():
                if r in ends or r in holes:
                    continue
                if side in sides and overlap(box, bx):
                    depth = min(min(box[2] - bx[0], bx[2] - box[0]), min(box[3] - bx[1], bx[3] - box[1]))
                    hit(name, "through", r, depth, f"lane {name} leg ({a[0]:.2f},{a[1]:.2f})->({b_[0]:.2f},{b_[1]:.2f}) [{'/'.join(sorted(sides))}] runs through {r} ({side}) by {depth:.2f} mm")
                elif sides == {"B"} and side == "F" and any(overlap(box, u) for u in ctx.under.get(r, ())):
                    hit(name, "under", r, 0.0, f"lane {name} leg ({a[0]:.2f},{a[1]:.2f})->({b_[0]:.2f},{b_[1]:.2f}) runs under {r} (a crystal or a through-hole row, section 4)")
    def vias_in(name, vboxes, ends):
        for p, box in vboxes:
            for r, (side, bx) in ctx.boxes.items():
                if r not in ends and r not in holes and overlap(box, bx):
                    depth = min(min(box[2] - bx[0], bx[2] - box[0]), min(box[3] - bx[1], bx[3] - box[1]))
                    hit(name, "via", r, depth, f"lane {name} via at ({p[0]:.2f},{p[1]:.2f}) runs into {r} ({side}) by {depth:.2f} mm")
    for name, lane in LANES.items():
        if "pair" in lane:
            base = lane["pair"]; netP, netN = ctx.net_named(base + "_P"), ctx.net_named(base + "_N")
            cls = ctx.class_of(netP); w, gap, clear = cls["diff_pair_width"], cls["diff_pair_gap"], cls["clearance"]
            off = (w + gap) / 2; hw = off + w / 2 + clear + c["LANE_MARGIN"]
            end_items = [it for it in lane["path"] if it[0] not in ("x", "y", "layer")]
            def pads_of_end(item):
                ref, spec = item[0], item[1]; out = {}
                for side in ("P", "N"):
                    v = spec[side]
                    pairs = [tuple(v)] if ref == "pads" else [(ref, n) for n in (v if isinstance(v, (list, tuple)) else [v])]
                    for r, n in pairs:
                        want = netP if side == "P" else netN
                        if ctx.nets.get((r, n)) != want:
                            hit(name, "net", r, 0.0, f"lane {name}: {r} pad {n} is on {ctx.nets.get((r, n))}, not {want}")
                    out[side] = pairs
                return out
            def end_mid(item):
                spec = pads_of_end(item)
                cs = [ctx.pad_geom(r, n)[0] for side in ("P", "N") for r, n in spec[side]]
                shift = item[2] if len(item) > 2 else (0.0, 0.0)
                return (sum(p[0] for p in cs) / len(cs) + shift[0], sum(p[1] for p in cs) / len(cs) + shift[1])
            pts, layers, switches = walk(lane, end_mid)
            if len(pts) < 2:
                hit(name, "path", "", 0.0, f"lane {name}: needs at least one leg"); continue
            d0 = unit(pts[0], pts[1]); d1 = unit(pts[-2], pts[-1])
            spec0, spec1 = pads_of_end(end_items[0]), pads_of_end(end_items[1])
            doubled = any(len(spec[s]) > 1 for spec in (spec0, spec1) for s in ("P", "N"))
            m0 = {"P": ctx.pad_geom(*spec0["P"][0])[0], "N": ctx.pad_geom(*spec0["N"][0])[0]}
            m1 = {"P": ctx.pad_geom(*spec1["P"][0])[0], "N": ctx.pad_geom(*spec1["N"][0])[0]}
            def side_of(m, d):
                mx, my = (m["P"][0] + m["N"][0]) / 2, (m["P"][1] + m["N"][1]) / 2
                return 1 if cross(d, (m["P"][0] - mx, m["P"][1] - my)) >= 0 else -1
            crossing = (not doubled) and side_of(m1, d1) != side_of(m0, d0)   # doubled pads: the engine chooses the members
            def depth(spec, d, p0):
                deep, tht = 0.0, False
                for side in ("P", "N"):
                    r, n = spec[side][0]; tip = ctx.pad_tip(r, n, d)
                    deep = max(deep, (tip[0] - p0[0]) * d[0] + (tip[1] - p0[1]) * d[1]); tht |= ctx.pad_geom(r, n)[3]
                return deep + (c["THT_STUB"] if tht else 0) + c["ESCAPE_LENGTH"]
            dep0 = depth(spec0, d0, pts[0]); dep1 = depth(spec1, (-d1[0], -d1[1]), pts[-1])
            leg0 = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]); leg1 = math.hypot(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
            total = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
            ends = {r for spec in (spec0, spec1) for side in ("P", "N") for r, _ in spec[side]}
            direct = total < dep0 + dep1 + 1.0
            notes = []
            if crossing:
                notes.append("CROSSING: the P and N pads are on opposite sides at the two ends"); hit(name, "crossing", "", 0.0, f"lane {name}: the P and N pads are on opposite sides at the two ends; swap the array's channels or lead the path in from the other side")
            if direct:
                notes.append(f"direct (too short for a body: {total:.1f} < {dep0 + dep1 + 1.0:.1f} mm)")
            else:
                if leg0 < dep0 + c["CHAMFER"]:
                    hit(name, "escape", "", 0.0, f"lane {name}: the first leg ({leg0:.2f} mm) is shorter than the escape ({dep0:.2f}) and the chamfer")
                if leg1 < dep1 + c["CHAMFER"]:
                    hit(name, "escape", "", 0.0, f"lane {name}: the last leg ({leg1:.2f} mm) is shorter than the escape ({dep1:.2f}) and the chamfer")
            if any(k <= 0 or k >= len(pts) - 1 for k, _, _ in switches):
                hit(name, "path", "", 0.0, f"lane {name}: a layer change needs a leg on each side of it")
            lines.insert(len(lines) - sum(1 for f in findings if f["lane"] == name), f"{name}: {base} pair w{w:g}/g{gap:g} hw {hw:.2f}, {len(pts) - 1} legs, {total:.1f} mm, {len(switches)} layer change(s)" + ("; " + "; ".join(notes) if notes else ""))
            if not direct:
                parts_in(name, corridors(ctx, pts, layers, hw, ends), ends)
                vias_in(name, via_boxes(ctx, pts, switches, c["VIA_PAIR_OFFSET"], cls["via_diameter"], clear, ends), ends)
            for i, (a, b_) in enumerate(zip(pts, pts[1:])):
                segments.append((name, layers[i + 1], a, b_))
        else:
            net = ctx.net_named(lane["net"]); cls = ctx.class_of(net); width = lane.get("width", cls["track_width"]); clear = cls["clearance"]
            hw = width / 2 + clear + c["LANE_MARGIN"]
            def end_pt(item):
                if ctx.nets.get(item) != net:
                    hit(name, "net", item[0], 0.0, f"lane {name}: pad {item} is on {ctx.nets.get(item)}, not {net}")
                return ctx.pad_geom(*item)[0]
            pts, layers, switches = walk(lane, end_pt)
            ends = {item[0] for item in lane["path"] if item[0] not in ("x", "y", "layer")}
            total = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
            lines.append(f"{name}: {net.split('/')[-1]} w{width:g} hw {hw:.2f}, {len(pts) - 1} legs, {total:.1f} mm, {len(switches)} layer change(s)")
            if any(k <= 0 or k >= len(pts) - 1 for k, _, _ in switches):
                hit(name, "path", "", 0.0, f"lane {name}: a layer change needs a leg on each side of it")
            parts_in(name, corridors(ctx, pts, layers, hw, ends), ends)
            vias_in(name, via_boxes(ctx, pts, switches, 0.0, cls["via_diameter"], clear, ends), ends)
            for i, (a, b_) in enumerate(zip(pts, pts[1:])):
                segments.append((name, layers[i + 1], a, b_))
    def seg_cross(a, b_, c_, d):
        def o(p, q, r): return cross((q[0] - p[0], q[1] - p[1]), (r[0] - p[0], r[1] - p[1]))
        o1, o2, o3, o4 = o(a, b_, c_), o(a, b_, d), o(c_, d, a), o(c_, d, b_)
        return (o1 * o2 < -1e-9) and (o3 * o4 < -1e-9)
    for i, (n1, l1, a, b_) in enumerate(segments):
        for n2, l2, c_, d in segments[i + 1:]:
            if n1 != n2 and l1 == l2 and seg_cross(a, b_, c_, d):
                hit(n1, "lane-cross", n2, 0.0, f"lane {n1} crosses {n2} on {l1} near ({(a[0] + b_[0] + c_[0] + d[0]) / 4:.1f},{(a[1] + b_[1] + c_[1] + d[1]) / 4:.1f})")
    lines.append(f"{len(findings)} finding(s)")
    return findings, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board"); ap.add_argument("gen_dir")
    ap.add_argument("--lanes", help="a Python file defining LANES to check instead of the directives' own")
    ap.add_argument("--near", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"), help="list the parts whose courtyards touch this rectangle")
    a = ap.parse_args(argv)
    ctx = Context(a.board, a.gen_dir)
    if a.near:
        for r, side, bx in ctx.near(tuple(a.near)):
            print(f"  {r:6s} {side} ({bx[0]:.2f},{bx[1]:.2f})-({bx[2]:.2f},{bx[3]:.2f})")
        return 0
    if a.lanes:
        spec = importlib.util.spec_from_file_location("cand", a.lanes); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); LANES = m.LANES
    else:
        LANES = ctx.L.LANES
    findings, lines = check(ctx, LANES)
    print("\n".join(lines))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
