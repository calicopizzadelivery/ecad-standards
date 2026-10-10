#!/usr/bin/env python3
"""The layout standard's placement engine: generate a board file from a project's schematic, project file and
layout directives, placed by the rules in layout.md (sections 2, 3, 6 and 9).

    placer.py DIRECTIVES.py OUT_DIR PROJECT [HOUSE_FOOTPRINTS_DIR]
    DEBUG_REF=C401 placer.py ...     # and say where that part's candidate spots were refused

or from a project's own wrapper: `import placer; placer.main(layout, out_dir, project, house_fp)`. The
directives module is the contract (see tools/README.md): BOARD, RADIUS, HOLES, HOLE_KEEPOUT, HOLE_CLEAR_R,
CONNECTORS, FIXED, ANCHORS, EDGE_ZONE, SPARE, STACKUP, and the optional LANES, ISOLATION_REGIONS, PLANES and
the engine's tunables (each with the standard's default here).

Edge connectors are locked at the directives' positions; the ICs sit where the flow puts them (ANCHORS); the
lanes (corridors reserved for a routed path) are laid out from the pads they join and kept free of parts;
every other part is placed at the pin it serves. Its host is the placed part it shares the most specific
nets with (a decoupling capacitor, whose nets are all planes, belongs to the IC the schematic drew it
beside); the attachment point is the host's pads on the shared nets; the part goes on the host's side
nearest that point, two-pin parts turned so the pad carrying the host's net faces it, the parts along a
side packed outward in rings. Small parts of the kinds the directives allow go to the bottom, tucked under
the host's pin row; the rest stay on top. The order: small decoupling capacitors, then the large parts on an
IC's or connector's own pins (inductors, diodes, crystals), then bulk capacitors, then the small parts on
those pins, then parts hosted by a passive (an RC chain), then parts whose partner was not down when they
were considered. A part that fits in no ring takes the nearest free spot to its pin. Then the reference
designators are placed where they overlap nothing, or omitted by the silkscreen rule. Run once per
placement pass: after hand edits the board file is the source of truth.
"""
import os, re, sys, glob, json, math, time, uuid, itertools, subprocess, collections, tempfile
import xml.etree.ElementTree as ET
import importlib.util
import pcbnew

KICAD_FP = "/usr/share/kicad/footprints"
MM = pcbnew.VECTOR2I_MM
DEBUG_REF = os.environ.get("DEBUG_REF")
SILK = {"F": pcbnew.F_SilkS, "B": pcbnew.B_SilkS}
L = OUT = PROJECT = HOUSE_FP = W = H = None       # set by configure(): the project's directives and paths

# the engine's tunables and the standard's defaults (layout.md sections 3, 6 and 9); a directives module overrides any
DEFAULTS = {"EDGE_ZONE": 3.0, "HOLE_CLEAR_R": 4.0, "PACK_MARGIN": 0.15, "RING_GAP": 0.15, "RINGS": 8, "RING_REACH": 8.0,
            "RING_SLIDES": (2.0, 5.0, 12.0, 40.0), "SEARCH_RADIUS": 40.0, "BIG_AREA": 20.0, "SMALL_AREA": 5.0,
            "LANES": {}, "LANE_MARGIN": 0.25, "ISOLATION_REGIONS": [], "PLANES": [],
            "CURRENT_CLASSES": set(), "PAIR_CLASSES": set(), "BOTTOM_MAX_AREA": {"R": 7.0, "C": 7.0, "D": 8.0, "Q": 12.0},
            "BOTTOM_NEVER_CLASSES": set(), "BOTTOM_TUCK": 1.75, "THT_MARGIN": 0.5, "EP_MARGIN": 0.6, "REFDES_SIZES": (0.8, 0.7),
            "ESD_VALUES": ("USBLC", "PESD", "ESDA", "TPD", "SRV05", "IP42", "TVS"), "FIXED": {}, "ANCHORS": {},
            "ESCAPE_WIDTH": 0.2, "ESCAPE_LENGTH": 1.2, "CHAMFER": 0.8, "VIA_PAIR_OFFSET": 0.45, "BRIDGE_DEPTHS": (0.8, 1.6), "THT_STUB": 1.0, "DIRECT_STUB": 0.4,
            "MATCH_TOLERANCE": 0.25, "BUMP_HEIGHT": 2.0, "BUMP_WIDTH": 1.5, "ISLAND_GAP": 6.0, "ISLAND_REACH": None, "ISLAND_SPREAD": 10.0,
            "CITY_GAP": 2.0, "REGULATORS": {}, "LAYOUTS": {}, "TEMPLATED": {}, "COPPER_VOIDS": {}, "SIDES": {},
            "RAIL_VIAS": {}}


class Directives:
    """The project's directives module with the standard's defaults behind it."""
    def __init__(self, module):
        self._m = module
    def __getattr__(self, name):
        if hasattr(self._m, name):
            return getattr(self._m, name)
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise AttributeError(f"the directives do not define {name} and the standard has no default for it")


def configure(directives, out, project, house_fp=None):
    global L, OUT, PROJECT, HOUSE_FP, W, H
    L = Directives(directives); OUT = os.path.abspath(out); PROJECT = project
    HOUSE_FP = os.path.abspath(house_fp) if house_fp else None
    W, H = L.BOARD


def netlist():
    """(components {ref: (value, footprint)}, nets {name: [(ref, pin)]}, classes {net: class}) from kicad-cli's XML export."""
    tmp = os.path.join(tempfile.gettempdir(), f"{PROJECT}-netlist.xml")
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", tmp,
                    os.path.join(OUT, f"{PROJECT}.kicad_sch")], check=True, capture_output=True)
    root = ET.parse(tmp).getroot()
    comps = {c.get("ref"): (c.findtext("value") or "", c.findtext("footprint") or "") for c in root.iter("comp")}
    nets = {n.get("name"): [(x.get("ref"), x.get("pin")) for x in n.findall("node")] for n in root.iter("net")}
    classes = {n.get("name"): (n.get("class") or "Default") for n in root.iter("net")}
    return comps, nets, classes


def class_geometry():
    """{class: (track width, clearance)} from the project file the schematic build wrote."""
    pro = json.load(open(os.path.join(OUT, f"{PROJECT}.kicad_pro"), encoding="utf-8"))
    return {c["name"]: (c["track_width"], c["clearance"]) for c in pro["net_settings"]["classes"]}


def class_pairs_and_vias():
    """{class: (diff pair width, gap)} and {class: (via diameter, drill)} from the project file."""
    pro = json.load(open(os.path.join(OUT, f"{PROJECT}.kicad_pro"), encoding="utf-8"))
    cl = pro["net_settings"]["classes"]
    return ({c["name"]: (c["diff_pair_width"], c["diff_pair_gap"]) for c in cl}, {c["name"]: (c["via_diameter"], c["via_drill"]) for c in cl})


def sch_positions():
    """{ref: (sheet file, x, y)}: where the schematic drew each part. The schematic puts a decoupling
    capacitor beside the IC it serves, which the netlist alone cannot tell."""
    pat = re.compile(r'\(symbol\s+\(lib_id "[^"]+"\)\s+\(at ([-\d.]+) ([-\d.]+)(?: [-\d.]+)?\)(?:(?!\(symbol\s+\().)*?\(property "Reference" "([^"]+)"', re.S)
    pos = {}
    for f in sorted(glob.glob(os.path.join(OUT, "*.kicad_sch"))):
        for x, y, ref in pat.findall(open(f, encoding="utf-8").read()):
            if not ref.startswith("#") and ref not in pos:
                pos[ref] = (os.path.basename(f), float(x), float(y))
    return pos


def sexp(text):
    """A KiCad s-expression as nested lists (strings unquoted, numbers as floats)."""
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', text)
    stack = [[]]
    for tok in tokens:
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop(); stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(tok[1:-1])
        else:
            try:
                stack[-1].append(float(tok))
            except ValueError:
                stack[-1].append(tok)
    return stack[0]


