# Layout

What a board needs before anyone places a part, and the order the work goes
in. The method is the one developed on the SBC development baseboard; each
step records the tool and the gate that go with it.

## 1. Entering layout: the project directives

Layout starts from a written set of directives, not from the schematic
alone. They are a short document (`docs/layout-directives.md` on the
project) that any engineer could place the board from, and a block diagram
drawn to scale that shows the same thing. Both exist before the first
footprint is placed, and both are kept current while the board changes.

The directives state:

- **Board outline**: dimensions, corner radius, and the origin the layout
  measures from. Thickness and stackup, naming the fab's standard stackup
  by name and layer-by-layer so controlled impedance can be computed.
- **Mounting holes**: size, position, whether plated, what they connect to,
  and the keep-out each one needs for its standoff or screw head.
- **Connector placement and direction**: every connector on an edge, with
  the edge, its order along the edge, which way it faces (toward the user,
  the target, the PSU, the bench) and how it is driven (cable, plug, ribbon),
  plus the usable length of each edge once the holes have taken their
  share. Inboard connectors say why they earn no edge.
- **Keep-outs**: areas no part or copper may enter: the strip inside each
  edge, the standoff pads, isolation gaps with their creepage distance, the
  antenna or magnetics regions, the label area.
- **Component side**: one side or two, and which parts, if any, go on the
  back.
- **Special considerations**: controlled-impedance pairs and their class,
  high-current paths and their class and width, the reference plane each
  signal class needs unbroken under it, thermal paths for the regulators,
  parts that must sit next to each other (a crystal and its IC, a switch and
  its receptacle), and the nets that may not share a plane (an isolated
  ground).

The schematic carries the same information where layout meets it: every
differential pair is named `_P`/`_N` and classed, every high-current net
carries a net class directive flag whose class name states the current
(`PSU_3A`), and the sheet notes say what the class means in copper. See
[schematic-style.md](schematic-style.md).

## 2. The board is generated, once

The first board file is produced by a generator (the baseboard's
`gen/pcb.py`, on KiCad's `pcbnew` Python module) from the schematic, the
project file and the directives, so the directives are honoured exactly and
the start is reproducible:

1. The outline (with its corner radius), the mounting holes and the keep-out
   rule areas come straight from the directives; the stackup is written into
   the board, layer by layer, with the fab's dielectric thicknesses.
2. Every footprint is loaded from the libraries the schematic names and its
   pads are given the nets from the schematic's XML netlist export, so the
   board starts with a complete ratsnest and the net classes the project
   file assigns.
3. Edge connectors are placed on their edges in the stated order, rotated so
   that they mate outward, flush with the edge or on the footprint's own
   "PCB Edge" mark, with a stated gap between bodies and a stated distance
   from each corner. Measure the footprints before trusting the spec's body
   widths: an RJ45 is 22 mm, not 16.
4. Parts that straddle an isolation barrier (a relay, an opto-coupler) are
   placed by hand in the directives, and the barrier polygon is drawn
   through them between the two sides' pins.
5. Every other part joins the group of the IC it shares the most signal nets
   with, and each group is packed into its rectangle (tallest first, in
   rows). The rectangles are sized from the parts' courtyards; the generator
   reports a group that does not fit instead of spilling it over a neighbour.
6. Planes are drawn as zones (the ground plane on L2, an isolated ground
   island where there is one), stopping a millimetre short of the edge, and
   a `.kicad_dru` carries the rules the directives need (nothing but the
   isolated class inside the isolation area; its creepage clearance).
7. The gate is `kicad-cli pcb drc --severity-all` read by severity: zero
   errors other than unconnected items (the board is unrouted), with the
   silkscreen warnings left for the layout work to clear. An error inside a
   library footprint (a connector's own hole-to-pad spacing under the board's
   constraint) is checked against the fab's minimums and recorded in the
   README, not silenced by loosening the constraint. The board's clearance
   and hole constraints are the fab's published minimums, not KiCad's
   defaults: a 0.5 mm pitch part fails KiCad's 0.2 mm default clearance.

From there the board file is the source of truth. Placement is refined and
routing is done in KiCad; the generator is not run again over a board that
has been edited. The schematic generator stays usable, because its derived
UUIDs keep the footprints linked (see
[kicad-generation.md](kicad-generation.md)).

## 3. Placement and routing order

The order the baseboard's layout follows; each step is checked against the
directives before the next starts.

1. **Mechanical first.** The edge connectors against the mechanical drawing
   or a printed 1:1 outline: edge, order, direction, overhang, the holes'
   clearance. The terminal blocks' orientation is checked in the 3D view,
   since a footprint does not say which way its plug enters.
2. **Barriers and isolation.** Any isolated region is settled next: the parts
   that straddle it, its ground island, the creepage gap, and the DRC rule
   that enforces it, so nothing routed later can cross it by accident.
3. **The pairs.** Controlled-impedance pairs are placed and routed before
   anything else inboard: receptacle, ESD array and controller in a line,
   the pair on the top layer over the unbroken ground plane, no stubs,
   lengths matched.
4. **Power.** Switching regulators get their loops (input capacitor, switch,
   catch diode, inductor, output capacitor) tight and their thermal copper;
   the high-current classes get their pours and their via pairs; then the
   rails to each block.
5. **Everything else**, block by block, each block's decoupling against its
   pins before its signals leave it. Crystals last within their block, with
   nothing routed under them.
6. **Silkscreen and fabrication**: reference designators readable with the
   connectors fitted, the markings the directives call for (pin 1, polarity,
   connector names), the stackup and controlled-impedance notes in the fab
   drawing.

The gate at every step is DRC at every severity: zero errors, and the
warnings explained. Unconnected items go to zero at step 5.
