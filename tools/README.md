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

## harvest.py: measured practice (layout.md section 9)

    tools/harvest.py --mirror reference-boards/ --board OUT_DIR/PROJECT.kicad_pcb --csv docs/reference-boards.csv
    tools/harvest.py FILE.kicad_pcb

measures every board under the mirror and prints the reference medians next
to the project's own numbers (decoupling distance and side, ESD and crystal
distance, pad gaps, edge clearance, bottom-side shares, tuck and
through-hole margins, designator visibility and size, track widths). The
baseboard's `scripts/reference-boards.txt` lists the mirror's sources.