def sch_islands(gap, reach):
    """The schematic's islands (layout.md 3.1): on each sheet, the symbols whose bodies, grown by half the gap, touch,
    or that a wire joins (a pin end on a wire end, on a wire's run, or on another pin) while their bodies lie within
    the reach of each other (a supply bus or a long wire across the sheet joins areas, not an island), transitively.
    Returns {ref: island id} and {island id: (sheet, [refs], bbox)}. A symbol's body and pin ends come from its
    library graphics, placed by the instance's position, mirror and rotation; power symbols and parts without a
    footprint are not members. A label joins nothing: what leaves an island by name is another island's."""
    def lib_geometry(sym):
        xs, ys, pins = [], [], []
        def walk(node):
            for item in node:
                if not isinstance(item, list) or not item:
                    continue
                head = item[0]
                if head == "property":
                    continue
                if head in ("start", "end", "mid", "center", "xy") and len(item) >= 3 and all(isinstance(v, float) for v in item[1:3]):
                    xs.append(item[1]); ys.append(item[2])
                elif head == "pin":
                    at = next((i for i in item if isinstance(i, list) and i and i[0] == "at"), None)
                    ln = next((i for i in item if isinstance(i, list) and i and i[0] == "length"), None)
                    if at and len(at) >= 3:
                        xs.append(at[1]); ys.append(at[2]); pins.append((at[1], at[2]))
                        if ln and len(at) >= 4:
                            a = math.radians(at[3]); xs.append(at[1] + ln[1] * math.cos(a)); ys.append(at[2] + ln[1] * math.sin(a))
                elif head == "circle":
                    c = next((i for i in item if isinstance(i, list) and i and i[0] == "center"), None)
                    r = next((i for i in item if isinstance(i, list) and i and i[0] == "radius"), None)
                    if c and r:
                        xs.extend([c[1] - r[1], c[1] + r[1]]); ys.extend([c[2] - r[1], c[2] + r[1]])
                walk(item)
        walk(sym)
        return ((min(xs), min(ys), max(xs), max(ys)) if xs else (-1.27, -1.27, 1.27, 1.27)), pins
    def place(px, py, at, mirror):
        py = -py                                                   # library y is up, the sheet's is down
        if mirror == "x": py = -py
        if mirror == "y": px = -px
        rot = math.radians(at[3] if len(at) > 3 else 0.0)
        return (at[1] + px * math.cos(rot) + py * math.sin(rot), at[2] - px * math.sin(rot) + py * math.cos(rot))
    def key(p):
        return (round(p[0], 2), round(p[1], 2))
    ref_island, islands = {}, {}
    for f in sorted(glob.glob(os.path.join(OUT, "*.kicad_sch"))):
        doc = sexp(open(f, encoding="utf-8").read())[0]
        libs, wires = {}, []
        for node in doc:
            if not (isinstance(node, list) and node):
                continue
            if node[0] == "lib_symbols":
                for sym in node[1:]:
                    if isinstance(sym, list) and sym and sym[0] == "symbol":
                        libs[sym[1]] = lib_geometry(sym)
            elif node[0] == "wire":
                pts = next((i for i in node if isinstance(i, list) and i and i[0] == "pts"), [])
                xy = [key((i[1], i[2])) for i in pts[1:] if isinstance(i, list) and i and i[0] == "xy"]
                if len(xy) >= 2:
                    wires.append((xy[0], xy[-1]))
        parent = {}
        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        def union(a, b):
            parent[find(a)] = find(b)
        for a, b in wires:
            union(a, b)
        pts = {p for w in wires for p in w}
        def on_run(p, a, b):
            return (p != a and p != b and min(a[0], b[0]) - 0.01 <= p[0] <= max(a[0], b[0]) + 0.01 and min(a[1], b[1]) - 0.01 <= p[1] <= max(a[1], b[1]) + 0.01
                    and abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) < 0.02 * max(1.0, math.hypot(b[0] - a[0], b[1] - a[1])))
        for p in pts:                                              # a wire ending on another wire's run joins it
            for a, b in wires:
                if on_run(p, a, b):
                    union(p, a)
        symbols = []
        for node in doc:
            if not (isinstance(node, list) and node and node[0] == "symbol"):
                continue
            props = {i[1]: i[2] for i in node if isinstance(i, list) and i and i[0] == "property" and len(i) >= 3}
            ref = props.get("Reference", ""); fp = props.get("Footprint", "")
            if ref.startswith("#") or not fp or ref in ref_island or any(ref == s[0] for s in symbols):
                continue
            lib_id = next((i[1] for i in node if isinstance(i, list) and i and i[0] == "lib_id"), None)
            at = next((i for i in node if isinstance(i, list) and i and i[0] == "at"), ["at", 0.0, 0.0, 0.0])
            mirror = next((i[1] for i in node if isinstance(i, list) and i and i[0] == "mirror"), None)
            (x0, y0, x1, y1), pins = libs.get(lib_id, ((-1.27, -1.27, 1.27, 1.27), []))
            corners = [place(px, py, at, mirror) for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            box = (min(c[0] for c in corners) - gap / 2, min(c[1] for c in corners) - gap / 2, max(c[0] for c in corners) + gap / 2, max(c[1] for c in corners) + gap / 2)
            symbols.append((ref, box, [key(place(px, py, at, mirror)) for px, py in pins]))
        sym_parent = {ref: ref for ref, _, _ in symbols}
        def sfind(r):
            while sym_parent[r] != r:
                sym_parent[r] = sym_parent[sym_parent[r]]; r = sym_parent[r]
            return r
        by_wire = collections.defaultdict(list)
        for ref, _, pins in symbols:
            for p in pins:
                if p in pts or any(p in s[2] for s in symbols if s[0] != ref):   # on a wire's end, or pin to pin
                    by_wire[find(p)].append(ref)
                else:
                    for a, b in wires:                             # or on a wire's run (a capacitor hung from a bus)
                        if on_run(p, a, b):
                            union(p, a); by_wire[find(p)].append(ref); break
        box_of = {ref: box for ref, box, _ in symbols}
        def body_gap(a, b):
            ba, bb = box_of[a], box_of[b]
            return max(0.0, max(bb[0] - ba[2], ba[0] - bb[2]) + gap, max(bb[1] - ba[3], ba[1] - bb[3]) + gap)
        for refs in by_wire.values():
            for i, ra in enumerate(refs):
                for rb in refs[i + 1:]:
                    if reach is None or body_gap(ra, rb) <= reach:
                        sym_parent[sfind(ra)] = sfind(rb)
        for i, (ra, ba, _) in enumerate(symbols):
            for rb, bb, _ in symbols[i + 1:]:
                if ba[0] < bb[2] and ba[2] > bb[0] and ba[1] < bb[3] and ba[3] > bb[1]:
                    sym_parent[sfind(ra)] = sfind(rb)
        groups = collections.defaultdict(list)
        for ref, box, _ in symbols:
            groups[sfind(ref)].append((ref, box))
        sheet = os.path.basename(f)
        for members in sorted(groups.values(), key=lambda m: natural(min((r for r, _ in m), key=natural))):
            refs = sorted((r for r, _ in members), key=natural)
            iid = f"{sheet.rsplit('.', 1)[0]}/{refs[0]}"
            islands[iid] = (sheet, refs, (min(b[0] for _, b in members) + gap / 2, min(b[1] for _, b in members) + gap / 2,
                                          max(b[2] for _, b in members) - gap / 2, max(b[3] for _, b in members) - gap / 2))
            for r in refs:
                ref_island[r] = iid
    return ref_island, islands


def rot_side(side, deg):
    """A side of a footprint's own frame (as the library draws it) seen on the board after its rotation."""
    order = "RTLB"                                                 # a positive (counter-clockwise) quarter turn takes R to T, T to L, ...
    return order[(order.index(side) + int(round(deg / 90.0))) % 4]


def sch_pin_names():
    """{ref: {pad number: pin name}} from the sheets' library symbols, for the regulators' SW and VIN pins."""
    names = {}
    for f in sorted(glob.glob(os.path.join(OUT, "*.kicad_sch"))):
        doc = sexp(open(f, encoding="utf-8").read())[0]
        libs = {}
        for node in doc:
            if isinstance(node, list) and node and node[0] == "lib_symbols":
                for sym in node[1:]:
                    if not (isinstance(sym, list) and sym and sym[0] == "symbol"):
                        continue
                    pins = {}
                    def walk(n):
                        for item in n:
                            if isinstance(item, list) and item:
                                if item[0] == "pin":
                                    nm = next((i[1] for i in item if isinstance(i, list) and i and i[0] == "name"), None)
                                    num = next((i[1] for i in item if isinstance(i, list) and i and i[0] == "number"), None)
                                    if nm is not None and num is not None:
                                        pins[str(num)] = str(nm)
                                elif item[0] != "property":
                                    walk(item)
                    walk(sym); libs[sym[1]] = pins
        for node in doc:
            if not (isinstance(node, list) and node and node[0] == "symbol"):
                continue
            props = {i[1]: i[2] for i in node if isinstance(i, list) and i and i[0] == "property" and len(i) >= 3}
            ref = props.get("Reference", "")
            lib_id = next((i[1] for i in node if isinstance(i, list) and i and i[0] == "lib_id"), None)
            if ref and not ref.startswith("#") and lib_id in libs:
                names.setdefault(ref, {}).update(libs[lib_id])
    return names


def load_footprint(fpid):
    lib, name = fpid.split(":")
    for base in (HOUSE_FP, KICAD_FP):
        if not base:
            continue
        path = os.path.join(base, lib + ".pretty")
        if os.path.exists(os.path.join(path, name + ".kicad_mod")):
            fp = pcbnew.FootprintLoad(path, name)
            if fp is not None:
                return fp
    raise FileNotFoundError(fpid)


def bbox_mm(fp, courtyard=True):
    """Courtyard box (or the body+pads box) of a footprint as (x0, y0, x1, y1) in mm, where it is now, on its side."""
    cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    bb = cy.BBox() if (courtyard and cy.OutlineCount()) else fp.GetBoundingBox(False, False)
    return (bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6)


def overlap(a, b, margin=0.0):
    return a[0] < b[2] + margin and a[2] > b[0] - margin and a[1] < b[3] + margin and a[3] > b[1] - margin


def inside(b, c):
    return b[0] >= c[0] and b[2] <= c[2] and b[1] >= c[1] and b[3] <= c[3]


def rot_vec(v, deg):
    """A vector rotated as KiCad rotates footprints (counter-clockwise on the screen, Y down)."""
    c, s = round(math.cos(math.radians(deg))), round(math.sin(math.radians(deg)))
    return (v[0] * c + v[1] * s, -v[0] * s + v[1] * c)


def rect_outline(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def natural(ref):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", ref)]


def canonical(t):
    """pcbnew writes items in the order of the random UUIDs it draws for them. Sort the footprints by
    reference, the graphics and tracks by content and the zones by name, then derive every UUID in
    document order, so two generations of one design are byte-identical (ecad-standards/kicad-generation.md)."""
    def sort_run(kinds, key):
        nonlocal t
        pat = re.compile(r"\n\t\((?:" + kinds + r")\b.*?\n\t\)(?=\n)", re.S)
        found = list(pat.finditer(t))
        if not found:
            return
        assert all(found[i].end() == found[i + 1].start() for i in range(len(found) - 1)), kinds
        t = t[:found[0].start()] + "".join(sorted((m.group(0) for m in found), key=key)) + t[found[-1].end():]
    strip = lambda b: re.sub(r'\(uuid "[0-9a-f-]{36}"\)', "", b)
    sort_run("footprint", lambda b: natural(re.search(r'\(property "Reference" "([^"]+)"', b).group(1)))
    sort_run("gr_line|gr_arc|gr_rect|gr_circle|gr_poly", strip)
    sort_run("segment|arc|via", strip)
    sort_run("zone", lambda b: (re.search(r'\(name "([^"]*)"', b) or re.search(r"\(layer", b)).group(0))
    ns = uuid.uuid5(uuid.NAMESPACE_URL, f"kicad:{PROJECT}:pcb")
    n = itertools.count()
    return re.sub(r'\(uuid "[0-9a-f-]{36}"\)', lambda m: f'(uuid "{uuid.uuid5(ns, str(next(n)))}")', t)


def prefix(ref):
    return re.match(r"[A-Z]+", ref).group(0)


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; l = math.hypot(dx, dy) or 1.0
    return (dx / l, dy / l)


def nplus(d):
    """The '+' side of a direction: sign(cross(d, q)) > 0 for q on this side (y down on screen)."""
    return (-d[1], d[0])


def cross(d, q):
    return d[0] * q[1] - d[1] * q[0]


def offset_polyline(pts, offs):
    """Offset a polyline by a per-vertex distance along its '+' normal (mitred joins)."""
    out = []
    for i, p in enumerate(pts):
        if len(pts) == 1:
            return [p]
        d1 = unit(pts[i - 1], p) if i > 0 else unit(p, pts[i + 1])
        d2 = unit(p, pts[i + 1]) if i < len(pts) - 1 else d1
        n1, n2 = nplus(d1), nplus(d2)
        dot = 1 + n1[0] * n2[0] + n1[1] * n2[1]
        if dot < 0.3:                                                # a reversal: no mitre
            n = n1; k = 1.0
        else:
            n = (n1[0] + n2[0], n1[1] + n2[1]); k = 1.0 / dot
        out.append((p[0] + offs[i] * n[0] * k, p[1] + offs[i] * n[1] * k))
    return out


def chamfer(pts, c):
    """Replace each 90-degree corner of an axis-aligned polyline by a 45-degree cut c long."""
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, p, b = pts[i - 1], pts[i], pts[i + 1]
        la, lb = math.hypot(p[0] - a[0], p[1] - a[1]), math.hypot(b[0] - p[0], b[1] - p[1])
        cc = min(c, la / 2, lb / 2)
        if cc < 0.2:
            out.append(p); continue
        da, db = unit(a, p), unit(p, b)
        out.append((p[0] - da[0] * cc, p[1] - da[1] * cc)); out.append((p[0] + db[0] * cc, p[1] + db[1] * cc))
    out.append(pts[-1])
    return out


def kind(ref):
    p = prefix(ref)
    return "conn" if p == "J" else "ic" if p in ("U", "K") else "passive"


class Placer:
    """Places the footprints of a board by the rules in ecad-standards/layout.md section 3."""

    def __init__(self, board, fps, nets, classes, pad_net, schpos, geometry):
        self.board, self.fps, self.nets, self.pad_net, self.schpos = board, fps, nets, pad_net, schpos
        self.classes, self.geometry = classes, geometry
        self.pair_geometry, self.via_geometry = class_pairs_and_vias()
        self.weight = {n: (5.0 if c in L.CURRENT_CLASSES else 2.0 if c in L.PAIR_CLASSES else 1.0) for n, c in classes.items()}
        self.area = {ref: (lambda b: (b[2] - b[0]) * (b[3] - b[1]))(bbox_mm(fp)) for ref, fp in fps.items()}
        self.values = {ref: fp.GetValue() for ref, fp in fps.items()}
        self.pads_of = {ref: [(p.GetNumber(), pad_net.get((ref, p.GetNumber()))) for p in fp.Pads()] for ref, fp in fps.items()}
        self.plane = {n for n, nodes in nets.items() if len(nodes) > 8}
        self.loop = {n for n, nodes in nets.items() if any(prefix(r) == "L" for r, _ in nodes)}   # an inductor's nets: a switching loop
        self.boxes = {"F": {}, "B": {}}                   # side -> ref -> courtyard box of every placed part
        self.debug_shown = 0
        self.ring_side = {}                               # ref -> the side of its host it was placed on (L/R/T/B)
        self.host_of = {}                                 # ref -> its host
        self.side = {}
        self.under = {}                                   # a top part -> the boxes it denies the bottom (THT pads, EP via field, a crystal)
        self.lanes = []                                   # (name, box) corridors kept free of parts on both sides
        self.tracks = []                                  # (net, layer, width, p0, p1) the lanes' copper
        self.vias = []                                    # (net, x, y, diameter, drill) the lanes' vias
        self.lane_report = []                             # one line per lane: lengths, mismatch, crossings
        self.pairs_laid = []                              # (name, netP, netN, sP, first track index, last) for the length matching
        self.fixed = set()
        self.locked = []                                  # the connectors' boxes: nothing within 0.3 mm on top
        self.rings = collections.defaultdict(list)        # (host, side, layer) -> [{depth, members: [(ref, interval)]}]
        self.used_pins = collections.Counter()            # (host, rail) -> supply pins already given a capacitor
        self.order = []                                   # (ref, host, layer, how) in placement order
        self.parked = []
        self.debug = collections.Counter()
        s = L.HOLE_KEEPOUT
        self.corners = [(0 if hx < W / 2 else W - s, 0 if hy < H / 2 else H - s) for hx, hy in L.HOLES.values()]
        self.corners = [(x0, y0, x0 + s, y0 + s) for x0, y0 in self.corners]
        self.regions = list(L.ISOLATION_REGIONS)         # each: name, outline, rects, grown, nets (regex), classes, gap, island
        self.island, self.islands = sch_islands(L.ISLAND_GAP, L.ISLAND_REACH)   # layout.md 3.1: {ref: island}, {island: (sheet, refs, sheet bbox)}
        self.hub_of = {}                                  # island -> its hub: the member with the most pads, connectors aside
        self.city_of = {}                                 # ref -> city (layout.md 3.1): its island when that has two or more parts; a lone part joins its host's city when placed
        for iid, (_, refs, _) in self.islands.items():
            members = [r for r in refs if r in self.fps and kind(r) != "conn" and r not in L.HOLES]
            if members:
                self.hub_of[iid] = max(members, key=lambda r: (len(self.pads_of[r]), kind(r) == "ic", [-ord(c) for c in r]))
            if len(members) >= 2:
                for r in members:
                    self.city_of[r] = iid
        self.regulator_of = {self.island[r]: r for r in L.REGULATORS if r in self.island}   # island -> the regulator that is its hub
        self.reg_nets = {}                                # regulator -> {"sw": net, "in": net} from the schematic's pin names (or the directive's pin numbers)
        pin_names = sch_pin_names() if L.REGULATORS else {}
        for r, cfg in L.REGULATORS.items():
            if r not in self.fps:
                continue
            net_of_pad = dict(self.pads_of[r]); by_name = {}
            for num, nm in pin_names.get(r, {}).items():
                if num in net_of_pad:
                    by_name.setdefault(nm.upper(), net_of_pad[num])
            def pick(key, *defaults):
                want = str(cfg.get(key, "")).upper()
                if want and want in net_of_pad:                    # a pad number
                    return net_of_pad[want]
                return next((by_name[k] for k in ((want,) if want else ()) + defaults if k in by_name), None)
            self.reg_nets[r] = {"sw": pick("sw_pin", "SW", "LX", "PH", "SW1"), "in": pick("in_pin", "VIN", "PVIN", "IN", "VCC")}
            if not self.reg_nets[r]["sw"]:
                print(f"regulator {r}: no SW pin found (name it with 'sw_pin' in REGULATORS); pins by name: {sorted(by_name)}")
        self.pin_names = pin_names
        self.layout_of = {}                               # IC -> its datasheet or evaluation-board layout template (layout.md 3.2)
        for r, name in L.TEMPLATED.items():
            if r in self.fps and name in L.LAYOUTS:
                self.layout_of[r] = L.LAYOUTS[name]
        self.inductor_of = {}                             # regulator -> its inductor (the L on the SW net in its city)
        for r, cfg in L.REGULATORS.items():
            if r in self.fps and cfg.get("layout") in L.LAYOUTS:
                self.layout_of[r] = L.LAYOUTS[cfg["layout"]]
            sw = self.reg_nets.get(r, {}).get("sw")
            if r in self.fps and sw:
                self.inductor_of[r] = next((hr for hr, _ in self.nets[sw] if prefix(hr) == "L"), None)
        self.top_only = set()                             # cities whose template keeps every part on the top side
        for r, lay in self.layout_of.items():
            if lay.get("top_only") and r in self.city_of:
                self.top_only.add(self.city_of[r])

    def has_specific(self, ref):
        return any(n and n not in self.plane and not n.startswith("unconnected-") for _, n in self.pads_of[ref])

    def same_sheet(self, a, b):
        pa, pb = self.schpos.get(a), self.schpos.get(b)
        return bool(pa and pb and pa[0] == pb[0])

    def is_esd(self, ref):
        return self.values[ref].upper().startswith(L.ESD_VALUES)

    def bottom_ok(self, ref, host):
        """May this part go to the bottom (directives: small parts of the listed kinds, not LEDs, not ESD, not on a
        current-carrying, pair or switching-loop net, not a crystal's load capacitor)."""
        p = prefix(ref)
        if p not in L.BOTTOM_MAX_AREA or self.area[ref] > L.BOTTOM_MAX_AREA[p] or self.is_esd(ref):
            return False
        if self.values[ref].upper().startswith("LED"):
            return False
        for _, net in self.pads_of[ref]:
            if net and (self.classes.get(net) in L.BOTTOM_NEVER_CLASSES or net in self.loop):
                return False
        if host and not self.same_city(ref, host):               # another city's land (3.1): not under its host either
            return False
        if self.city_of.get(ref) in self.top_only:                # the datasheet's figure keeps the whole circuit on top (3.2)
            return False
        return not (host and prefix(host) == "Y")

    # ---- geometry helpers
    def box_of(self, ref):
        return self.boxes["F"].get(ref) or self.boxes["B"].get(ref)

    def flip_to(self, ref, layer):
        fp = self.fps[ref]
        if (layer == "B") != fp.IsFlipped():
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)

    def pose(self, ref, x, y, rot, layer="F"):
        fp = self.fps[ref]; self.flip_to(ref, layer)
        fp.SetOrientationDegrees(rot); fp.SetPosition(MM(x, y))
        for side in ("F", "B"):
            self.boxes[side].pop(ref, None)
        self.boxes[layer][ref] = bbox_mm(fp); self.side[ref] = layer
        self.under.pop(ref, None)
        if layer == "F":
            blocks = []
            for p in fp.Pads():
                x, y = p.GetPosition().x / 1e6, p.GetPosition().y / 1e6; sx, sy = p.GetSizeX() / 2e6, p.GetSizeY() / 2e6
                if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    m = L.THT_MARGIN; blocks.append((x - sx - m, y - sy - m, x + sx + m, y + sy + m))
                elif sx >= 0.75 and sy >= 0.75 and kind(ref) == "ic":
                    m = L.EP_MARGIN; blocks.append((x - sx - m, y - sy - m, x + sx + m, y + sy + m))
            if prefix(ref) == "Y":
                blocks.append(self.boxes["F"][ref])
            if blocks:
                self.under[ref] = blocks

    def origin_box(self, ref, rot, layer="F"):
        fp = self.fps[ref]; self.flip_to(ref, layer)
        fp.SetOrientationDegrees(rot); fp.SetPosition(MM(0, 0)); return bbox_mm(fp)

    def psu_part(self, ref):
        """The isolated region a part belongs to (its nets match the region's pattern), or None."""
        for region in self.regions:
            if any(n and re.search(region["nets"], n) for _, n in self.pads_of[ref]):
                return region
        return None

    def allowed(self, ref, b, layer="F", ignore=()):
        """May the part sit with its courtyard at b on that side: in the board's component area, out of the
        corner keep-outs and the lanes, on its side of the isolation barrier, clear of the connectors and of
        everything placed on that side (and, on the bottom, of through-hole pads and exposed-pad via fields)."""
        why = self.why_not(ref, b, layer, ignore)
        if why and ref == DEBUG_REF:
            self.debug[why] += 1
        return why is None

    def why_not(self, ref, b, layer, ignore=()):
        z = L.EDGE_ZONE
        if b[0] < z or b[1] < z or b[2] > W - z or b[3] > H - z:
            return "edge zone"
        if any(overlap(b, c) for c in self.corners):
            return "corner keep-out"
        if any(overlap(b, c) for _, c in self.lanes):
            return "a lane"
        mine = self.psu_part(ref)
        for region in self.regions:
            if region is mine:
                if not any(inside(b, c) for c in region["rects"]):
                    return f"outside {region['name']}"
            elif any(overlap(b, c) for c in region["grown"]):
                return region["name"]
        if layer == "F":
            conn_gap = max(0.3, mine["gap"]) if mine else 0.3          # a region's part keeps the creepage from the connectors outside it
            if any(overlap(b, c, conn_gap if not any(inside(c, r) for r in mine["rects"]) else 0.3) if mine else overlap(b, c, 0.3) for c in self.locked):
                return "a connector"
        else:
            for other, blocks in self.under.items():
                if other != ref and any(overlap(b, c) for c in blocks):
                    return f"under {other}"
        for other, ob in self.boxes[layer].items():              # the void between cities holds on each side: one channel may sit under another (3.7)
            if other != ref and other not in ignore and overlap(b, ob, self.margin(ref, other)):
                return other
        return None

    def exempt(self, ref):
        """Connectors, holes and ESD parts stand outside the cities (layout.md 3.1 and 3.8: ESD sits at its
        connector, in line with the pair, whatever island drew it): a part keeps the packing margin to them."""
        return kind(ref) == "conn" or ref in L.HOLES or (ref in self.values and self.is_esd(ref)) or ref not in self.city_of

    def same_city(self, a, b):
        if self.exempt(a) or self.exempt(b):
            return True
        ca, cb = self.city_of.get(a), self.city_of.get(b)
        return ca is not None and ca == cb

    def margin(self, a, b):
        ra, rb = self.psu_part(a), self.psu_part(b)
        if ra is not rb:                                               # one side of an isolation barrier to the other: the creepage (3.6)
            return max(L.CITY_GAP, (ra or rb)["gap"])
        return L.PACK_MARGIN if self.same_city(a, b) else L.CITY_GAP

    # ---- the fixed parts
    def place_fixed(self):
        for ref, (x, y) in L.HOLES.items():
            self.pose(ref, x, y, 0); self.fixed.add(ref)
        # a fixed part (x, y, rot[, side]) outranks its anchor: a hand placement harvested into FIXED wins over the directive
        # that first put the part somewhere, and a part fixed on the bottom goes there
        for table in (L.CONNECTORS, L.FIXED, L.ANCHORS):
            for ref, pose in table.items():
                if ref in self.fps and not (table is L.ANCHORS and ref in L.FIXED):
                    x, y, rot = pose[:3]; self.pose(ref, x, y, rot, pose[3] if len(pose) > 3 else "F"); self.fixed.add(ref)
        self.locked = [self.boxes["F"][ref] for ref in L.CONNECTORS if ref in self.fps]
        # the directives' own parts must be consistent; what the owner fixed by hand is taken as it is, its closeness to its
        # neighbours reported here and judged by the DRC gate (courtyards), not refused
        problems, hand = [], []
        refs = sorted(self.fixed)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if self.side[a] == self.side[b] and overlap(self.boxes[self.side[a]][a], self.boxes[self.side[b]][b], L.PACK_MARGIN):
                    (hand if (a in L.FIXED or b in L.FIXED) else problems).append(f"{a} and {b}")
        for ref in L.ANCHORS:
            if ref not in self.fps:
                problems.append(f"{ref} is anchored but not in the schematic"); continue
            if ref in L.FIXED:
                continue
            if not self.allowed(ref, self.boxes["F"][ref]):
                b = self.boxes["F"][ref]; why = self.why_not(ref, b, 'F')
                (hand if any(w in L.FIXED for w in re.split(r"[ ,]+", why)) else problems).append(f"{ref} at ({b[0]:.1f}-{b[2]:.1f}, {b[1]:.1f}-{b[3]:.1f}) is refused by: {why}")
        self.hand_notes = getattr(self, "hand_notes", [])            # what the hand-fixed parts put in each other's way: placement.txt
        if hand:
            self.hand_notes.append(f"hand-fixed parts closer than the packing margin to a neighbour ({len(hand)}; the DRC gate judges the courtyards): " + ", ".join(hand))
            print(self.hand_notes[-1])
        for ref, b in self.boxes["F"].items():
            if ref not in L.HOLES and ref not in L.FIXED and any(overlap(b, c) for c in self.corners):
                problems.append(f"{ref} stands in a corner keep-out")
        if problems:
            raise SystemExit("the directives' fixed parts are not consistent:\n  " + "\n  ".join(problems))

    # ---- lanes
    def net_named(self, short):
        for n in self.nets:
            if n == short or n.endswith("/" + short):
                return n
        raise KeyError(short)

    def pad_xy(self, ref, num):
        p = self.fps[ref].FindPadByNumber(num).GetPosition(); return (p.x / 1e6, p.y / 1e6)

    def pad_geom(self, ref, num):
        """(centre, half-length along the pad's long axis, that axis as a unit vector, is-through-hole) of a pad."""
        p = self.fps[ref].FindPadByNumber(num)
        if p is None:
            raise SystemExit(f"{ref} has no pad {num}")
        c = (p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
        sx, sy = p.GetSizeX() / 1e6, p.GetSizeY() / 1e6
        rot = math.radians(p.GetOrientationDegrees()) if hasattr(p, "GetOrientationDegrees") else math.radians(p.GetOrientation().AsDegrees())
        ax = (math.cos(rot), -math.sin(rot)) if sx >= sy else (math.sin(rot), math.cos(rot))
        return c, max(sx, sy) / 2, ax, p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)

    def pad_tip(self, ref, num, d):
        """Where a track leaves a pad travelling in direction d: the finger's end for an SMD pad, the centre for a hole."""
        c, half, ax, tht = self.pad_geom(ref, num)
        if tht:
            return c
        s = ax[0] * d[0] + ax[1] * d[1]
        if abs(s) < 0.5:                                               # leaving across the finger: from its centre
            return c
        k = half - 0.1 if s > 0 else -(half - 0.1)
        return (c[0] + ax[0] * k, c[1] + ax[1] * k)

    def resolve_lanes(self):
        """The lanes from the directives: corridors kept free of parts (stopping at the parts a lane joins), the
        tracks laid at the class width, a single-net lane's layer changes, and the differential pairs' two
        member tracks with their escapes, bridges, corners and the crossing check."""
        self.lane_problems, self.lane_crossing = [], []            # not laid; laid with the members crossing
        for name, lane in L.LANES.items():
            try:
                if "pair" in lane:
                    self.lay_pair(name, lane)
                else:
                    self.lay_single(name, lane)
            except SystemExit as e:                                 # a lane the engine cannot lay is reported and left to the router
                self.lane_problems.append(f"{name}: {e}"); print(f"LANE NOT LAID {name}: {e}")
        through = []                                                # a corridor over a locked part that is not hand-fixed (a connector)
        self.hand_notes = getattr(self, "hand_notes", [])
        for name, box in self.lanes:                                # nothing fixed but the lane's ends may stand in it
            ends = set()
            for lane in L.LANES.values():
                for item in lane["path"]:
                    if item[0] == "pads":
                        ends.update(r for r, _ in item[1].values())
                    elif isinstance(item[0], str) and item[0] not in ("x", "y", "layer") and item[0] in self.fps:
                        ends.add(item[0])
            for ref in self.fixed:                                  # on either side: a corridor keeps parts out of both
                if ref not in L.HOLES and ref not in ends and overlap(box, self.boxes[self.side[ref]][ref]):
                    if ref in L.FIXED:                              # a hand-fixed part in a corridor is reported; the lane is laid and DRC judges
                        self.hand_notes.append(f"lane {name} runs through the hand-fixed {ref}"); print(self.hand_notes[-1])
                    else:
                        through.append(f"lane {name} runs through {ref}"); print(through[-1])
        crossing = [f"lane {n}: the P and N pads are on opposite sides at its two ends: swap the array's channels in the schematic, "
                    "or lead the path in from the other side" for n in self.lane_crossing]
        if (self.lane_problems or through or crossing) and not L.FIXED:   # with nothing hand-fixed the directives themselves are at fault
            raise SystemExit("the directives' lanes are not consistent:\n  " + "\n  ".join(self.lane_problems + through + crossing))
        if crossing:
            self.hand_notes.append(f"lanes laid with their members crossing ({len(crossing)}; the DRC gate faults the crossing): " + "; ".join(crossing)); print(self.hand_notes[-1])
        if self.lane_problems:
            self.hand_notes.append(f"lanes not laid ({len(self.lane_problems)}; the router routes those pairs, and the pair sweep spares them): "
                                   + "; ".join(self.lane_problems)); print(self.hand_notes[-1])
        if through:
            self.hand_notes.append(f"lanes laid through a locked part ({len(through)}; the DRC gate judges them): " + "; ".join(through)); print(self.hand_notes[-1])
        self.match_lengths()

    def match_lengths(self):
        """Every pair's members matched within the tolerance: a bump on the shorter member's longest straight run,
        on its outer side, where it stands clear of every lane and fixed part (layout.md 5)."""
        for name, netP, netN, sP, i0, i1 in self.pairs_laid:
            mine = [(i, tr) for i, tr in enumerate(self.tracks) if i0 <= i < i1]
            def length(net):
                return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for i, (n, l, ww, a, b) in mine if n == net)
            delta = length(netP) - length(netN)
            note = ""
            if abs(delta) > L.MATCH_TOLERANCE:
                short, sign = (netN, -sP) if delta > 0 else (netP, sP)
                cands = [(i, tr) for i, tr in mine if tr[0] == short and (abs(tr[3][0] - tr[4][0]) < 1e-6 or abs(tr[3][1] - tr[4][1]) < 1e-6)
                         and math.hypot(tr[4][0] - tr[3][0], tr[4][1] - tr[3][1]) > L.BUMP_WIDTH + 2.0]
                others = [bx for nm, bx in self.lanes if not nm.startswith(name)] + [bx for r, bx in self.boxes["F"].items() if r in self.fixed]
                placed = False
                for i, (net, lay, ww, a, b) in sorted(cands, key=lambda it: -math.hypot(it[1][4][0] - it[1][3][0], it[1][4][1] - it[1][3][1])):
                    d = unit(a, b); n = nplus(d); h = min(abs(delta) / 2, L.BUMP_HEIGHT); s = L.BUMP_WIDTH
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    p1 = (mid[0] - d[0] * s / 2, mid[1] - d[1] * s / 2); p2 = (mid[0] + d[0] * s / 2, mid[1] + d[1] * s / 2)
                    q1 = (p1[0] + n[0] * h * sign, p1[1] + n[1] * h * sign); q2 = (p2[0] + n[0] * h * sign, p2[1] + n[1] * h * sign)
                    bb = (min(q1[0], q2[0], p1[0], p2[0]) - ww, min(q1[1], q2[1], p1[1], p2[1]) - ww, max(q1[0], q2[0], p1[0], p2[0]) + ww, max(q1[1], q2[1], p1[1], p2[1]) + ww)
                    if any(overlap(bb, o, 0.2) for o in others) or bb[0] < L.EDGE_ZONE or bb[1] < L.EDGE_ZONE or bb[2] > W - L.EDGE_ZONE or bb[3] > H - L.EDGE_ZONE:
                        continue
                    self.tracks[i:i + 1] = [(net, lay, ww, a, p1), (net, lay, ww, p1, q1), (net, lay, ww, q1, q2), (net, lay, ww, q2, p2), (net, lay, ww, p2, b)]
                    self.lanes.append((name + "_bump", bb)); placed = True
                    for j, (nm, nP, nN, sg, a0, a1) in enumerate(self.pairs_laid):   # the indices after the bump shift by four
                        if a0 > i: self.pairs_laid[j] = (nm, nP, nN, sg, a0 + 4, a1 + 4)
                        elif a1 > i: self.pairs_laid[j] = (nm, nP, nN, sg, a0, a1 + 4)
                    mine = [(i, tr) for i, tr in enumerate(self.tracks) if i0 <= i < i1 + 4]
                    break
                if not placed:
                    note = f", no room for a {abs(delta):.2f} mm bump"
            lp, ln = length(netP), length(netN)
            changes, crossing = self.lane_meta.get(name, (0, False))
            self.lane_report.append(f"{name}: P {lp:.1f} mm, N {ln:.1f} mm, mismatch {abs(lp - ln):.2f} mm, {changes} layer change(s)" + note + (" CROSSING" if crossing else ""))

    def class_of(self, net):
        track, clear = self.geometry.get(self.classes.get(net, "Default"), self.geometry["Default"])
        return track, clear

    def rail_vias(self):
        """Every SMD pad on a net that has a plane or rail polygon under it gets that net's vias beside it, the
        class's count (RAIL_VIAS), joined to the pad by a stub at the class width (the pad's narrower side where that is
        less), so the current reaches the
        copper that carries it and the router routes nothing for it (layout.md 4). Through-hole pads reach the
        planes by themselves. Returns (vias placed, pads served, pads with no room)."""
        polys = collections.defaultdict(list)                          # net -> [outline] of its planes and islands
        covers = []                                                    # (net, layer, outline, priority) of every plane and island
        for plane in L.PLANES:
            polys[plane[1]].append(plane[3]); covers.append((plane[1], plane[2], plane[3], plane[4] if len(plane) > 4 else 0))
        for region in self.regions:
            for isl in region_islands(region):
                polys[isl[1]].append(isl[3]); covers.append((isl[1], isl[2], isl[3], isl[4] if len(isl) > 4 else 0))
        if not polys:
            return 0, 0, [], []
        def inside(pt, outline):
            x, y = pt; n = len(outline); ok = False
            for i in range(n):
                (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % n]
                if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                    ok = not ok
            return ok
        pad_boxes = []                                                 # (ref, net, layer, box) of every pad, both sides
        for ref, fp in self.fps.items():
            for p in fp.Pads():
                bb = p.GetBoundingBox()
                pad_boxes.append((ref, self.pad_net.get((ref, p.GetNumber())), "B" if fp.IsFlipped() else "F", p.GetDrillSize().x > 0,
                                  (bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6)))
        vias_here = [(x, y, dia / 2) for _, x, y, dia, _ in self.vias]
        lane_segs = [(a, b, w / 2) for _, _, w, a, b in self.tracks]
        def seg_dist(p, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]; l2 = dx * dx + dy * dy
            if l2 == 0: return math.hypot(p[0] - a[0], p[1] - a[1])
            u = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
            return math.hypot(p[0] - a[0] - u * dx, p[1] - a[1] - u * dy)
        def via_ok(net, ref, x, y, r):
            z = L.EDGE_ZONE
            if x - r < z or y - r < z or x + r > W - z or y + r > H - z: return False
            box = (x - r, y - r, x + r, y + r)
            if any(overlap(box, c) for c in self.corners): return False
            if any(overlap(box, c, 0.1) for _, c in self.lanes): return False
            for a, b, cc, d in getattr(L, "COPPER_VOIDS", {}).values():
                if overlap(box, (a, b, cc, d)): return False
            mine = self.psu_part(ref)
            for region in self.regions:
                if region is not mine and any(overlap(box, c) for c in region["grown"]): return False
                if region is mine and not any(inside((x, y), [(c[0], c[1]), (c[2], c[1]), (c[2], c[3]), (c[0], c[3])]) for c in region["rects"]): return False
            for pref, pnet, _, tht, pb in pad_boxes:
                if pnet == net and pref == ref: continue
                if overlap(box, pb, 0.5 if pnet != net else 0.2): return False   # 0.5 keeps a solder-mask web to another net's pad
            if any(math.hypot(x - vx, y - vy) < r + vr + 0.3 for vx, vy, vr in vias_here): return False
            if any(seg_dist((x, y), a, b) < r + hw + 0.3 for a, b, hw in lane_segs): return False
            # inside one of the net's own planes, and on that layer the net's plane is the one on top: a higher-priority
            # rectangle of another net carves the base rail out there, and a via in the carved copper reaches nothing
            for cnet, clayer, outline, prio in covers:
                if cnet == net and inside((x, y), outline):
                    top = max((p2 for n2, l2, o2, p2 in covers if l2 == clayer and n2 != net and inside((x, y), o2)), default=None)
                    if top is None or top < prio:
                        return True
            return False
        def stub_ok(net, ref, a, b, hw):
            """The stub from the pad centre (or the previous via) to the via crosses no other pad, via or lane track
            (sampled every 0.1 mm); the vias at its own ends are its joints, not obstacles."""
            n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.1) + 1)
            for k in range(n + 1):
                x, y = a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
                box = (x - hw, y - hw, x + hw, y + hw)
                for pref, pnet, _, tht, pb in pad_boxes:
                    if pnet == net: continue                           # a stub across a pad of its own net is a joint, not a short
                    if overlap(box, pb, 0.3): return False
                if any(math.hypot(x - vx, y - vy) < hw + vr + 0.3 for vx, vy, vr in vias_here
                       if math.hypot(vx - a[0], vy - a[1]) > 1e-6 and math.hypot(vx - b[0], vy - b[1]) > 1e-6): return False
                if any(seg_dist((x, y), sa, sb) < hw + shw + 0.3 for sa, sb, shw in lane_segs): return False
            return True
        counts = getattr(L, "RAIL_VIAS", {})
        placed, served, no_room, short = 0, 0, [], []
        for ref, fp in self.fps.items():
            if ref in L.HOLES: continue
            side = "B" if fp.IsFlipped() else "F"
            fx, fy = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
            for p in fp.Pads():
                net = self.pad_net.get((ref, p.GetNumber()))
                if not net or net not in polys or p.GetDrillSize().x > 0: continue
                px, py = p.GetPosition().x / 1e6, p.GetPosition().y / 1e6
                if not any(inside((px, py), o) for o in polys[net]): continue
                cls = self.classes.get(net, "Default"); want = counts.get(cls, 1)
                cdia, cdrill = self.via_geometry.get(cls, self.via_geometry.get("Default", (0.6, 0.3)))
                ddia, ddrill = self.via_geometry.get("Default", (0.6, 0.3))
                half = max(p.GetSize().x, p.GetSize().y) / 2e6
                track, _ = self.class_of(net); sw = min(track, min(p.GetSize().x, p.GetSize().y) / 1e6)
                dirs = sorted([(1, 0), (-1, 0), (0, 1), (0, -1), (0.707, 0.707), (-0.707, 0.707), (0.707, -0.707), (-0.707, -0.707)],
                              key=lambda d: -((px - fx) * d[0] + (py - fy) * d[1]))   # outward from the part first
                def chain(n, dia):
                    """n vias of this diameter in a straight run beside the pad, in the first direction that takes them all."""
                    r = dia / 2
                    for dx, dy in dirs:
                        got = []; prev = (px, py)
                        for k in range(n):
                            step = None
                            for dist in [half + r + 0.35 + (k * (dia + 0.35)) + j * 0.25 for j in range(10)]:
                                vx, vy = px + dx * dist, py + dy * dist
                                if via_ok(net, ref, vx, vy, r) and stub_ok(net, ref, prev, (vx, vy), sw / 2):
                                    step = (vx, vy); break
                            if step is None: break
                            got.append(step); vias_here.append((step[0], step[1], r)); prev = step
                        if len(got) == n: return got
                        for g in got: vias_here.remove((g[0], g[1], r))
                    return []
                # the class's count of the class's via; failing that fewer of them, down to one; failing that the default via,
                # the class's count down to one: the rail is reached by whatever fits and the shortfall is reported
                tries = [(n, cdia, cdrill) for n in range(want, 0, -1)]
                if (ddia, ddrill) != (cdia, cdrill):
                    tries += [(n, ddia, ddrill) for n in range(want, 0, -1)]
                got, used = [], None
                for n, dia, drill in tries:
                    got = chain(n, dia)
                    if got: used = (n, dia, drill); break
                if not got:
                    no_room.append(f"{ref}.{p.GetNumber()}"); continue
                if used != (want, cdia, cdrill):
                    short.append(f"{ref}.{p.GetNumber()} {used[0]}x{used[1]:g} for {want}x{cdia:g}")
                layer = "F.Cu" if side == "F" else "B.Cu"
                prev = (px, py)
                for g in got:
                    self.tracks.append((net, layer, sw, prev, g)); lane_segs.append((prev, g, sw / 2)); prev = g
                    self.add_via(net, g, (used[1], used[2])); placed += 1
                served += 1
        return placed, served, no_room, short

    def corridor(self, name, a, b, hw):
        """A leg's keep-out box, clipped where it enters the courtyard of a part the lane joins (handled by the caller)."""
        box = (min(a[0], b[0]) - hw, min(a[1], b[1]) - hw, max(a[0], b[0]) + hw, max(a[1], b[1]) + hw)
        self.lanes.append((name, box))

    def walk(self, lane, ends_pads):
        """The centreline of a lane from its path items: points, the layer of each leg, and the end directions.
        ends_pads: callable(item) -> the point an end item stands for."""
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
        return pts, layers, switches                           # a diagonal leg is allowed: its corridor is its bounding box

    def lay_single(self, name, lane):
        net = self.net_named(lane["net"])
        track, clear = self.class_of(net)
        width = lane.get("width", track)
        hw = width / 2 + clear + L.LANE_MARGIN
        def end_pt(item):
            if self.pad_net.get(item) != net:
                raise SystemExit(f"lane {name}: pad {item} is not on {net}")
            return self.pad_geom(*item)[0]
        pts, layers, switches = self.walk(lane, end_pt)
        ends = [item[0] for item in lane["path"] if item[0] not in ("x", "y", "layer")]
        layer = lane.get("layer", "F.Cu")
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:])):
            for k, old, new in switches:
                if k == i:
                    layer = new; self.add_via(net, (x0, y0))
            self.tracks.append((net, layer, width, (x0, y0), (x1, y1)))
            self.add_corridor(name, (x0, y0), (x1, y1), hw, ends)
        self.lane_report.append(f"{name}: {net.split('/')[-1]} {sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(pts, pts[1:])):.1f} mm, {len(switches)} layer change(s)")

    def add_corridor(self, name, a, b, hw, ends):
        box = [min(a[0], b[0]) - hw, min(a[1], b[1]) - hw, max(a[0], b[0]) + hw, max(a[1], b[1]) + hw]
        horizontal = abs(a[1] - b[1]) < 1e-6
        for ref in ends:
            cb = self.box_of(ref)
            if not cb or not overlap(tuple(box), cb):
                continue
            lo, hi = (0, 2) if horizontal else (1, 3)
            if cb[lo] <= box[lo] and cb[hi] >= box[hi]:
                return
            if cb[lo] <= box[lo]:
                box[lo] = cb[hi]
            elif cb[hi] >= box[hi]:
                box[hi] = cb[lo]
            else:                                                      # the part stands inside the leg's span: keep the far side
                end_near_hi = abs(b[lo // 2] - cb[hi]) < abs(b[lo // 2] - cb[lo]) if False else ((b[0] if horizontal else b[1]) >= (cb[lo] + cb[hi]) / 2)
                if end_near_hi:
                    box[hi] = cb[lo]
                else:
                    box[lo] = cb[hi]
        if box[2] - box[0] > 0.1 and box[3] - box[1] > 0.1:
            self.lanes.append((name, tuple(box)))

    def add_via(self, net, p, size=None):
        """A via of the net's class (or of `size`, a (diameter, drill) pair: a rail via that fell back to a smaller one)."""
        track, clear = self.class_of(net)
        cls = self.classes.get(net, "Default")
        dia, drill = size or self.via_geometry.get(cls, self.via_geometry.get("Default", (0.6, 0.3)))
        self.vias.append((net, p[0], p[1], dia, drill))
        return dia

    def lay_pair(self, name, lane):
        """A differential pair along the lane: the two member tracks at the class width and gap, escapes from the
        pads at the pads' own pitch, bridges for a receptacle's doubled pads, one optional layer change, and the
        check that the P side is the same at both ends (else the schematic swaps the array's channels)."""
        base = lane["pair"]
        netP, netN = self.net_named(base + "_P"), self.net_named(base + "_N")
        track, clear = self.class_of(netP)
        gap = self.pair_geometry.get(self.classes.get(netP, "Default"), (track, clear))[1]
        w = self.pair_geometry.get(self.classes.get(netP, "Default"), (track, clear))[0]
        off = (w + gap) / 2
        hw = off + w / 2 + clear + L.LANE_MARGIN
        esc_w = min(w, L.ESCAPE_WIDTH)
        items = lane["path"]
        end_items = [it for it in items if it[0] not in ("x", "y", "layer")]
        if len(end_items) != 2:
            raise SystemExit(f"lane {name}: a pair lane has exactly two ends")
        laid_from = len(self.tracks)                                   # the member lengths count every track laid from here: stubs, escapes, legs
        # each end: the P and N pads (a list means doubled pads: members chosen below, the rest bridged)
        def pads_of_end(item):
            """-> {side: [(ref, pad), ...]}: an end is (ref, {"P": pad(s), "N": pad(s)}) or ("pads", {"P": (ref, pad), "N": (ref, pad)})."""
            ref, spec = item[0], item[1]
            out = {}
            for side in ("P", "N"):
                v = spec[side]
                if ref == "pads":
                    pairs = [tuple(v)]
                else:
                    pairs = [(ref, n) for n in (v if isinstance(v, (list, tuple)) else [v])]
                for r, n in pairs:
                    want = netP if side == "P" else netN
                    if self.pad_net.get((r, n)) != want:
                        raise SystemExit(f"lane {name}: {r} pad {n} is not on {want}")
                out[side] = pairs
            return out
        def end_mid(item):
            spec = pads_of_end(item)
            cs = [self.pad_geom(r, n)[0] for side in ("P", "N") for r, n in spec[side]]
            shift = item[2] if len(item) > 2 else (0.0, 0.0)
            return (sum(c[0] for c in cs) / len(cs) + shift[0], sum(c[1] for c in cs) / len(cs) + shift[1])
        pts, layers, switches = self.walk(lane, end_mid)
        if len(pts) < 2:
            raise SystemExit(f"lane {name}: needs at least one leg")
        d0 = unit(pts[0], pts[1]); d1 = unit(pts[-2], pts[-1])
        # members at each end, and the P side at the start
        spec0 = pads_of_end(end_items[0]); spec1 = pads_of_end(end_items[1])
        ref0 = end_items[0][0] if end_items[0][0] != "pads" else None; ref1 = end_items[1][0] if end_items[1][0] != "pads" else None
        def side_of(m, d):                                            # the P member's side of the members' own midpoint
            mx, my = (m["P"][0] + m["N"][0]) / 2, (m["P"][1] + m["N"][1]) / 2
            return 1 if cross(d, (m["P"][0] - mx, m["P"][1] - my)) >= 0 else -1
        doubled0 = len(spec0["P"]) > 1; doubled1 = len(spec1["P"]) > 1
        if doubled0 and not doubled1:                                  # a receptacle's members follow the fixed end
            m1, bridges1 = self.choose_members(spec1, (-d1[0], -d1[1]), pts[-1], None, netP, netN)
            m0, bridges0 = self.choose_members(spec0, d0, pts[0], side_of(m1, d1), netP, netN)
        else:
            m0, bridges0 = self.choose_members(spec0, d0, pts[0], None, netP, netN)
            m1, bridges1 = self.choose_members(spec1, (-d1[0], -d1[1]), pts[-1], side_of(m0, d0), netP, netN)
        sP = side_of(m0, d0)
        s1 = side_of(m1, d1)
        crossing = (s1 != sP)
        # the body: from the escape point after the first end to the one before the last, chamfered, offset
        E = L.ESCAPE_LENGTH
        def depth_fix(m, d, p0, layer):
            """Escape geometry at one end: the tips to escape from (after any straight stub to a common depth) and the
            depth the body starts at, measured from the centreline point p0 along d."""
            tips = {}
            depths = {}
            for side in ("P", "N"):
                tip = self.pad_tip(*m[side + "pad"], d); tips[side] = tip
                depths[side] = (tip[0] - p0[0]) * d[0] + (tip[1] - p0[1]) * d[1]
            deep = max(depths.values())
            tht = any(self.pad_geom(*m[side + "pad"])[3] for side in ("P", "N"))
            if tht:
                deep += L.THT_STUB                                     # a row of holes: run straight past the row's other pins first
            for side in ("P", "N"):
                if deep - depths[side] > 0.3:                          # a stub straight along d to the common depth
                    tip = tips[side]; far = (tip[0] + d[0] * (deep - depths[side]), tip[1] + d[1] * (deep - depths[side]))
                    self.tracks.append((netP if side == "P" else netN, layer, esc_w, tip, far)); tips[side] = far
            return tips, deep + E
        tips0, dep0 = depth_fix(m0, d0, pts[0], layers[0]); tips1, dep1 = depth_fix(m1, (-d1[0], -d1[1]), pts[-1], layers[-1])
        leg0 = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]); leg1 = math.hypot(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        body = [(pts[0][0] + d0[0] * dep0, pts[0][1] + d0[1] * dep0)] + pts[1:-1] + [(pts[-1][0] - d1[0] * dep1, pts[-1][1] - d1[1] * dep1)]
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        if total < dep0 + dep1 + 1.0:                                 # too short for a body: pad to pad at the escape width
            for side, net in (("P", netP), ("N", netN)):
                a = tips0[side]; b = tips1[side]
                a2 = (a[0] + d0[0] * L.DIRECT_STUB, a[1] + d0[1] * L.DIRECT_STUB); b2 = (b[0] - d1[0] * L.DIRECT_STUB, b[1] - d1[1] * L.DIRECT_STUB)
                self.tracks.append((net, layers[0], esc_w, a, a2)); self.tracks.append((net, layers[0], esc_w, a2, b2)); self.tracks.append((net, layers[0], esc_w, b2, b))
            self.bridge(name, bridges0, netP, netN, d0, layers[0]); self.bridge(name, bridges1, netP, netN, (-d1[0], -d1[1]), layers[-1])
            # a direct lane is too short for a bump: its members' lengths (pad to pad, the bridges aside) are reported with
            # the mismatch all the same, as the standard's step 5 says
            def member(side):
                a = tips0[side]; b = tips1[side]
                a2 = (a[0] + d0[0] * L.DIRECT_STUB, a[1] + d0[1] * L.DIRECT_STUB); b2 = (b[0] - d1[0] * L.DIRECT_STUB, b[1] - d1[1] * L.DIRECT_STUB)
                return sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in ((a, a2), (a2, b2), (b2, b)))
            lp, ln = member("P"), member("N")
            self.lane_report.append(f"{name}: direct {total:.1f} mm, P {lp:.2f} mm, N {ln:.2f} mm, mismatch {abs(lp - ln):.2f} mm"
                                    + (", no room for a bump" if abs(lp - ln) > L.MATCH_TOLERANCE else "") + (" CROSSING" if crossing else ""))
            if crossing:
                self.lane_crossing.append(name)
            return
        change_at = {k: new for k, old, new in switches}             # body index -> new layer (body[i] is pts[i] for inner points)
        VIA = L.VIA_PAIR_OFFSET
        chunks, cur_pts, cur_offs, cur_fix = [], [], [], {}           # per layer: points, offsets, and vertices pinned to via points
        layer = lane.get("layer", "F.Cu")
        via_pts = []                                                   # (net sign, point) per change
        for i, p in enumerate(body):
            if i in change_at and 0 < i < len(body) - 1:
                dprev = unit(body[i - 1], p); dnext = unit(p, body[i + 1])
                n1, n2 = nplus(dprev), nplus(dnext); dot = 1 + n1[0] * n2[0] + n1[1] * n2[1]
                nm = ((n1[0] + n2[0]) / dot, (n1[1] + n2[1]) / dot)   # the corner's mitre normal (the normal itself on a straight run)
                vp = {+1: (p[0] + VIA * nm[0], p[1] + VIA * nm[1]), -1: (p[0] - VIA * nm[0], p[1] - VIA * nm[1])}
                via_pts.append(vp)
                if math.hypot(p[0] - body[i - 1][0], p[1] - body[i - 1][1]) > 1.3:
                    cur_pts.append((p[0] - dprev[0] * 1.0, p[1] - dprev[1] * 1.0)); cur_offs.append(off)
                cur_pts.append(p); cur_offs.append(VIA); cur_fix[len(cur_pts) - 1] = vp
                chunks.append((cur_pts, cur_offs, layer, cur_fix))
                layer = change_at[i]
                cur_pts, cur_offs, cur_fix = [p], [VIA], {0: vp}
                if math.hypot(body[i + 1][0] - p[0], body[i + 1][1] - p[1]) > 1.3:
                    cur_pts.append((p[0] + dnext[0] * 1.0, p[1] + dnext[1] * 1.0)); cur_offs.append(off)
            else:
                cur_pts.append(p); cur_offs.append(off)
        chunks.append((cur_pts, cur_offs, layer, cur_fix))
        tracks_out = []
        def emit_member(sign, net):
            first, last = None, None
            for cpts, coffs, clay, cfix in chunks:
                if not cfix:                                           # a plain chunk: chamfered corners, one offset
                    opts = chamfer(cpts, L.CHAMFER); ooffs = [off] * len(opts)
                    offp = offset_polyline(opts, [o * sign for o in ooffs])
                else:
                    offp = offset_polyline(cpts, [o * sign for o in coffs])
                    for k, vp in cfix.items():
                        offp[k] = vp[sign]
                for a, b in zip(offp, offp[1:]):
                    if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
                        tracks_out.append((net, clay, w, a, b))
                first = offp[0] if first is None else first; last = offp[-1]
            return first, last
        fP, lP = emit_member(sP, netP); fN, lN = emit_member(-sP, netN)
        for vp in via_pts:                                             # the via pair at each layer change
            self.add_via(netP, vp[sP]); self.add_via(netN, vp[-sP])
        offP = [fP, lP]; offN = [fN, lN]
        # escapes: pad tip to the first/last member points
        for side, net, offp in (("P", netP, offP), ("N", netN, offN)):
            self.tracks.append((net, layers[0], esc_w, tips0[side], offp[0]))
            self.tracks.append((net, layers[-1], esc_w, offp[-1], tips1[side]))
        self.tracks += tracks_out
        self.pairs_laid.append((name, netP, netN, sP, laid_from, len(self.tracks)))   # the bridges below are not signal path
        self.bridge(name, bridges0, netP, netN, d0, layers[0]); self.bridge(name, bridges1, netP, netN, (-d1[0], -d1[1]), layers[-1])
        # corridors along the centreline legs
        ends = [r for spec in (spec0, spec1) for side in ("P", "N") for r, _ in spec[side]]
        for a, b in zip(pts, pts[1:]):
            self.add_corridor(name, a, b, hw, ends)
        self.lane_meta = getattr(self, "lane_meta", {}); self.lane_meta[name] = (len(switches), crossing)
        if crossing:
            self.lane_crossing.append(name)

    def choose_members(self, spec, d, mid, want_sign, netP, netN):
        """The member pad per net at an end (the single pad, or for doubled pads the adjacent pair whose P side
        matches), and the pads left over to bridge. spec: {side: [(ref, pad), ...]}."""
        out, bridges = {}, []
        if len(spec["P"]) == 1 and len(spec["N"]) == 1:
            for side in ("P", "N"):
                out[side] = self.pad_geom(*spec[side][0])[0]; out[side + "pad"] = spec[side][0]
            return out, bridges
        # doubled: order the four pads along the row (the '+' normal of the exit direction)
        n = nplus(d)
        row = sorted([(side, rp) for side in ("P", "N") for rp in spec[side]], key=lambda sn: (lambda c: c[0] * n[0] + c[1] * n[1])(self.pad_geom(*sn[1])[0]))
        # members at one end of the row (bridges at the other), or the middle pair (bridges at both ends)
        cands = [(row[0], row[1], row[2], row[3], "end"), (row[2], row[3], row[0], row[1], "end"), (row[1], row[2], row[0], row[3], "middle")]
        choice = None
        for a, b, c, dd, mode in cands:
            if a[0] == b[0]:
                continue
            P = a if a[0] == "P" else b; N = b if a[0] == "P" else a
            cP = self.pad_geom(*P[1])[0]; cN = self.pad_geom(*N[1])[0]
            m = ((cP[0] + cN[0]) / 2, (cP[1] + cN[1]) / 2)
            s = 1 if cross(d, (cP[0] - m[0], cP[1] - m[1])) >= 0 else -1
            if want_sign is None or s == want_sign:
                choice = (P, N, (a, b), (c, dd), mode); break
        if choice is None:
            a, b, c, dd, mode = cands[0]; P = a if a[0] == "P" else b; N = b if a[0] == "P" else a; choice = (P, N, (a, b), (c, dd), mode)
        P, N, members, rest, mode = choice
        out["P"] = self.pad_geom(*P[1])[0]; out["Ppad"] = P[1]; out["N"] = self.pad_geom(*N[1])[0]; out["Npad"] = N[1]
        bridges = [("members", members), ("rest", rest), ("mode", mode)]
        return out, bridges

    def bridge(self, name, bridges, netP, netN, d, layer):
        """A receptacle's doubled D+/D- pads: the two pads not used as members are joined to the members behind
        the row, through vias on the far side of the board, staggered so nothing is within clearance."""
        if not bridges:
            return
        info = dict(bridges); members = info["members"]; rest = info["rest"]; mode = info.get("mode", "end")
        n = nplus(d)
        def t_of(rp):
            c = self.pad_geom(*rp)[0]; return c[0] * n[0] + c[1] * n[1]
        row = sorted(list(members) + list(rest), key=lambda sn: t_of(sn[1]))
        other = "B.Cu" if layer == "F.Cu" else "F.Cu"
        def inner(rp):
            c, half, ax, tht = self.pad_geom(*rp)
            s = ax[0] * d[0] + ax[1] * d[1]
            k = -(half - 0.1) if s > 0 else (half - 0.1)
            return (c[0] + ax[0] * k, c[1] + ax[1] * k)
        def at(rp, depth, dt):                                         # dt along +n from the pad's inner end, depth behind it
            c = inner(rp)
            return (c[0] - d[0] * depth + n[0] * dt, c[1] - d[1] * depth + n[1] * dt)
        def net_of(sn):
            return netP if sn[0] == "P" else netN
        vias = collections.defaultdict(list)
        def via(net, rp, depth, dt):
            v = at(rp, depth, dt); vias[net].append(v)
            back = at(rp, 0.3, 0.0)                                    # straight behind the pad first, clear of the row's next pad
            self.tracks.append((net, layer, L.ESCAPE_WIDTH, inner(rp), back)); self.tracks.append((net, layer, L.ESCAPE_WIDTH, back, v))
            self.add_via(net, v); return v
        if mode == "end":
            mirror = row[0] not in members                              # members at the high-t end of the row
            if mirror:
                row = row[::-1]
            m0, m1, b2, b3 = row                                       # m0 adjoins nothing, m1 adjoins b2 (same net as m0), b3 (same net as m1)
            sgn = -1 if mirror else 1
            k1, k2 = L.BRIDGE_DEPTHS
            for net, rp, depth, dt in [(net_of(m0), m0[1], k1, -0.35 * sgn), (net_of(b2), b2[1], k1, 0.05 * sgn), (net_of(m1), m1[1], k2, -0.25 * sgn), (net_of(b3), b3[1], k2, 0.8 * sgn)]:
                via(net, rp, depth, dt)
            for net, vs in vias.items():
                self.tracks.append((net, other, L.ESCAPE_WIDTH, vs[0], vs[1]))
        else:                                                          # the middle pair are the members: one bridge at each end of the row
            p0, p1, p2, p3 = row                                       # p0 shares p2's net, p3 shares p1's net
            k1, k2, k3 = L.BRIDGE_DEPTHS[0], L.BRIDGE_DEPTHS[1], L.BRIDGE_DEPTHS[1] + L.BRIDGE_DEPTHS[0]
            out0 = -1.0; out3 = 1.0                                    # outward along n at each end of the row
            v0 = via(net_of(p0), p0[1], k1, 0.35 * out0)               # p0's net, behind its end of the row
            v2 = via(net_of(p2), p2[1], k2, 0.35 * out3)               # its member, deeper, leaning to the far end
            v3 = via(net_of(p3), p3[1], k1, 0.5 * out3)
            v1 = via(net_of(p1), p1[1], k3, 0.35 * out0)
            self.tracks.append((net_of(p0), other, L.ESCAPE_WIDTH, v0, v2))                      # straight, diagonal
            corner = (v3[0] - d[0] * (k3 - k1), v3[1] - d[1] * (k3 - k1))                        # an L: deeper first, then across
            self.tracks.append((net_of(p3), other, L.ESCAPE_WIDTH, v3, corner))
            self.tracks.append((net_of(p3), other, L.ESCAPE_WIDTH, corner, v1))

    def forced_side(self, ref, host):
        """The directives' side for this part (SIDES, layout.md 3.7: one channel of a mirrored pair on each side), or its
        host's forced side for the parts that follow it; an indicator LED, an ESD part or a connector never follows."""
        if ref in L.SIDES:
            return L.SIDES[ref]
        if host in L.SIDES and not self.values[ref].upper().startswith("LED") and not self.is_esd(ref) and kind(ref) != "conn":
            if prefix(ref) == "R" and any(len(self.nets.get(net, ())) == 2 and any(self.values.get(hr, "").upper().startswith("LED") for hr, _ in self.nets[net])
                                          for _, net in self.pads_of[ref] if net):
                return None                                        # an indicator's series resistor stays on top with its LED
            return L.SIDES[host]
        return None

    # ---- the datasheet's layout template (layout.md 3.2)
    def template_hint(self, host, ref, shared, lay):
        """Where the template puts this part: the pin it hangs from (the most specific of the templated pins it shares),
        that pin's side turned with the host, the ring the part's kind takes in the figure's order, whether it lies along
        the side, and its rank in the placement order (the template's pin order, then the kind's order)."""
        names = self.pin_names.get(host, {}); pins = lay.get("pins", {})
        best = None
        for pad, net in shared:
            name = names.get(pad)
            if name in pins and net:
                nodes = len(self.nets.get(net, ()))
                if best is None or nodes < best[0]:
                    best = (nodes, name, net)
        if best is None:
            return None
        _, name, net = best
        entry = pins[name]; side, order = entry[0], entry[1]; start = entry[2] if len(entry) > 2 else 0
        kinds = [k.rstrip("^") for k in order]; pk = prefix(ref)
        role = kinds.index(pk) if pk in kinds else len(kinds)
        along = pk in kinds and order[role].endswith("^")
        return {"sides": (rot_side(side, self.fps[host].GetOrientationDegrees()),), "ring": start + (role if pk in kinds else 0),
                "along": along, "rank": (list(pins).index(name), role), "pin": name}

    def output_hint(self, host, rail):
        """The output capacitors' place: the template's inductor_out, for a part on the regulator's output rail hosted by its inductor."""
        for reg, ind in self.inductor_of.items():
            if ind == host and reg in self.layout_of and "inductor_out" in self.layout_of[reg]:
                sw = self.reg_nets[reg]["sw"]
                if rail != sw and rail in {n for _, n in self.pads_of[host]}:
                    side, order = self.layout_of[reg]["inductor_out"][:2]
                    return {"sides": (rot_side(side, self.fps[reg].GetOrientationDegrees()),), "ring": 0, "along": False, "rank": (99, 0), "pin": "out"}
        return None

    # ---- hosts
    def best_host(self, ref):
        """(host, attachment-point resolver, the host's net the part should face, priority) for an unplaced part,
        or None. Recomputed every round: a part whose partner on a two-node net is not down yet scores low
        on its planes alone, and is picked up by the partner once that is placed."""
        scores, shared = collections.Counter(), collections.defaultdict(list)
        isl = self.island.get(ref)
        for num, net in self.pads_of[ref]:
            if not net or net.startswith("unconnected-"):
                continue
            nodes = self.nets[net]
            w = (0.15 if net in self.plane else 1.0) * self.weight.get(net, 1.0) / len(nodes)
            for hr, hp in nodes:
                if hr == ref or hr not in self.side or hr in L.HOLES or hr in self.parked:
                    continue
                where = 2.5 if (isl and self.island.get(hr) == isl) else 1.5 if self.same_sheet(hr, ref) else 1.0
                bonus = {"conn": 2.5, "ic": 1.3, "passive": 1.0}[kind(hr)] * where
                scores[hr] += w * bonus; shared[hr].append((hp, net))
        if not scores:
            return None
        if kind(ref) == "ic" and any(kind(hr) != "passive" for hr in scores):   # an IC is never hosted by a passive while a connector or IC will do
            scores = collections.Counter({hr: s for hr, s in scores.items() if kind(hr) != "passive"})
        crystal = [hr for hr in scores if prefix(hr) == "Y" and any(net not in self.plane for _, net in shared[hr])]
        if prefix(ref) == "C" and crystal:                         # a load capacitor belongs to its crystal (layout.md 5), not to the IC's pin
            scores = collections.Counter({hr: scores[hr] for hr in crystal})
        button = [hr for hr in scores if prefix(hr) == "SW" and any(net not in self.plane for _, net in shared[hr])]
        if prefix(ref) in ("R", "C") and button:                   # a button's debounce and pull-up parts are the button's, wherever it sits
            scores = collections.Counter({hr: scores[hr] for hr in button})
        esd_at_conn = self.is_esd(ref) and any(kind(hr) == "conn" for hr in scores)   # 3.8 outranks 3.1: ESD stays at its connector
        if esd_at_conn:
            scores = collections.Counter({hr: s for hr, s in scores.items() if kind(hr) == "conn"})
        hub = self.hub_of.get(isl)
        waiting = bool(hub) and hub != ref and hub not in self.side and not esd_at_conn   # 3.1: an island's hub goes down first, its mates follow it
        mates = [hr for hr in scores if isl and self.island.get(hr) == isl]
        if mates and ref != hub and not esd_at_conn:               # the board mimics the schematic's islands: a part whose placed island
            scores = collections.Counter({hr: scores[hr] for hr in mates})   # mate shares any net with it is placed with that mate
            shared = {hr: shared[hr] for hr in mates}
        specific = {hr: [(hp, net) for hp, net in pins if net not in self.plane] for hr, pins in shared.items()}
        specific = {hr: pins for hr, pins in specific.items() if pins}
        if specific:
            host = max(specific, key=lambda hr: scores[hr])
            pts = [self.fps[host].FindPadByNumber(hp).GetPosition() for hp, _ in specific[host]]
            x = sum(p.x for p in pts) / len(pts) / 1e6; y = sum(p.y for p in pts) / len(pts) / 1e6
            a = self.area[ref]
            tier = (4 if a >= L.BIG_AREA else 2) if kind(host) != "passive" else 1
            if (self.is_esd(ref) and kind(host) == "conn") or prefix(ref) == "Y" or (prefix(host) == "Y" and prefix(ref) == "C"):
                tier = 6                                           # ESD, crystals and their load capacitors first of all: the pins and the crystal's ends are theirs
            if waiting and tier < 6:
                tier = 0
            reg = self.regulator_of.get(isl)
            lay = self.layout_of.get(host)
            if lay and host == self.hub_of.get(isl) and ref != host and not waiting and tier < 6:
                hint = self.template_hint(host, ref, specific[host], lay)
                if hint:                                           # the figure's city (3.2) is placed before every other satellite: its pin order, then its order along the pin
                    return host, (lambda: (x, y)), specific[host][0][1], (5.9, -hint["rank"][0], -hint["rank"][1], -a), hint
            if reg and ref != reg and not waiting and tier < 6:    # a regulator without a figure: the switching loop (inductor, diode) first, then the rest at their pins
                nets = {net for _, net in specific[host]}
                tier = 5.9 if self.reg_nets[reg]["sw"] in nets else 5.6
                return host, (lambda: (x, y)), specific[host][0][1], (tier, a if tier > 5.8 else -a, scores[host]), None
            return host, (lambda: (x, y)), specific[host][0][1], (tier, a if tier > 1 else 0, scores[host]), None
        # only planes shared (a decoupling or bulk capacitor, or a part waiting for its partner): among the parts
        # on its rail, the one the schematic drew it beside, on the same sheet, an IC counting as nearer than a
        # passive at the same distance, a capacitor never hosted by another capacitor; then its next free pin
        me = self.schpos.get(ref)
        def sch_dist(hr):
            there = self.schpos.get(hr)
            if not me or not there or there[0] != me[0]:
                return 1e9
            return math.hypot(there[1] - me[1], there[2] - me[2])
        def is_gnd(net):
            return net == "GND" or net.endswith("GND")
        cands = [hr for hr in scores if any(not is_gnd(net) for _, net in shared[hr])] or list(scores)
        cands = [hr for hr in cands if kind(hr) != "passive" or self.has_specific(hr)] or cands
        same = [hr for hr in cands if sch_dist(hr) < 1e9]
        cands = same or cands
        near = {"ic": 0.25, "conn": 0.5, "passive": 1.0}             # a symbol's position is its centre; an IC's pins reach further
        host = min(cands, key=lambda hr: (sch_dist(hr) * near[kind(hr)], -scores[hr]))
        pins = shared[host]
        rail = next((net for hp, net in pins if not is_gnd(net)), pins[0][1])
        cand = [hp for hp, net in pins if net == rail]
        def resolve():
            hp = cand[self.used_pins[(host, rail)] % len(cand)]; self.used_pins[(host, rail)] += 1
            p = self.fps[host].FindPadByNumber(hp).GetPosition(); return (p.x / 1e6, p.y / 1e6)
        if isl:                                                    # drawn beside an IC of its island that is not down yet: it waits for it
            drawn_by = min((r for r in self.islands[isl][1] if r in self.fps and kind(r) == "ic"), key=sch_dist, default=None)
            waiting = waiting or (drawn_by is not None and drawn_by not in self.side)
        waiting = waiting or self.has_specific(ref)
        a = self.area[ref]
        tier = 0 if waiting else 5 if a < L.SMALL_AREA else 3      # small decoupling first of all; bulk after the big parts on the pins
        reg = self.regulator_of.get(isl)
        lay = self.layout_of.get(host)
        if (reg or lay) and ref != host and not waiting:           # a figure's or a regulator's capacitors: smallest nearest the pin, before all
            hint = None
            if lay:                                                # the figure's side for the pin on their rail
                hint = self.template_hint(host, ref, [(hp, net) for hp, net in pins if net == rail], lay)
            hint = hint or self.output_hint(host, rail)            # a regulator's output capacitors: the figure's place by the inductor
            tier = 5.8 if (reg and rail == self.reg_nets[reg]["in"]) else 5.7
            return host, resolve, rail, (tier, -a, scores[host]), hint
        return host, resolve, rail, (tier, a if tier > 0 else 0, scores[host]), None

    def orientation(self, ref, hostnet, side, layer, host=None):
        """Two-pin parts turn so the pad carrying the host's net faces the host; a flow-through part (an ESD array
        with the connector's pair on one pin row and the IC's on the other) turns its host-side row to the host;
        others stay upright."""
        pads = list(self.fps[ref].Pads())
        if prefix(ref) == "Y":                                     # a crystal lies along the IC's edge, a signal pad at each end (layout.md 5)
            return 0 if side in ("T", "B") else 90
        ob = self.origin_box(ref, 0, layer); cx, cy = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2
        if len(pads) == 2:
            near = next((p for p in pads if self.pad_net.get((ref, p.GetNumber())) == hostnet), pads[0])
            v = (near.GetPosition().x / 1e6 - cx, near.GetPosition().y / 1e6 - cy)
        else:
            hostnets = {net for _, net in self.pads_of[host] if net} if host else {hostnet}
            near = [p for p in pads if self.pad_net.get((ref, p.GetNumber())) in hostnets]
            far = [p for p in pads if self.pad_net.get((ref, p.GetNumber())) not in hostnets and not (self.pad_net.get((ref, p.GetNumber())) or "GND").endswith("GND")]
            if not near or not far:
                return 0
            nx = sum(p.GetPosition().x for p in near) / len(near) / 1e6; ny = sum(p.GetPosition().y for p in near) / len(near) / 1e6
            fx = sum(p.GetPosition().x for p in far) / len(far) / 1e6; fy = sum(p.GetPosition().y for p in far) / len(far) / 1e6
            v = (nx - fx, ny - fy)
            if abs(v[0]) < 0.05 and abs(v[1]) < 0.05:
                return 0
        want = {"L": (1, 0), "R": (-1, 0), "T": (0, 1), "B": (0, -1)}[side]
        return max((0, 90, 180, 270), key=lambda d: rot_vec(v, d)[0] * want[0] + rot_vec(v, d)[1] * want[1])

    def try_box(self, ref, rot, cx, cy, layer):
        """Pose the part with its courtyard centred at (cx, cy) on that side if that is allowed."""
        ob = self.origin_box(ref, rot, layer)
        w, h = ob[2] - ob[0], ob[3] - ob[1]
        b = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if not self.allowed(ref, b, layer):
            if ref == DEBUG_REF and self.debug_shown < 12:
                self.debug_shown += 1; print(f"   {ref} refused at ({cx:.1f},{cy:.1f}) rot {rot} on {layer}: {self.why_not(ref, b, layer)}")
            return False
        self.pose(ref, cx - (ob[0] + ob[2]) / 2, cy - (ob[1] + ob[3]) / 2, rot, layer)
        return True

    def place_in_rings(self, ref, host, point, hostnet, layer, rings=None, sides=(), first_ring=0, along=False, toward=None):
        hb = self.box_of(host)
        if hb is None:                                             # a parked host has no box: the nearest free spot will do
            return None
        gap = L.RING_GAP if self.same_city(ref, host) else L.CITY_GAP   # another city's host: the void between them (3.1)
        tuck = layer == "B" and self.side[host] == "F" and host not in self.under   # under an SMD host's pin row (not a through-hole part's), else beside it
        base = -L.BOTTOM_TUCK if tuck else gap
        d = {"L": point[0] - hb[0], "R": hb[2] - point[0], "T": point[1] - hb[1], "B": hb[3] - point[1]}
        for side in list(sides) + [s for s in sorted(d, key=d.get) if s not in sides]:   # the directives' side first, else nearest first
            rot = self.orientation(ref, hostnet, side, layer, host)
            if along and (not sides or side in sides):             # the part lies along the side (a diode beside the pin, a capacitor across its trace), host pad toward the pin
                rot = (rot + 90) % 360
            ob = self.origin_box(ref, rot, layer)
            w, h = ob[2] - ob[0], ob[3] - ob[1]
            normal, along = (w, h) if side in ("L", "R") else (h, w)
            t = point[1] if side in ("L", "R") else point[0]
            lo, hi = (hb[1], hb[3]) if side in ("L", "R") else (hb[0], hb[2])
            ringlist = self.rings[(host, side, layer)]
            order = ([(s, r) for r in range(rings or L.RINGS) for s in L.RING_SLIDES] if side in sides   # a directed side: along it first, then out
                     else [(s, r) for s in L.RING_SLIDES for r in range(rings or L.RINGS)])
            for slide, ri in order:
                if ri < first_ring and side in sides:
                    continue
                if ri >= len(ringlist):
                    ringlist.append({"depth": 0.0, "members": []})
                ring = ringlist[ri]
                if ring["members"] and normal > ring["depth"] + 1e-6 and any(rr["members"] for rr in ringlist[ri + 1:]):
                    continue                                       # fatter than this ring, and the outer rings are occupied
                inner = base + sum(rr["depth"] for rr in ringlist[:ri]) + L.RING_GAP * ri + 0.005 * (ri + 1)   # a hair of slack per ring: the margin test is exact
                offset = inner + normal / 2
                reach = L.RING_REACH
                for dt in [0] + [sgn * k * 0.5 for k in range(1, int(slide * 2) + 1) for sgn in (1, -1)]:
                    tt = t + dt
                    if tt - along / 2 < lo - reach or tt + along / 2 > hi + reach:
                        continue
                    iv = (tt - along / 2 - L.PACK_MARGIN / 2, tt + along / 2 + L.PACK_MARGIN / 2)
                    if any(iv[0] < m[1][1] and iv[1] > m[1][0] for m in ring["members"]):
                        continue
                    if side == "L":   cx, cy = hb[0] - offset, tt
                    elif side == "R": cx, cy = hb[2] + offset, tt
                    elif side == "T": cx, cy = tt, hb[1] - offset
                    else:             cx, cy = tt, hb[3] + offset
                    r_try = rot
                    if along and (not sides or side in sides) and len(list(self.fps[ref].Pads())) == 2:
                        pad = next((p for p in self.fps[ref].Pads() if self.pad_net.get((ref, p.GetNumber())) == hostnet), None)
                        if pad is not None:                        # the host-net pad toward the pin along the side (or toward a given point)
                            o = self.fps[ref].GetOrientationDegrees(); pos = self.fps[ref].GetPosition(); pp = pad.GetPosition()
                            v = rot_vec(((pp.x - pos.x) / 1e6, (pp.y - pos.y) / 1e6), rot - o)
                            if toward is not None:
                                d0 = math.hypot(cx + v[0] - toward[0], cy + v[1] - toward[1]); d1 = math.hypot(cx - v[0] - toward[0], cy - v[1] - toward[1])
                                if d1 < d0:
                                    r_try = (rot + 180) % 360
                            else:
                                towards = (t - tt)
                                comp = v[1] if side in ("L", "R") else v[0]
                                if towards != 0 and comp * towards < 0:
                                    r_try = (rot + 180) % 360
                    if self.try_box(ref, r_try, cx, cy, layer):
                        ring["members"].append((ref, iv)); self.ring_side[ref] = side
                        ring["depth"] = max(ring["depth"], normal)
                        return f"{side}{ri}"
        return None

    def place_nearest(self, ref, point, hostnet, layer, radius=None):
        """The nearest free spot to the pin on that side, spiralling outward; the part faces the pin."""
        rad, radii = 0.5, []
        while rad <= (radius or L.SEARCH_RADIUS):
            radii.append(rad); rad += 0.5 if rad < 20 else 1.0
        for rad in radii:
            n = max(8, int(2 * math.pi * rad / (0.5 if rad < 20 else 1.0)))
            for j in range(n):
                a = 2 * math.pi * j / n
                cx, cy = point[0] + rad * math.cos(a), point[1] + rad * math.sin(a)
                if abs(math.cos(a)) >= abs(math.sin(a)):
                    side = "R" if cx > point[0] else "L"           # the side of the pin the part is on; it faces the pin
                else:
                    side = "B" if cy > point[1] else "T"
                rot = self.orientation(ref, hostnet, side, layer)
                if self.try_box(ref, rot, cx, cy, layer) or self.try_box(ref, (rot + 90) % 360, cx, cy, layer):   # or across the pocket
                    return f"free@{rad:.0f}"
        return None

    def place_satellites(self):
        unplaced = [ref for ref in self.fps if ref not in self.side]
        while unplaced:
            best = None
            for ref in unplaced:
                got = self.best_host(ref)
                if got and (best is None or got[3] > best[1][3]):
                    best = (ref, got)
            if best is None:
                break
            ref, (host, resolve, hostnet, prio, hint) = best
            point = resolve()
            if ref == DEBUG_REF:
                print(f"{ref}: chosen with priority {prio} (host {host}, template {hint}); {len(self.order)} satellites down before it")
            if ref not in self.city_of and host in self.city_of:   # a lone part joins its host's city (3.1)
                self.city_of[ref] = self.city_of[host]
            forced = self.forced_side(ref, host)
            layer = forced or ("B" if self.bottom_ok(ref, host) else "F")
            if hint:
                sides, first_ring, along = hint["sides"], hint["ring"], hint["along"]
            else:
                sides = (L.REGULATORS[host]["sw"],) if host in L.REGULATORS and hostnet == self.reg_nets[host]["sw"] and "sw" in L.REGULATORS[host] else ()
                first_ring, along = 0, False
                if forced and kind(host) == "conn" and ref in L.SIDES:   # the split channel's switch sits inboard of its connector, as the top one does (3.7)
                    hb = self.box_of(host)
                    edge = min({"L": hb[0], "R": W - hb[2], "T": hb[1], "B": H - hb[3]}.items(), key=lambda kv: kv[1])[0]
                    sides = ({"L": "R", "R": "L", "T": "B", "B": "T"}[edge],)
            toward = None
            if prefix(host) == "Y" and prefix(ref) == "C" and self.has_specific(ref) and host in self.ring_side and host in self.host_of:
                yside = self.ring_side[host]; ic = self.host_of[host]      # a crystal's load capacitor (layout.md 5): at the end of the crystal nearer its own
                pad = next((p for p in self.fps[host].Pads() if self.pad_net.get((host, p.GetNumber())) == hostnet), None)   # pad, across the IC's edge,
                if pad is not None and ic in self.fps:                      # its signal pad on the trace from that pad to the IC's pin
                    yb = self.box_of(host); px, py = pad.GetPosition().x / 1e6, pad.GetPosition().y / 1e6
                    end = ("L" if px < (yb[0] + yb[2]) / 2 else "R") if yside in ("T", "B") else ("T" if py < (yb[1] + yb[3]) / 2 else "B")
                    sides, first_ring, along, point = (end,), 0, True, (px, py)
                    icpad = next((p for p in self.fps[ic].Pads() if self.pad_net.get((ic, p.GetNumber())) == hostnet), None)
                    if icpad is not None:
                        toward = (icpad.GetPosition().x / 1e6, icpad.GetPosition().y / 1e6)
            prefer_along = False
            if prefix(ref) == "C" and not self.has_specific(ref) and kind(host) == "ic" and not hint and not along:
                along = prefer_along = True                        # decoupling lies across its power trace (3.3): along the IC's edge, power pad nearest the pin
            how = None
            if layer == "B" and not forced and not self.has_specific(ref) and self.side[host] == "F":   # decoupling stays on its IC's side unless its first rings are full
                how = self.place_in_rings(ref, host, point, hostnet, "F", rings=2, sides=sides, first_ring=first_ring, along=along, toward=toward)
            how = how or self.place_in_rings(ref, host, point, hostnet, layer, sides=sides, first_ring=first_ring, along=along, toward=toward)
            if not how and prefer_along:                           # no room across the trace at this pin: the capacitor faces the pin instead
                how = self.place_in_rings(ref, host, point, hostnet, layer, sides=sides, first_ring=first_ring)
            if forced:                                             # a directed side is kept: the nearest free spot on it
                how = how or self.place_nearest(ref, point, hostnet, layer)
            else:
                if not how and layer == "B":                       # no ring under the pin: near it on the bottom, else beside it on top
                    how = self.place_nearest(ref, point, hostnet, "B", radius=8.0) or self.place_in_rings(ref, host, point, hostnet, "F")
                how = how or self.place_nearest(ref, point, hostnet, layer) or (layer == "B" and self.place_nearest(ref, point, hostnet, "F"))
            if ref == DEBUG_REF:
                print(f"{ref}: host {host} at {point[0]:.1f},{point[1]:.1f} facing {hostnet}, side {layer}: {how}; placed as number {len(self.order) + 1}; "
                      f"spots rejected by: {dict(self.debug.most_common(8))}")
            if how:
                self.order.append((ref, host, self.side[ref], how)); self.host_of[ref] = host
            else:
                self.parked.append(ref); self.order.append((ref, host, "-", "parked"))
                self.side[ref] = "F"                               # keeps the loop moving; parked below
            unplaced.remove(ref)
        self.parked += unplaced
        for ref in self.parked:                                    # the nearest free spot to the spare area, anywhere
            self.side.pop(ref, None)
            how = self.place_nearest(ref, L.SPARE, None, "F", radius=200.0)
            if not how:
                ob = self.origin_box(ref, 0); self.pose(ref, L.SPARE[0] - ob[0], L.SPARE[1] - ob[1], 0)
            self.order = [(r, h, self.side[ref], f"parked, then {how or 'SPARE'}") if r == ref else (r, h, s, w) for r, h, s, w in self.order]

    def relax(self):
        """Residual courtyard overlaps between movable parts on a side: nudge the later one apart where allowed."""
        left = 0
        for layer in ("F", "B"):
            movable = [ref for ref in self.boxes[layer] if ref not in self.fixed]
            for _ in range(20):
                moved = 0
                for i, a in enumerate(movable):
                    for bref in movable[i + 1:]:
                        ba, bb = self.boxes[layer][a], self.boxes[layer][bref]
                        if not overlap(ba, bb, L.PACK_MARGIN):
                            continue
                        dx = (bb[0] + bb[2]) / 2 - (ba[0] + ba[2]) / 2; dy = (bb[1] + bb[3]) / 2 - (ba[1] + ba[3]) / 2
                        ox = min(ba[2], bb[2]) - max(ba[0], bb[0]) + L.PACK_MARGIN + 0.1
                        oy = min(ba[3], bb[3]) - max(ba[1], bb[1]) + L.PACK_MARGIN + 0.1
                        steps = [(ox if dx >= 0 else -ox, 0), (0, oy if dy >= 0 else -oy)]
                        if ox > oy:
                            steps.reverse()
                        for sx, sy in steps:
                            nb = (bb[0] + sx, bb[1] + sy, bb[2] + sx, bb[3] + sy)
                            if self.allowed(bref, nb, layer):
                                p = self.fps[bref].GetPosition()
                                self.pose(bref, p.x / 1e6 + sx, p.y / 1e6 + sy, self.fps[bref].GetOrientationDegrees(), layer); moved += 1
                                break
                if not moved:
                    break
            for i, a in enumerate(movable):
                for bref in movable[i + 1:]:
                    if overlap(self.boxes[layer][a], self.boxes[layer][bref]):
                        left += 1
        return left

    # ---- silkscreen: ecad-standards/layout.md section 6
    def silkscreen(self):
        """Reference designators next to their parts where they overlap nothing, else omitted; a top-side cluster
        that loses more than a third of its passives' designators loses them all and is outlined."""
        texts = {"F": [], "B": []}
        omitted, outlined, not_outlined, stepped_out = [], [], [], []
        court = {layer: dict(self.boxes[layer]) for layer in ("F", "B")}
        blocks_b = [b for blocks in self.under.values() for b in blocks]   # the bottom's silk also keeps off through-hole pads and via fields
        inset = 0.5
        def text_box(t):
            bb = t.GetBoundingBox()
            return (bb.GetLeft() / 1e6 - 0.1, bb.GetTop() / 1e6 - 0.1, bb.GetRight() / 1e6 + 0.1, bb.GetBottom() / 1e6 + 0.1)
        def fits(b, layer):
            if b[0] < inset or b[1] < inset or b[2] > W - inset or b[3] > H - inset:
                return False
            if any(overlap(b, c) for c in court[layer].values()):
                return False
            if layer == "B" and any(overlap(b, c) for c in blocks_b):
                return False
            return not any(overlap(b, tb) for tb in texts[layer])
        def inboard_first(ref, b):
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if kind(ref) != "conn":
                return ["T", "B", "L", "R"]
            dist = {"T": cy, "B": H - cy, "L": cx, "R": W - cx}  # the side farthest from the edge reads with the plug fitted
            return sorted(dist, key=lambda s: -dist[s])
        def prepare(ref, size):
            layer = self.side[ref]; t = self.fps[ref].Reference()
            t.SetVisible(True); t.SetLayer(SILK[layer]); t.SetTextThickness(pcbnew.FromMM(max(0.1, round(0.15 * size, 2))))
            t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER); t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
            t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
            return layer, t
        def place_ref(ref, sizes, gaps=(0.15, 0.35, 0.6, 0.85)):
            b = court[self.side[ref]][ref]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            tall = (b[3] - b[1]) > 1.5 * (b[2] - b[0])
            for size in sizes:
                layer, t = prepare(ref, size)
                for side in inboard_first(ref, b):
                    angle = 90 if (side in ("L", "R") and tall) else 0
                    t.SetTextAngleDegrees(angle); t.SetPosition(MM(cx, cy))
                    tb = text_box(t); tw, th = tb[2] - tb[0], tb[3] - tb[1]
                    span = (b[3] - b[1]) if side in ("L", "R") else (b[2] - b[0])
                    slides = [0] + [sgn * k * 0.5 for k in range(1, int(span / 2 / 0.5) + 1) for sgn in (1, -1)]
                    for gap in gaps:
                        for sl in slides:
                            if side == "T":   x, y = cx + sl, b[1] - gap - th / 2
                            elif side == "B": x, y = cx + sl, b[3] + gap + th / 2
                            elif side == "L": x, y = b[0] - gap - tw / 2, cy + sl
                            else:             x, y = b[2] + gap + tw / 2, cy + sl
                            t.SetPosition(MM(x, y))
                            tb = text_box(t)
                            if fits(tb, layer):
                                texts[layer].append(tb); return True
            t.SetVisible(False)
            return False
        def place_ref_near(ref, size):
            layer, t = prepare(ref, size); t.SetTextAngleDegrees(0)
            b = court[layer][ref]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            radius = math.hypot(b[2] - b[0], b[3] - b[1]) / 2 + 10.0
            rad = 1.0
            while rad <= radius:
                n = max(8, int(2 * math.pi * rad / 0.5))
                for j in range(n):
                    a = 2 * math.pi * j / n
                    t.SetPosition(MM(cx + rad * math.cos(a), cy + rad * math.sin(a)))
                    tb = text_box(t)
                    if fits(tb, layer):
                        texts[layer].append(tb); return True
                rad += 0.5
            t.SetVisible(False)
            return False
        majors = [r for r in self.fps if kind(r) != "passive" and r not in L.HOLES]
        minors = [r for r in self.fps if kind(r) == "passive"]
        for ref in L.HOLES:
            self.fps[ref].Reference().SetVisible(False)
        for ref in majors:
            if not place_ref(ref, L.REFDES_SIZES):
                if any(place_ref_near(ref, s) for s in L.REFDES_SIZES):
                    stepped_out.append(ref)
                else:
                    omitted.append(ref)
        for ref in minors:
            if not place_ref(ref, L.REFDES_SIZES):
                omitted.append(ref)
        # the cluster rule, on the top side
        clusters = collections.defaultdict(list)
        for ref, host, layer, _ in self.order:
            if ref[0] in "RCLD" and layer == "F":
                clusters[host].append(ref)
        for host, members in clusters.items():
            lost = [m for m in members if m in omitted]
            if len(members) >= 3 and len(lost) * 3 > len(members):
                for m in members:
                    t = self.fps[m].Reference()
                    if t.IsVisible():
                        tb = text_box(t)
                        texts["F"][:] = [x for x in texts["F"] if x != tb]
                        t.SetVisible(False); omitted.append(m)
                xs = [court["F"][r] for r in members + [host] if r in court["F"]]
                m = L.PACK_MARGIN / 2
                ob = (min(x[0] for x in xs) - m, min(x[1] for x in xs) - m, max(x[2] for x in xs) + m, max(x[3] for x in xs) + m)
                edges = [(ob[0], ob[1], ob[2], ob[1]), (ob[2], ob[1], ob[2], ob[3]), (ob[2], ob[3], ob[0], ob[3]), (ob[0], ob[3], ob[0], ob[1])]
                others = {r: c for r, c in court["F"].items() if r not in members and r != host}
                def edge_clear(e):
                    eb = (min(e[0], e[2]) - 0.075, min(e[1], e[3]) - 0.075, max(e[0], e[2]) + 0.075, max(e[1], e[3]) + 0.075)
                    return (eb[0] >= inset and eb[1] >= inset and eb[2] <= W - inset and eb[3] <= H - inset
                            and not any(overlap(eb, c) for c in others.values()) and not any(overlap(eb, tb) for tb in texts["F"]))
                enclosed = any(overlap(ob, c) for c in others.values())
                if not enclosed and all(edge_clear(e) for e in edges):
                    for e in edges:
                        s = pcbnew.PCB_SHAPE(self.board); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
                        s.SetStart(MM(e[0], e[1])); s.SetEnd(MM(e[2], e[3])); s.SetLayer(pcbnew.F_SilkS); s.SetWidth(pcbnew.FromMM(0.15))
                        self.board.Add(s)
                    outlined.append(host)
                else:
                    not_outlined.append(host)
        return omitted, outlined, not_outlined, stepped_out


