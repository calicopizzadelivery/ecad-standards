# tools: the standard's implementation

Scripts that carry out what [layout.md](../layout.md) says, so a new project
follows the rules by running them rather than by rereading them. Projects
take this repository as a git submodule (the baseboard: `hardware/kicad/standards`)
and call the tools from a thin wrapper of their own.

## placer.py: the placement engine (layout.md sections 2, 3, 6, 9)

    tools/placer.py DIRECTIVES.py OUT_DIR PROJECT [HOUSE_FOOTPRINTS_DIR]

reads the project's schematic netlist (`kicad-cli`), its project file (net
classes) and its directives module, and writes `OUT_DIR/PROJECT.kicad_pcb`,
`PROJECT.kicad_dru`, `placement.txt` and the DRC report `drc.txt`. Edge
connectors are locked, ICs anchored, lanes laid, every other part placed at
the pin it serves, small parts on the bottom where the directives allow,
designators placed or omitted by the silkscreen rule; the output is
byte-identical between runs. Run once per placement pass.

The directives module is the contract. Required:

| Name | Meaning |
|---|---|
| `BOARD`, `RADIUS` | outline size (mm) and corner radius |
| `HOLES`, `HOLE_KEEPOUT` | mounting holes `{ref: (x, y)}` and the corner square kept empty around each |
| `CONNECTORS` | `{ref: (x, y, rot)}`, locked |
| `ANCHORS`, `FIXED` | `{ref: (x, y, rot)}` ICs placed by flow (validated), parts fixed by hand (not validated, for parts that straddle a barrier) |
| `SPARE` | `(x, y)` where a part that fits nowhere is parked and reported |
| `STACKUP` | layers for the board file: `("F.Cu", "copper", 0.035)` and `("dielectric 1", "prepreg", 0.3048, 4.6)` |

Optional, with the standard's default in `placer.DEFAULTS` when absent:
`EDGE_ZONE` (3.0), `HOLE_CLEAR_R`, `PACK_MARGIN` (0.15), `RING_GAP` (0.15),
`RINGS`, `RING_REACH`, `RING_SLIDES`, `SEARCH_RADIUS`, `BIG_AREA`,
`SMALL_AREA`, `LANES` (corridors: `{name: {"net", "layer", "path": [pad,
("x"|"y", value or pad), ...]}}`), `LANE_MARGIN`, `ISOLATION_REGIONS`
(`{"name", "outline", "rects", "grown", "nets" regex, "classes", "gap",
"island": (zone name, net, layer, outline)}`), `PLANES` (`(name, net, layer,
outline)`, single outlines, notched rather than holed), `CURRENT_CLASSES`,
`PAIR_CLASSES`, `BOTTOM_MAX_AREA`, `BOTTOM_NEVER_CLASSES`, `BOTTOM_TUCK`
(1.75), `THT_MARGIN` (0.5), `EP_MARGIN`, `REFDES_SIZES` ((0.8, 0.7)),
`ESD_VALUES`. The baseboard's `hardware/kicad/gen/layout.py` is the worked
example, and its `docs/layout-directives.md` the prose the module encodes.

### Lanes

`LANES` is a dict of corridors. A single-net lane:

    "PSU_VP": {"net": "PSU_VP", "layer": "F.Cu", "width": 2.0,     # width optional: the class's
               "path": [("J18", "1"), ("y", 65.0), ("x", ("K803", "6")), ("K803", "6")]}

`path` items: a pad `(ref, number)`; a leg `("x", value)` or `("y", value)`
moving along one axis to a coordinate or to another pad's coordinate; a
layer change `("layer", "B.Cu")` at the current point (a via). A pair lane:

    "HUB_DN4": {"pair": "HUB_DN4", "path": [("U408", {"P": "1", "N": "3"}), ("y", 50.0),
                ("x", ("U402", {"P": "9", "N": "8"})), ("U402", {"P": "9", "N": "8"})]}

