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
            "ESD_VALUES": ("USBLC", "PESD", "ESDA", "TPD", "SRV05", "IP42", "TVS"), "FIXED": {}, "ANCHORS": {}}


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


def kind(ref):
    p = prefix(ref)
    return "conn" if p == "J" else "ic" if p in ("U", "K") else "passive"


class Placer:
    """Places the footprints of a board by the rules in ecad-standards/layout.md section 3."""

    def __init__(self, board, fps, nets, classes, pad_net, schpos, geometry):
        self.board, self.fps, self.nets, self.pad_net, self.schpos = board, fps, nets, pad_net, schpos
        self.classes, self.geometry = classes, geometry
        self.weight = {n: (5.0 if c in L.CURRENT_CLASSES else 2.0 if c in L.PAIR_CLASSES else 1.0) for n, c in classes.items()}
        self.area = {ref: (lambda b: (b[2] - b[0]) * (b[3] - b[1]))(bbox_mm(fp)) for ref, fp in fps.items()}
        self.values = {ref: fp.GetValue() for ref, fp in fps.items()}
        self.pads_of = {ref: [(p.GetNumber(), pad_net.get((ref, p.GetNumber()))) for p in fp.Pads()] for ref, fp in fps.items()}
        self.plane = {n for n, nodes in nets.items() if len(nodes) > 8}
        self.loop = {n for n, nodes in nets.items() if any(prefix(r) == "L" for r, _ in nodes)}   # an inductor's nets: a switching loop
        self.boxes = {"F": {}, "B": {}}                   # side -> ref -> courtyard box of every placed part
        self.side = {}
        self.under = {}                                   # a top part -> the boxes it denies the bottom (THT pads, EP via field, a crystal)
        self.lanes = []                                   # (name, box) corridors kept free of parts on both sides
        self.tracks = []                                  # (net, layer, width, p0, p1) the lanes' copper
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
            if any(overlap(b, c, 0.3) for c in self.locked):
                return "a connector"
        else:
            for other, blocks in self.under.items():
                if other != ref and any(overlap(b, c) for c in blocks):
                    return f"under {other}"
        for other, ob in self.boxes[layer].items():
            if other != ref and other not in ignore and overlap(b, ob, L.PACK_MARGIN):
                return other
        return None

    # ---- the fixed parts
    def place_fixed(self):
        for ref, (x, y) in L.HOLES.items():
            self.pose(ref, x, y, 0); self.fixed.add(ref)
        for table in (L.CONNECTORS, L.FIXED, L.ANCHORS):
            for ref, (x, y, rot) in table.items():
                if ref in self.fps:
                    self.pose(ref, x, y, rot); self.fixed.add(ref)
        self.locked = [self.boxes["F"][ref] for ref in L.CONNECTORS if ref in self.fps]
        problems = []
        refs = sorted(self.fixed)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if overlap(self.boxes["F"][a], self.boxes["F"][b], L.PACK_MARGIN):
                    problems.append(f"{a} and {b} overlap")
        for ref in L.ANCHORS:
            if ref not in self.fps:
                problems.append(f"{ref} is anchored but not in the schematic"); continue
            if not self.allowed(ref, self.boxes["F"][ref]):
                problems.append(f"{ref} stands in a keep-out, the edge zone or on the wrong side of an isolation barrier")
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

    def resolve_lanes(self):
        """The lanes' legs from the directives: their tracks, and the corridors kept free of parts (which stop at
        the courtyards of the parts the lane joins)."""
        problems = []
        for name, lane in L.LANES.items():
            net = self.net_named(lane["net"])
            track, clear = self.geometry.get(self.classes.get(net, "Default"), self.geometry["Default"])
            hw = track / 2 + clear + L.LANE_MARGIN
            pts, ends = [], []
            for item in lane["path"]:
                if item[0] in ("x", "y"):
                    val = self.pad_xy(*item[1]) if isinstance(item[1], tuple) else (item[1], item[1])
                    px, py = pts[-1]
                    pts.append((val[0], py) if item[0] == "x" else (px, val[1]))
                elif isinstance(item[0], str):
                    if self.pad_net.get(item) != net:
                        problems.append(f"lane {name}: pad {item} is not on {net}")
                    pts.append(self.pad_xy(*item)); ends.append(item[0])
                else:
                    pts.append(tuple(item))
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if abs(x0 - x1) > 1e-6 and abs(y0 - y1) > 1e-6:
                    problems.append(f"lane {name}: the leg {x0, y0} to {x1, y1} is not axis-aligned"); continue
                self.tracks.append((net, lane["layer"], track, (x0, y0), (x1, y1)))
                box = [min(x0, x1) - hw, min(y0, y1) - hw, max(x0, x1) + hw, max(y0, y1) + hw]
                horizontal = abs(y0 - y1) < 1e-6
                for ref in ends:                                   # the keep-out stops at the parts the lane joins
                    cb = self.box_of(ref)
                    if not overlap(tuple(box), cb):
                        continue
                    lo, hi = (0, 2) if horizontal else (1, 3)
                    if cb[lo] <= box[lo] and cb[hi] >= box[hi]:
                        box = None; break
                    if cb[lo] <= box[lo]:
                        box[lo] = cb[hi]
                    elif cb[hi] >= box[hi]:
                        box[hi] = cb[lo]
                if box and box[2] - box[0] > 0.1 and box[3] - box[1] > 0.1:
                    self.lanes.append((name, tuple(box)))
        for name, box in self.lanes:                                # nothing fixed but the lane's ends may stand in it
            for ref in self.fixed:
                if ref not in L.HOLES and overlap(box, self.boxes["F"][ref]) and ref not in {e for lane in L.LANES.values() for e in (lane["path"][0][0], lane["path"][-1][0])}:
                    problems.append(f"lane {name} runs through {ref}")
        if problems:
            raise SystemExit("the directives' lanes are not consistent:\n  " + "\n  ".join(problems))

    # ---- hosts
    def best_host(self, ref):
        """(host, attachment-point resolver, the host's net the part should face, priority) for an unplaced part,
        or None. Recomputed every round: a part whose partner on a two-node net is not down yet scores low
        on its planes alone, and is picked up by the partner once that is placed."""
        scores, shared = collections.Counter(), collections.defaultdict(list)
        for num, net in self.pads_of[ref]:
            if not net or net.startswith("unconnected-"):
                continue
            nodes = self.nets[net]
            w = (0.15 if net in self.plane else 1.0) * self.weight.get(net, 1.0) / len(nodes)
            for hr, hp in nodes:
                if hr == ref or hr not in self.side or hr in L.HOLES:
                    continue
                bonus = {"conn": 2.5, "ic": 1.3, "passive": 1.0}[kind(hr)] * (1.5 if self.same_sheet(hr, ref) else 1.0)
                scores[hr] += w * bonus; shared[hr].append((hp, net))
        if not scores:
            return None
        specific = {hr: [(hp, net) for hp, net in pins if net not in self.plane] for hr, pins in shared.items()}
        specific = {hr: pins for hr, pins in specific.items() if pins}
        if specific:
            host = max(specific, key=lambda hr: scores[hr])
            pts = [self.fps[host].FindPadByNumber(hp).GetPosition() for hp, _ in specific[host]]
            x = sum(p.x for p in pts) / len(pts) / 1e6; y = sum(p.y for p in pts) / len(pts) / 1e6
            a = self.area[ref]
            tier = (4 if a >= L.BIG_AREA else 2) if kind(host) != "passive" else 1
            if self.is_esd(ref) and kind(host) == "conn":
                tier = 6                                           # ESD first of all: the connector's signal pins are its
            return host, (lambda: (x, y)), specific[host][0][1], (tier, a if tier > 1 else 0, scores[host])
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
        waiting = self.has_specific(ref)
        a = self.area[ref]
        tier = 0 if waiting else 5 if a < L.SMALL_AREA else 3      # small decoupling first of all; bulk after the big parts on the pins
        return host, resolve, rail, (tier, a if tier > 0 else 0, scores[host])

    def orientation(self, ref, hostnet, side, layer, host=None):
        """Two-pin parts turn so the pad carrying the host's net faces the host; a flow-through part (an ESD array
        with the connector's pair on one pin row and the IC's on the other) turns its host-side row to the host;
        others stay upright."""
        pads = list(self.fps[ref].Pads())
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
            return False
        self.pose(ref, cx - (ob[0] + ob[2]) / 2, cy - (ob[1] + ob[3]) / 2, rot, layer)
        return True

    def place_in_rings(self, ref, host, point, hostnet, layer, rings=None):
        hb = self.box_of(host)
        base = -L.BOTTOM_TUCK if (layer == "B" and self.side[host] == "F") else L.RING_GAP   # under the host's pin row, or beside it
        d = {"L": point[0] - hb[0], "R": hb[2] - point[0], "T": point[1] - hb[1], "B": hb[3] - point[1]}
        for side in sorted(d, key=d.get):                      # nearest side first, the others if it is full
            rot = self.orientation(ref, hostnet, side, layer, host)
            ob = self.origin_box(ref, rot, layer)
            w, h = ob[2] - ob[0], ob[3] - ob[1]
            normal, along = (w, h) if side in ("L", "R") else (h, w)
            t = point[1] if side in ("L", "R") else point[0]
            lo, hi = (hb[1], hb[3]) if side in ("L", "R") else (hb[0], hb[2])
            ringlist = self.rings[(host, side, layer)]
            for slide, ri in [(s, r) for s in L.RING_SLIDES for r in range(rings or L.RINGS)]:
                if ri >= len(ringlist):
                    ringlist.append({"depth": 0.0, "members": []})
                ring = ringlist[ri]
                if ring["members"] and normal > ring["depth"] + 1e-6 and any(rr["members"] for rr in ringlist[ri + 1:]):
                    continue                                       # fatter than this ring, and the outer rings are occupied
                inner = base + sum(rr["depth"] for rr in ringlist[:ri]) + L.RING_GAP * ri
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
                    if self.try_box(ref, rot, cx, cy, layer):
                        ring["members"].append((ref, iv))
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
            ref, (host, resolve, hostnet, _) = best
            point = resolve()
            layer = "B" if self.bottom_ok(ref, host) else "F"
            how = None
            if layer == "B" and not self.has_specific(ref):        # decoupling stays on its IC's side unless its first rings are full
                how = self.place_in_rings(ref, host, point, hostnet, "F", rings=2)
            how = how or self.place_in_rings(ref, host, point, hostnet, layer)
            if not how and layer == "B":                           # no ring under the pin: near it on the bottom, else beside it on top
                how = self.place_nearest(ref, point, hostnet, "B", radius=8.0) or self.place_in_rings(ref, host, point, hostnet, "F")
            how = how or self.place_nearest(ref, point, hostnet, layer) or (layer == "B" and self.place_nearest(ref, point, hostnet, "F"))
            if ref == DEBUG_REF:
                print(f"{ref}: host {host} at {point[0]:.1f},{point[1]:.1f} facing {hostnet}, side {layer}: {how}; placed as number {len(self.order) + 1}; "
                      f"spots rejected by: {dict(self.debug.most_common(8))}")
            if how:
                self.order.append((ref, host, self.side[ref], how))
            else:
                self.parked.append(ref); self.order.append((ref, host, "-", "parked"))
                self.side[ref] = "F"                               # keeps the loop moving; parked below
            unplaced.remove(ref)
        self.parked += unplaced
        for ref in self.parked:                                    # the nearest free spot to the spare area, anywhere
            self.side.pop(ref, None)
            if not self.place_nearest(ref, L.SPARE, None, "F", radius=200.0):
                ob = self.origin_box(ref, 0); self.pose(ref, L.SPARE[0] - ob[0], L.SPARE[1] - ob[1], 0)

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