def copper_layers():
    """The copper layers of the directives' STACKUP, outer to outer: ("F.Cu", "In1.Cu", ..., "B.Cu")."""
    return tuple(item[0] for item in L.STACKUP if item[1] == "copper")


def copper_layers_count():
    return len(copper_layers())


def region_islands(region):
    """An isolated region's own plane islands: `islands` (a list of (name, net, layer, outline[, priority])) or the single
    `island` of the earlier directives; one per plane layer the region's own ground must cover."""
    return list(region.get("islands", ())) + ([region["island"]] if region.get("island") else [])


def rail_pieces(step=0.1, islands=()):
    """Per plane net and layer, the pieces its copper falls into once the higher-priority planes of other nets carve it
    (the directives' PLANES and the regions' islands, rasterised at `step` mm, and within a rasterised piece the planes
    joined only where their outlines overlap or touch exactly: a gap narrower than the step is a gap in the copper too):
    {(net, layer): [piece names]}, a piece named by the planes whose centres it holds. A rail in more than one piece is a
    directive error (layout.md 4): the router does not join the pieces of a plane net, and a via in a carved patch
    reaches nothing."""
    covers = [(p[1], p[2], p[3], p[4] if len(p) > 4 else 0, p[0]) for p in L.PLANES] + \
             [(i[1], i[2], i[3], i[4] if len(i) > 4 else 0, i[0]) for i in islands]
    nx, ny = int(W / step) + 1, int(H / step) + 1
    def inside(x, y, outline):
        n = len(outline); ok = False
        for i in range(n):
            (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % n]
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                ok = not ok
        return ok
    out = {}
    for layer in sorted({c[1] for c in covers}):
        top = {}                                                       # (ix, iy) -> (priority, net)
        for net, lay, outline, prio, name in covers:
            if lay != layer: continue
            xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
            for iy in range(max(0, int(min(ys) / step)), min(ny, int(max(ys) / step) + 1)):
                for ix in range(max(0, int(min(xs) / step)), min(nx, int(max(xs) / step) + 1)):
                    x, y = (ix + 0.5) * step, (iy + 0.5) * step
                    if inside(x, y, outline) and top.get((ix, iy), (-1, None))[0] < prio:
                        top[(ix, iy)] = (prio, net)
        seen = set()
        for cell, (prio, net) in top.items():
            if cell in seen: continue
            piece, stack = set(), [cell]
            while stack:
                c = stack.pop()
                if c in seen or top.get(c, (None, None))[1] != net: continue
                seen.add(c); piece.add(c)
                stack += [(c[0] + 1, c[1]), (c[0] - 1, c[1]), (c[0], c[1] + 1), (c[0], c[1] - 1)]
            members = [(name, outline) for n2, lay, outline, _, name in covers if n2 == net and lay == layer
                       and (int(sum(p[0] for p in outline) / len(outline) / step), int(sum(p[1] for p in outline) / len(outline) / step)) in piece]
            if not members:
                out.setdefault((net, layer), []).append(f"{len(piece)} cells"); continue
            # the exact test within the rasterised piece: planes join where their outlines overlap or touch, not across a gap
            def box(o): return (min(p[0] for p in o), min(p[1] for p in o), max(p[0] for p in o), max(p[1] for p in o))
            boxes = {name: box(o) for name, o in members}
            def joined(a, b):
                (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = boxes[a], boxes[b]
                return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1
            left = [name for name, _ in members]
            while left:
                comp, stack = [], [left.pop(0)]
                while stack:
                    n = stack.pop(); comp.append(n)
                    for m in [x for x in left if joined(n, x)]:
                        left.remove(m); stack.append(m)
                out.setdefault((net, layer), []).append(" ".join(comp))
    return out


def rails_gate(islands=()):
    """The rails' pieces printed, and the generation stopped on a rail in more than one piece."""
    pieces = rail_pieces(islands=islands)
    print("rails: " + "; ".join(f"{net} {layer} " + (f"{len(ps)} pieces ({' | '.join(ps)})" if len(ps) > 1 else "1 piece")
                                for (net, layer), ps in sorted(pieces.items())))
    split = [f"{net} on {layer}" for (net, layer), ps in pieces.items() if len(ps) > 1]
    if split:
        raise SystemExit("rails in pieces: " + ", ".join(split) + ": redraw the planes so each rail is one piece, or route the net")
    return pieces


def drc_gate(path):
    """kicad-cli pcb drc at every severity with the zones refilled; the report pinned to the title date and sorted so it is
    reproducible; the summary by severity (unconnected items apart). Returns the error counts by kind."""
    rep = os.path.join(os.path.dirname(path), "drc.txt")
    subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--refill-zones", "--format", "report", "-o", rep, path], capture_output=True, text=True)
    txt = open(rep, encoding="utf-8").read()
    txt = re.sub(r"Created on \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "Created on 2026-10-03T00:00:00", txt, count=1)
    def sort_section(m):
        entries = [e.rstrip("\n") for e in re.split(r"\n(?=\[)", m.group(0).rstrip("\n"))]   # each entry without its newline: the
        return entries[0] + "".join("\n" + e for e in sorted(entries[1:])) + "\n"             # sorted section keeps kicad-cli's shape
    txt = re.sub(r"^\*\* Found[^\n]*\n(?:\[.*?\n)+?(?=\n\*\*|\Z)", lambda m: sort_section(m), txt, flags=re.M | re.S)
    # the USB-C receptacles' coincident pad pairs (A1/B12, A4/B9, A9/B4, A12/B1 at one position each) are named by the A pad,
    # since kicad-cli names either; no other footprint's pads are touched
    usbc = set(re.findall(r'\(footprint "[^"]*USB_C_Receptacle[^"]*"(?:(?!\(footprint ).)*?\(property "Reference" "([^"]+)"',
                          open(path, encoding="utf-8").read(), re.S))
    if usbc:
        txt = re.sub(r"Pad (B12|B9|B4|B1) \[([^\]]*)\] of (" + "|".join(map(re.escape, sorted(usbc))) + r") ",
                     lambda m: "Pad " + {"B12": "A1", "B9": "A4", "B4": "A9", "B1": "A12"}[m.group(1)] + f" [{m.group(2)}] of {m.group(3)} ", txt)
    # the unconnected items as a tally per net: kicad-cli names a different pair of items for the same missing connection on
    # every run, so the entries themselves cannot be reproducible; the count per net is
    def tally_unconnected(m):
        entries = re.split(r"\n(?=\[)", m.group(0))
        nets = collections.Counter()
        for e in entries[1:]:
            n = re.search(r"\[([^\]]*)\] of|\[([^\]]*)\] on", e)
            nets[(n.group(1) or n.group(2)) if n else "?"] += 1
        head = entries[0].rstrip("\n").replace("unconnected pads", "unconnected pads, as a tally per net")
        return head + "".join(f"\n[unconnected_items]: {k} missing connection(s) on {n}" for n, k in sorted(nets.items(), key=lambda kv: (-kv[1], kv[0]))) \
            + ("\n\n" if m.group(0).endswith("\n\n") else "\n")                           # the blank line before the next header kept
    # the section is the header and every line to the next header, blank line included (never past a header)
    txt = re.sub(r"^\*\* Found \d+ unconnected pads \*\*\n(?:(?!\*\* ).*(?:\n|\Z))*", tally_unconnected, txt, flags=re.M)
    open(rep, "w", encoding="utf-8").write(txt)
    kinds = collections.Counter()
    for m in re.finditer(r"^\[(\w+)\][^\n]*\n\s*(?:(?:Rule: [^;]*|Local override); )?(\w+)", txt, re.M):   # the severity after "Rule: x; " or "Local override; "
        kinds[(m.group(1), m.group(2))] += 1
    errors = {k: v for (k, sev), v in kinds.items() if sev == "error" and k != "unconnected_items"}
    warnings = {k: v for (k, sev), v in kinds.items() if sev != "error" and k != "unconnected_items"}
    unconnected = sum(int(m.group(1)) for m in re.finditer(r"^\[unconnected_items\]: (\d+) missing connection", txt, re.M)) or \
                  sum(v for (k, sev), v in kinds.items() if k == "unconnected_items")
    print(f"DRC: {sum(errors.values())} error(s) {errors}; {unconnected} unconnected (unrouted); warnings {warnings}")
    return errors


def main(directives=None, out=None, project=None, house_fp=None):
    if directives is not None:
        configure(directives, out, project, house_fp)
    t0 = time.time()
    comps, nets, classes = netlist()
    # the gate on the classes: one the project defines but no net resolves to is a pattern that matches nothing (the
    # patterns are globs over the full hierarchical name, so a sheet-local net is matched only by one that starts with *)
    counts = {c: 0 for c in class_geometry()}
    for n, c in classes.items():
        if not n.startswith("unconnected-"):
            counts[c] = counts.get(c, 0) + 1
    print("net classes: " + ", ".join(f"{c} {k}" for c, k in counts.items()))
    empty = [c for c, k in counts.items() if c != "Default" and k == 0]
    if empty:
        raise SystemExit(f"net classes with no net: {', '.join(empty)}: a pattern that matches nothing (a sheet-local net's pattern starts with *)")
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(copper_layers_count())                 # from the directives' STACKUP
    for layer in sorted({p[2] for p in L.PLANES}):                    # a layer that carries a plane is a power layer in the board file too
        board.SetLayerType(board.GetLayerID(layer), pcbnew.LT_POWER)
    ds = board.GetDesignSettings(); ds.m_MinThroughDrill = pcbnew.FromMM(0.2)     # the fab's minimum is 0.15; thermal vias in footprints are 0.2
    ds.m_CopperEdgeClearance = pcbnew.FromMM(0.25)                                   # the fab's copper-to-edge minimum
    # ---- outline with rounded corners
    r = L.RADIUS
    def seg(a, b):
        s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetStart(MM(*a)); s.SetEnd(MM(*b))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(pcbnew.FromMM(0.1)); board.Add(s)
    def arc(a, m, b):
        s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_ARC); s.SetArcGeometry(MM(*a), MM(*m), MM(*b))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(pcbnew.FromMM(0.1)); board.Add(s)
    k = r * (1 - math.sqrt(0.5))
    seg((r, 0), (W - r, 0)); seg((W, r), (W, H - r)); seg((W - r, H), (r, H)); seg((0, H - r), (0, r))
    arc((0, r), (k, k), (r, 0)); arc((W - r, 0), (W - k, k), (W, r)); arc((W, H - r), (W - k, H - k), (W - r, H)); arc((r, H), (k, H - k), (0, H - r))
    # ---- nets
    netinfo = {}
    for name in sorted(nets):
        if name.startswith("unconnected-"):
            continue
        n = pcbnew.NETINFO_ITEM(board, name); board.Add(n); netinfo[name] = n
    pad_net = {}
    for name, nodes in nets.items():
        if name.startswith("unconnected-"):
            continue
        for ref, pin in nodes:
            pad_net[(ref, pin)] = name
    # ---- footprints
    fps = {}
    for ref, (value, fpid) in comps.items():
        if not fpid:
            continue
        fp = load_footprint(fpid)
        fp.SetReference(ref); fp.SetValue(value)
        for p in fp.Pads():
            name = pad_net.get((ref, p.GetNumber()))
            if name:
                p.SetNet(netinfo[name])
        board.Add(fp); fps[ref] = fp
    # ---- placement
    P = Placer(board, fps, nets, classes, pad_net, sch_positions(), class_geometry())
    rails_gate(islands=[i for r in P.regions for i in region_islands(r)])
    P.place_fixed()
    P.resolve_lanes()
    P.place_satellites()
    residual = P.relax()
    rail_placed, rail_pads, rail_none, rail_short = P.rail_vias()
    print(f"rail vias: {rail_placed} beside {rail_pads} pads on plane nets" + (f"; no room at {len(rail_none)}: {' '.join(rail_none[:12])}" if rail_none else "")
          + (f"; short at {len(rail_short)} (placed for wanted): {', '.join(rail_short[:12])}" if rail_short else ""))
    omitted, outlined, not_outlined, stepped_out = P.silkscreen()
    # ---- the lanes' copper
    for net, layer, width, (x0, y0), (x1, y1) in P.tracks:
        tr = pcbnew.PCB_TRACK(board); tr.SetStart(MM(x0, y0)); tr.SetEnd(MM(x1, y1)); tr.SetWidth(pcbnew.FromMM(width))
        tr.SetLayer(board.GetLayerID(layer)); tr.SetNet(netinfo[net]); tr.SetLocked(True); board.Add(tr)
    for net, x, y, dia, drill in P.vias:
        v = pcbnew.PCB_VIA(board); v.SetPosition(MM(x, y)); v.SetWidth(pcbnew.FromMM(dia)); v.SetDrill(pcbnew.FromMM(drill))
        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(netinfo[net]); v.SetLocked(True); board.Add(v)
    # ---- keep-outs: the corners (strips around the holes' pads), the isolation region and the lanes
    def rule_area(name, outline, layers=None, footprints=True, tracks=True, vias=True, fills=True):
        layers = layers or copper_layers()                             # every copper layer of the stackup unless told otherwise
        zn = pcbnew.ZONE(board); zn.SetIsRuleArea(True)
        zn.SetDoNotAllowTracks(tracks); zn.SetDoNotAllowVias(vias); zn.SetDoNotAllowZoneFills(fills); zn.SetDoNotAllowFootprints(footprints)
        zn.SetDoNotAllowPads(False)
        ls = pcbnew.LSET()
        for ln in layers: ls.addLayer(board.GetLayerID(ln))
        zn.SetLayerSet(ls); zn.SetZoneName(name)
        ol = zn.Outline(); ol.NewOutline()
        for x, y in outline: ol.Append(MM(x, y))
        board.Add(zn); return zn
    s, c = L.HOLE_KEEPOUT, L.HOLE_CLEAR_R
    for ref, (hx, hy) in L.HOLES.items():
        x0 = 0 if hx < W / 2 else W - s; y0 = 0 if hy < H / 2 else H - s
        strips = [(x0, y0, x0 + s, hy - c), (x0, hy + c, x0 + s, y0 + s), (x0, hy - c, hx - c, hy + c), (hx + c, hy - c, x0 + s, hy + c)]
        for i, (a, b, cc, d) in enumerate(strips):
            if cc - a > 0.1 and d - b > 0.1:
                rule_area(f"corner_{ref}_{i}", rect_outline(a, b, cc, d))
    for region in L.ISOLATION_REGIONS:                     # named for the .kicad_dru rules, which keep board nets out by class
        rule_area(region["name"], region["outline"], footprints=False, tracks=False, vias=False, fills=False)
    for name, (a, b, cc, d) in L.COPPER_VOIDS.items():        # no plane or pour on any layer (under a jack's magnetics): section 4; the pins' tracks pass
        rule_area(f"void_{name}", rect_outline(a, b, cc, d), footprints=False, tracks=False, vias=False, fills=True)
    for i, (name, box) in enumerate(P.lanes):              # parts stay out of a lane on both sides; its copper goes through
        rule_area(f"lane_{name}_{i}", rect_outline(*box), layers=("F.Cu", "B.Cu"), footprints=True, tracks=False, vias=False, fills=False)
    # ---- planes from the directives (drawn as single outlines: a zone outline with a hole does not fill), and each
    # isolated region's own island
    def copper_zone(name, net, layer, outline, holes=()):
        zn = pcbnew.ZONE(board); zn.SetLayer(board.GetLayerID(layer)); zn.SetNet(netinfo[net]); zn.SetZoneName(name)
        zn.SetMinThickness(pcbnew.FromMM(0.25)); zn.SetLocalClearance(pcbnew.FromMM(0.3))
        ol = zn.Outline(); ol.NewOutline()
        for x, y in outline: ol.Append(MM(x, y))
        for hole in holes:
            ol.NewHole()
            for x, y in hole: ol.Append(MM(x, y), 0, 0)
        board.Add(zn); return zn
    for plane in L.PLANES:                                 # a board-net plane may not enter an isolated region's creepage band (3.6)
        name, net, layer, outline = plane[:4]
        for region in L.ISOLATION_REGIONS:
            if not re.search(region["nets"], net):
                poly = pcbnew.SHAPE_POLY_SET(); poly.NewOutline()
                for x, y in outline: poly.Append(MM(x, y))
                for g in region["grown"]:
                    band = pcbnew.SHAPE_POLY_SET(); band.NewOutline()
                    for x, y in ((g[0], g[1]), (g[2], g[1]), (g[2], g[3]), (g[0], g[3])): band.Append(MM(x, y))
                    cut = pcbnew.SHAPE_POLY_SET(poly); cut.BooleanIntersection(band)
                    if cut.Area() > pcbnew.FromMM(0.01) * pcbnew.FromMM(0.01):
                        raise SystemExit(f"plane {name} ({net}) enters the creepage band of {region['name']} by {cut.Area() / 1e12:.2f} mm2: redraw it clear of the grown region {g}")
        zn = copper_zone(name, net, layer, outline)
        if len(plane) > 4:
            zn.SetAssignedPriority(plane[4])
    for region in L.ISOLATION_REGIONS:
        for isl in region_islands(region):
            copper_zone(*isl[:4])
    # ---- save, then the stackup (not reachable through the Python API) and the design rules
    path = os.path.join(OUT, f"{PROJECT}.kicad_pcb")
    pcbnew.SaveBoard(path, board, True)                # skip settings: the project file (net classes) is the schematic build's
    t = open(path, encoding="utf-8").read()
    layers = ['\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))', '\t\t\t(layer "F.Paste" (type "Top Solder Paste"))',
              '\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))']
    for item in L.STACKUP:
        if item[1] == "copper":
            layers.append(f'\t\t\t(layer "{item[0]}" (type "copper") (thickness {item[2]}))')
        else:
            layers.append(f'\t\t\t(layer "{item[0]}" (type "{item[1]}") (thickness {item[2]}) (material "FR4") (epsilon_r {item[3]}) (loss_tangent 0.02))')
    layers += ['\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))', '\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))',
               '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))', '\t\t\t(copper_finish "ENIG")', '\t\t\t(dielectric_constraints no)']
    stack = "\t\t(stackup\n" + "\n".join(layers) + "\n\t\t)\n"
    assert "(stackup" not in t
    total = sum(item[2] for item in L.STACKUP)                        # the board's thickness is the stackup's sum, not pcbnew's 1.6
    t = re.sub(r"(\(general\n\t\t\(thickness )[\d.]+", lambda m: m.group(1) + f"{total:.3f}".rstrip("0").rstrip("."), t, count=1)
    t = re.sub(r"(\n\t\(setup\n)", r"\1" + stack.replace("\\", "\\\\"), t, count=1)
    t = canonical(t)
    open(path, "w", encoding="utf-8").write(t)
    dru = "(version 1)\n"
    for region in L.ISOLATION_REGIONS:
        name = region["name"]; gap_ = region["gap"]
        isoc = "(" + " || ".join(f"A.NetClass == '{c}'" for c in region["classes"]) + ")"
        dru += f'''# {region.get("note", name)}: only the isolated classes' copper inside the region, and {gap_} mm creepage to board nets.
# (A board-net plane is kept out by the notch drawn in it, and the creepage rule catches a fill that strays in; a
# disallow on every zone that "intersects" the area would empty the whole ground plane, notch or not.)
(rule "{name}_keepout"
    (condition "A.intersectsArea('{name}') && !{isoc}")
    (constraint disallow track via))
(rule "{name}_no_board_zones"
    (condition "A.Type == 'Zone' && A.enclosedByArea('{name}') && !{isoc}")
    (constraint disallow zone))
# (a pad with no net, such as a relay's unused contact, has no side; it is inside the region by placement)
(rule "{name}_gap"
    (condition "{isoc} && !{isoc.replace('A.', 'B.')} && B.NetName != ''")
    (constraint clearance (min {gap_}mm)))
'''
    open(os.path.join(OUT, f"{PROJECT}.kicad_dru"), "w", encoding="utf-8").write(dru)
    # ---- the placement report
    hows = collections.Counter(("free" if how.startswith("free") else "ring", layer) for _, _, layer, how in P.order)
    sides = collections.Counter(P.side.values())
    print(f"wrote {path}: {len(fps)} footprints, {len(netinfo)} nets, {len(list(board.Zones()))} zones, {len(P.tracks)} lane tracks, {len(P.vias)} vias in {time.time() - t0:.0f} s")
    print(f"placement: {len(P.fixed)} fixed; top: {hows[('ring', 'F')]} in rings at their pins, {hows[('free', 'F')]} at the nearest free spot; "
          f"bottom: {hows[('ring', 'B')]} in rings under their pins, {hows[('free', 'B')]} at the nearest free spot; "
          f"{len(P.parked)} parked in SPARE{': ' + ' '.join(P.parked) if P.parked else ''}; {residual} residual overlap(s); "
          f"{sides['F']} parts on top, {sides['B']} on the bottom")
    # the anchored parts with their values beside their positions: an anchor written under the wrong designator (the
    # eFuse's position given to a level shifter) shows here, since DRC and the cities cannot see it
    print("anchors: " + ", ".join(f"{ref} {fps[ref].GetValue()} at ({x:g}, {y:g})" for ref, (x, y, *_) in sorted(L.ANCHORS.items()) if ref in fps))
    esd = []
    for ref, host, layer, how in P.order:
        if P.is_esd(ref):
            hb = P.box_of(host); hx, hy = (hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2
            hostnets = {net for _, net in P.pads_of[host] if net}
            pads = list(fps[ref].Pads())
            near = [p for p in pads if pad_net.get((ref, p.GetNumber())) in hostnets]
            farp = [p for p in pads if p not in near and not (pad_net.get((ref, p.GetNumber())) or "GND").endswith("GND")]
            dist = lambda ps: sum(math.hypot(p.GetPosition().x / 1e6 - hx, p.GetPosition().y / 1e6 - hy) for p in ps) / len(ps)
            facing = (dist(near) <= dist(farp) + 0.01) if (near and farp) else True
            esd.append(f"{ref}@{host} {layer}{how}{'' if facing else ' NOT FACING'}")
    print("ESD at their connectors, host-side pins toward it: " + ", ".join(esd))
    for line in P.lane_report:
        print("lane " + line)
    far = [(ref, host, how) for ref, host, _, how in P.order if how.startswith("free") and float(how[5:]) >= 8]
    if far:
        print("   far from their pin (mm): " + ", ".join(f"{ref}@{host} {how[5:]}" for ref, host, how in far))
    with open(os.path.join(OUT, "placement.txt"), "w", encoding="utf-8") as f:
        f.write("part   host   side  where (side+ring, or free@distance from the pin)\n")
        for ref, host, layer, how in P.order:
            f.write(f"{ref:6s} {host:6s} {layer:5s} {how}\n")
        f.write(f"\nrail vias (layout.md 4): {rail_placed} beside {rail_pads} pads on plane nets.\n")
        f.write(f"pads with no room beside them for any via ({len(rail_none)}; the router or the hand pass joins them to the rail):\n  "
                + " ".join(rail_none) + "\n")
        f.write(f"pads served short of their class's count or via (placed for wanted; {len(rail_short)}; the hand pass adds the rest):\n  "
                + "\n  ".join(rail_short) + "\n")
        if getattr(P, "hand_notes", None):
            f.write("\nhand placement (layout.md 2, item 4): what the hand-fixed parts and the lanes put in each other's way; the next hand pass's work\n  "
                    + "\n  ".join(P.hand_notes) + "\n")
        f.write(f"\ncities (layout.md 3.1): the schematic's islands with two or more parts, connectors, holes and ESD aside: each one's extent on\n"
                f"the board, and the members more than {L.ISLAND_SPREAD:g} mm from every other member (placed apart from their city)\n")
        apart = []
        hostof = {ref: host for ref, host, _, _ in P.order}
        def follows_island(r):                                     # connectors, holes and ESD at a connector (3.8) are not held to their island
            return r in P.fps and kind(r) != "conn" and r not in L.HOLES and not (P.is_esd(r) and kind(hostof.get(r) or r) == "conn")
        for iid, (sheet, refs, _) in P.islands.items():
            members = [r for r in refs if follows_island(r)]
            if len(members) < 2:
                continue
            pts = {r: (P.fps[r].GetPosition().x / 1e6, P.fps[r].GetPosition().y / 1e6) for r in members}
            dist = {r: min(math.hypot(x - x2, y - y2) for r2, (x2, y2) in pts.items() if r2 != r) for r, (x, y) in pts.items()}   # to the nearest fellow member
            xs = [x for x, _ in pts.values()]; ys = [y for _, y in pts.values()]
            far = sorted((r for r in members if dist[r] > L.ISLAND_SPREAD), key=natural); apart += far
            f.write(f"{iid:26s} {len(members):3d} parts  {max(xs) - min(xs):5.1f} x {max(ys) - min(ys):5.1f} mm" + (f"  apart: {' '.join(f'{r}@{dist[r]:.0f}' for r in far)}" if far else "") + "\n")
        narrow = []                                                # the voids: the gap between every two parts of different cities
        placed = [(r, layer, bx) for layer in ("F", "B") for r, bx in P.boxes[layer].items() if not P.exempt(r)]
        for i, (ra, la, ba) in enumerate(placed):
            for rb, lb, bb in placed[i + 1:]:
                if la != lb or P.same_city(ra, rb):
                    continue
                g = max(max(bb[0] - ba[2], ba[0] - bb[2]), max(bb[1] - ba[3], ba[1] - bb[3]))
                if g < L.CITY_GAP - 0.01:
                    narrow.append((g, ra, rb))
        narrow.sort()
        f.write(f"\nvoids (layout.md 3.1): every two parts of different cities on one side keep {L.CITY_GAP:g} mm; narrower gaps:\n" +
                ("".join(f"  {ra}-{rb} {g:.2f} mm\n" for g, ra, rb in narrow) if narrow else "  none\n"))
    n_isl = sum(1 for _, (s, refs, _) in P.islands.items() if sum(1 for r in refs if follows_island(r)) >= 2)
    print(f"cities: {n_isl} with two or more parts; parts placed apart from their city (> {L.ISLAND_SPREAD:g} mm from every other member): {' '.join(sorted(set(apart), key=natural)) or '-'}")
    print(f"voids: {L.CITY_GAP:g} mm between cities; gaps narrower than that: {len(narrow)}" + (f" ({' '.join(f'{ra}-{rb} {g:.1f}' for g, ra, rb in narrow[:8])})" if narrow else ""))
    majors_omitted = [r for r in omitted if kind(r) != 'passive']
    print(f"silkscreen: {len(fps) - len(L.HOLES) - len(omitted)} designators placed, {len(omitted)} omitted "
          f"({sum(1 for r in omitted if P.side[r] == 'B')} on the bottom)"
          f"{' (ICs/connectors among them: ' + ' '.join(majors_omitted) + ')' if majors_omitted else ''}; ICs/connectors labelled more than 1 mm out: {' '.join(stepped_out) or '-'}")
    print(f"   clusters outlined: {' '.join(outlined) or '-'}; dense, named by their host's designator only: {' '.join(not_outlined) or '-'}")
    errors = drc_gate(path)
    return errors


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--rails":            # the rails' pieces from the directives alone, for drawing them
        spec = importlib.util.spec_from_file_location("directives", sys.argv[2]); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        configure(mod, os.path.dirname(sys.argv[2]), "x", None)
        pieces = rail_pieces()
        for (net, layer), ps in sorted(pieces.items()):
            print(f"{net} {layer}: {len(ps)} piece(s)" + ("" if len(ps) == 1 else ": " + " | ".join(ps)))
        sys.exit(0)
    if len(sys.argv) < 4:
        raise SystemExit(__doc__.split("\n\n")[1])
    spec = importlib.util.spec_from_file_location("directives", sys.argv[1]); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    main(mod, sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