names the nets `<pair>_P` / `<pair>_N`; an end is `(ref, {"P": pad, "N": pad})`,
or `("pads", {"P": (ref, pad), "N": (ref, pad)})` for two parts (series
resistors), or with pad lists for a receptacle's doubled pads
(`{"P": ["A6", "B6"], "N": ["A7", "B7"]}`, bridged behind the row); an end
may carry a shift of its centreline, `(ref, {...}, (dx, dy))`, where two
pairs leave adjacent pins. The members are laid at the class's differential
width and gap, matched in length, and the lane is refused if the P member
would cross the N member between its ends (the array's channels then swap
in the schematic, layout.md 3.8). Tunables: `ESCAPE_WIDTH`, `ESCAPE_LENGTH`,
`THT_STUB`, `DIRECT_STUB`, `CHAMFER`, `VIA_PAIR_OFFSET`, `BRIDGE_DEPTHS`,
`MATCH_TOLERANCE`, `BUMP_HEIGHT`, `BUMP_WIDTH`, `ISLAND_GAP` (6.0: symbol
bodies closer than this on the sheet are one island), `ISLAND_REACH` (None:
a wire joins two symbols whatever their distance; a number caps the body
distance it may bridge), `ISLAND_SPREAD` (10.0: a member farther than this
from every other member of its city on the board is reported), `CITY_GAP`
(2.0: the void between any two cities' parts, both sides), `REGULATORS`
(`{ref: {"layout": name, "sw": "L"|"R"|"T"|"B", "sw_pin": name or pad,
"in_pin": ...}}`: the regulators' cities go first; with a `layout` the
template below places them, else the inductor and diode go on the `sw`
side), `LAYOUTS` (`{name: {"source": str, "top_only": bool, "pins": {pin
name: (side, [kinds outward, "D^" = along the side], start ring)},
"inductor_out": (side, [kinds])}}`: a datasheet figure transcribed in the
footprint's own frame; sides turn with the anchor's rotation), `TEMPLATED`
(`{ref: layout name}`: an IC that is not a regulator placed from a figure),
`SIDES` (`{ref: "F"|"B"}`: a part's side by directive, section 3.7; the parts
it hosts follow it, except indicator LEDs with their series resistors, ESD
and connectors), `RAIL_VIAS` (`{class: count}`: vias beside each SMD pad on a
plane net, one by default, section 4),
`COPPER_VOIDS` (`{name: (x0, y0, x1, y1)}`: no plane or pour on any layer,
tracks and vias pass). Read by `copper.py` rather than the placer:
`FLOODS`, `STITCH`, `STITCH_VIA`.

`PLANES` entries take an optional fifth item, the zone priority, so rail
regions on one layer carve a base plane under them.

## autoroute.py: the rest of the routing (layout.md section 2, step 10)

    tools/autoroute.py OUT_DIR/PROJECT.kicad_pcb /path/to/freerouting [--passes 30] [--threads 1] [--max-width 2.0]

exports the board to Specctra (locked tracks and vias fixed, planes as
planes), runs FreeRouting headless, imports the session and saves at once,
then, in a fresh process (`--post BOARD`: after the import even a reloaded
board can come back as a bare SWIG pointer), removes whatever the router laid
on a pair net (the lanes carry the pairs), floors the router's sub-minimum
stubs, writes the board in canonical order and runs the DRC gate. The class
widths given to the router are capped at `--max-width` (2.0 mm): the 6 A
class runs on the rails through the rail vias. Every clearance in the
export is 10 µm over the project's, so the router's rounding never comes
out under DRC's figure; a router track the gate still faults is removed in
a fresh process (`--post3`) and the gate runs again, the connection joining
the hand pass. One thread on purpose: FreeRouting's multi-threaded
optimiser produces clearance violations. Run it once after the placer; the
board file is the source of truth from then on.

## copper.py: the copper after routing (layout.md section 2, step 11)

    tools/copper.py OUT_DIR/PROJECT.kicad_pcb gen/layout.py [--no-stitch]

draws the ground floods of the directives' `FLOODS` (`(name, net, layer,
outline)`: both outer layers from the plane's notched outline, the isolated
region's own ground inside it) at priority 0 with thermal reliefs, then
places the stitching vias of `STITCH` (a list of `{"net", "pitch",
"margin"` from the edge, `"keep_out"` rectangles, or `"inside"` rectangles
for a region's own ground`}`; `STITCH_VIA` the via size, 0.6 / 0.3 by
default): on the grid point or at the nearest clear spot within half a
pitch, clear of pads, tracks and vias by 0.3 mm, out of every via
keep-out, never within the fill's minimum width of another net's zone
edge; then it fills the zones and drops any via that cut a new island off
another net's zone, and any via the filled floods reach on fewer than two
layers (that sweep runs in its own process: removing board-owned vias and
then touching zones corrupts pcbnew's Python proxies); a pour island that
holds a pad of the net but no via gets one inside it where there is room.
Rule areas that forbid pours (the copper voids) are via keep-outs too.
Idempotent: a flood that exists by name is kept, and a
via of the net within half a pitch of a grid point counts as that point, so
it reruns over a board whose routing has changed. Writes the board in
canonical order and runs the DRC gate. It is the last generated step before
the hand pass.

## harvest.py: measured practice (layout.md section 9)

    tools/harvest.py --mirror reference-boards/ --board OUT_DIR/PROJECT.kicad_pcb --csv docs/reference-boards.csv
    tools/harvest.py FILE.kicad_pcb

measures every board under the mirror and prints the reference medians next
to the project's own numbers (decoupling distance and side, ESD and crystal
distance, pad gaps, edge clearance, bottom-side shares, tuck and
through-hole margins, designator visibility and size, track widths). The
baseboard's `scripts/reference-boards.txt` lists the mirror's sources.