def main(directives=None, out=None, project=None, house_fp=None):
    if directives is not None:
        configure(directives, out, project, house_fp)
    t0 = time.time()
    comps, nets, classes = netlist()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
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
    P.place_fixed()
    P.resolve_lanes()
    P.place_satellites()
    residual = P.relax()
    omitted, outlined, not_outlined, stepped_out = P.silkscreen()
    # ---- the lanes' copper
    for net, layer, width, (x0, y0), (x1, y1) in P.tracks:
        tr = pcbnew.PCB_TRACK(board); tr.SetStart(MM(x0, y0)); tr.SetEnd(MM(x1, y1)); tr.SetWidth(pcbnew.FromMM(width))
        tr.SetLayer(board.GetLayerID(layer)); tr.SetNet(netinfo[net]); board.Add(tr)
    # ---- keep-outs: the corners (strips around the holes' pads), the isolation region and the lanes
    def rule_area(name, outline, layers=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), footprints=True, tracks=True, vias=True, fills=True):
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
    for name, net, layer, outline in L.PLANES:
        copper_zone(name, net, layer, outline)
    for region in L.ISOLATION_REGIONS:
        if region.get("island"):
            name, net, layer, outline = region["island"]; copper_zone(name, net, layer, outline)
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
    print(f"wrote {path}: {len(fps)} footprints, {len(netinfo)} nets, {len(list(board.Zones()))} zones, {len(P.tracks)} lane tracks in {time.time() - t0:.0f} s")
    print(f"placement: {len(P.fixed)} fixed; top: {hows[('ring', 'F')]} in rings at their pins, {hows[('free', 'F')]} at the nearest free spot; "
          f"bottom: {hows[('ring', 'B')]} in rings under their pins, {hows[('free', 'B')]} at the nearest free spot; "
          f"{len(P.parked)} parked in SPARE{': ' + ' '.join(P.parked) if P.parked else ''}; {residual} residual overlap(s); "
          f"{sides['F']} parts on top, {sides['B']} on the bottom")
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
    far = [(ref, host, how) for ref, host, _, how in P.order if how.startswith("free") and float(how[5:]) >= 8]
    if far:
        print("   far from their pin (mm): " + ", ".join(f"{ref}@{host} {how[5:]}" for ref, host, how in far))
    with open(os.path.join(OUT, "placement.txt"), "w", encoding="utf-8") as f:
        f.write("part   host   side  where (side+ring, or free@distance from the pin)\n")
        for ref, host, layer, how in P.order:
            f.write(f"{ref:6s} {host:6s} {layer:5s} {how}\n")
    majors_omitted = [r for r in omitted if kind(r) != 'passive']
    print(f"silkscreen: {len(fps) - len(L.HOLES) - len(omitted)} designators placed, {len(omitted)} omitted "
          f"({sum(1 for r in omitted if P.side[r] == 'B')} on the bottom)"
          f"{' (ICs/connectors among them: ' + ' '.join(majors_omitted) + ')' if majors_omitted else ''}; ICs/connectors labelled more than 1 mm out: {' '.join(stepped_out) or '-'}")
    print(f"   clusters outlined: {' '.join(outlined) or '-'}; dense, named by their host's designator only: {' '.join(not_outlined) or '-'}")
    # ---- DRC gate
    rep = os.path.join(OUT, "drc.txt")
    # zones are filled for the check (not saved), so what the planes connect does not count as unrouted
    subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--refill-zones", "--format", "report", "-o", rep, path], capture_output=True, text=True)
    txt = open(rep, encoding="utf-8").read()
    txt = re.sub(r"Created on \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "Created on 2026-10-03T00:00:00", txt, count=1)
    # the DRC lists violations in the order its threads find them: sort each section's entries
    def sort_section(m):
        entries = re.split(r"\n(?=\[)", m.group(0))
        return entries[0] + "".join("\n" + e for e in sorted(entries[1:]))
    txt = re.sub(r"^\*\* Found[^\n]*\n(?:\[.*?\n)+?(?=\n\*\*|\Z)", lambda m: sort_section(m), txt, flags=re.M | re.S)
    # the GCT USB-C footprint has two pads at each of four positions (A1/B12, A4/B9, A9/B4, A12/B1); the DRC
    # names either one: name the A side
    txt = re.sub(r"Pad (B12|B9|B4|B1) \[", lambda m: "Pad " + {"B12": "A1", "B9": "A4", "B4": "A9", "B1": "A12"}[m.group(1)] + " [", txt)
    open(rep, "w", encoding="utf-8").write(txt)
    kinds = collections.Counter()
    for m in re.finditer(r"^\[(\w+)\][^\n]*\n\s*(?:Rule: [^;]*; )?(\w+)", txt, re.M):
        kinds[(m.group(1), m.group(2))] += 1
    errors = {k: v for (k, sev), v in kinds.items() if sev == "error" and k != "unconnected_items"}
    warnings = {k: v for (k, sev), v in kinds.items() if sev != "error" and k != "unconnected_items"}
    unconnected = sum(v for (k, sev), v in kinds.items() if k == "unconnected_items")
    print(f"DRC: {sum(errors.values())} error(s) {errors}; {unconnected} unconnected (unrouted); warnings {warnings}")
    return errors


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__.split("\n\n")[1])
    spec = importlib.util.spec_from_file_location("directives", sys.argv[1]); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    main(mod, sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
