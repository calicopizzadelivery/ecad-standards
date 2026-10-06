# Layout

Rules for placing and routing a board, and the order the work goes in. The
method is the one developed on the SBC development baseboard; each rule
says what it inherits, what it measures, and the gate that checks it.

## 0. Parent standards

This standard inherits a public, maintained design-rule standard rather
than restating one. Where the parent and this document differ, this
document is a tailoring and says so.

- **Parent: ECSS-Q-ST-70-12C Rev.1, *Design rules for printed circuit
  boards*** (European Cooperation for Space Standardization, published
  30 April 2025; the Oct 2024 DIR1 draft is the text read for this
  document, so clause numbers are quoted from it). It is free to download
  from ecss.nl after registration, is kept current, and covers everything
  a layout needs numbers for: build-up and materials (6, 7.1–7.3), track
  width and spacing with their manufacturing tolerances (7.4), pad design
  (7.5), copper planes (7.6), thermal rules (10), current rating on the
  IPC-2152 model (13.6), voltage rating and insulation distance (13.8–13.10),
  controlled impedance (13.11), zone management for digital, analog and
  mixed boards (13.12–13.14), and design for assembly: distances from copper
  and components to edges, holes and screws (14.3, Tables 14-1 and 14-2)
  and SMT land patterns (14.5). We inherit all of it. Being a space
  standard it is conservative; that is the right default for a bench tool
  that will be rebuilt rarely.
- **Referenced through the parent**: IPC-2221B/2222 (generic and rigid
  board design), IPC-2152 (current carrying capacity, which supersedes the
  IPC-2221 charts), IPC-7351 (land patterns, which KiCad's library
  follows). These are paid documents; the parent carries the numbers we use
  from them.
- **Fallback**: MIL-STD-275E, *Printed wiring for electronic equipment*
  (US DoD, 1984, public domain, superseded by IPC-2221). Cited only where
  the parent is silent.
- **The fab's capability sheet** (for the baseboard, Advanced Circuits'
  standard process) sets the manufacturing minimums the DRC enforces. The
  stricter of the fab and the parent applies.

Tailorings, all recorded in the project's directives:

| Parent clause | Rule there | Here |
|---|---|---|
| 7.6 a, b | planes carry a venting grid | solid planes; the grid is for vacuum outgassing |
| 14.3.2 c | no components within 5 mm of the edge | edge connectors are at the edge by design; other parts keep 3 mm, the assembler consulted |
| 14.3.1 Table 14-1 A1 | tracks 0.7 mm from the edge | kept; planes and pads 0.25 mm (A6), as the fab allows |
| 13.6.2 b | tracks ≤ 5 °C rise preferred | 10 °C (13.6.2 a) is the design figure; the classes' widths come from it |
| 14.3.2 Table 14-2 B1 | 0.6 mm between bodies | met as KiCad courtyards (0.25 mm each side) plus a 0.25 mm packing margin, so two parts' silk outlines (drawn at the courtyard edge) never touch |

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
  share. Inboard connectors say why they earn no edge. Body widths are
  measured from the footprints, not estimated.
- **Keep-outs**: areas no part or copper may enter: the strip inside each
  edge, the standoff pads, isolation gaps with their creepage distance, the
  antenna or magnetics regions, the label area.
- **Component sides**: what stays on top and what may go to the bottom, by
  the rule in section 3.7, and the height the standoffs allow underneath.
- **Lanes**: a corridor reserved for each high-current path, and for each
  controlled-impedance pair once the pairs are placed: the pads it joins,
  its legs, its layer. Its width follows from the class (section 5).
- **Special considerations**: controlled-impedance pairs and their class,
  high-current paths and their class and width, the reference plane each
  signal class needs unbroken under it, thermal paths for the regulators,
  parts that must sit next to each other (a crystal and its IC, a switch and
  its receptacle), and the nets that may not share a plane (an isolated
  ground).
- **The ICs' own layout guidelines**, collected as in section 3.2.

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
   from each corner, and from then on **locked**: the directives carry their
   positions, and a later placement pass moves everything but them. The
   mating direction of a horizontal connector footprint follows one rule
   across KiCad's library: **the solder pins sit at the rear, so the mating
   face is the end of the body farthest from the pad rows**; a pin header
   mates where its pins point. The 3D view is the check: a jack facing a
   mounting hole cannot be plugged in.
4. Parts that straddle an isolation barrier (a relay, an opto-coupler) are
   placed by hand in the directives, and the barrier polygon is drawn
   through them between the two sides' pins. The ICs are **anchored** by
   hand too, by flow (section 3.1), each with a line saying why it is there;
   the generator refuses an anchor table whose parts overlap, stand in a
   keep-out or on the wrong side of the barrier. The anchors are the knobs
   of the placement: when a block comes out cramped, its anchor moves, not
   its parts.
5. **Lanes are laid next**, from the pads they join through the legs the
   directives give, as wide as the class's track plus its clearance plus a
   margin each side. Each becomes a footprint keep-out rule area on both
   sides, stopping at the courtyards of the parts the lane joins, and the
   lane's copper at the class width. A lane whose leg is not axis-aligned,
   whose pad is not on its net, or that runs through a fixed part is refused.
6. **Every other part is placed at the pin it serves.** Its host is the
   placed part it shares the most specific nets with (two-node nets count
   for most, planes for little, a connector or an IC for more than a
   passive, current-carrying and pair classes for more still); a part whose
   nets are all planes (a decoupling or bulk capacitor) belongs to the IC
   the schematic draws it beside, at that IC's next free pin on the rail, a
   capacitor never to another capacitor. The attachment point is the host's
   pads on the shared nets; the part goes on the host's side nearest that
   point, a two-pin part turned so the pad on the host's net faces it, the
   parts along a side packed outward in rings (a bulk capacitor behind the
   small one before either slides along the side). A part the directives
   allow on the bottom (section 3.7) goes there first, tucked under its
   host's pin row, clear of through-hole pads and exposed-pad via fields,
   with the top as its fallback. The order is five
   passes: small decoupling capacitors; the large parts on an IC's or
   connector's own pins (inductors, diodes, crystals); bulk capacitors; the
   small parts on those pins; then parts hosted by passives (an RC chain)
   and parts whose partner was not down yet. A part no ring can take goes
   to the nearest free spot to its pin; the generator reports every part it
   could not keep within 8 mm of its pin, and a placement report beside the
   board file (`placement.txt`) records each part's host and ring. Those
   far parts, and the indicator LEDs (which belong where they can be seen,
   not at the pin that drives them), are the first hand work.
7. Planes are drawn as zones (the ground plane on L2, an isolated ground
   island where there is one), stopping a millimetre short of the edge, and
   a `.kicad_dru` carries the rules the directives need (nothing but the
   isolated classes inside the isolation area; their creepage clearance).
8. The silkscreen pass of section 6 runs over the placement: every
   designator where it overlaps nothing, omitted otherwise, ICs and
   connectors stepping out to the nearest pocket rather than being omitted.
9. The gate is `kicad-cli pcb drc --severity-all --refill-zones` read by
   severity: zero errors other than unconnected items (the board is
   unrouted) and zero warnings, the silkscreen included. The refill (not
   saved) makes the planes real for the check: a plane that does not fill,
   an island, a fill in a keep-out all show. A zone outline with a hole does
   not fill in KiCad: a plane that must avoid a region is drawn as one
   outline around it.
10. The board file is reproducible like the schematic
   ([kicad-generation.md](kicad-generation.md)): `pcbnew` draws random UUIDs
   and writes items in their order, so the generator sorts footprints by
   reference, graphics by content and zones by name, derives every UUID in
   document order, pins the DRC report's date and sorts its entries. Two
   generations of one design are byte-identical, report included. An error inside a
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

## 3. Placement

### 3.1 Flow

- **A connector's interface circuit sits at the connector.** The USB-PD
  sink controller, its VBUS and CC parts and its protection sit at the PD
  inlet; an ESD array sits at its receptacle, in line with the pair; the
  magnetics live in the jack or beside it; a port switch sits between its
  hub pins and its receptacle. Nothing that belongs to a connector is placed
  inboard for convenience.
- **Power flows left to right onto the board's rails.** The inlet is at the
  left edge; the PD controller, the eFuse, the bucks and the rail
  distribution follow in that order across the board, so the high-current
  path never doubles back and each stage's input is the previous stage's
  output. Signals flow the same way the board is read: from the user's
  edge to the target's edge.
- **Blocks are zones** (parent 13.12.2): low-speed, high-speed, analog,
  power and RF are physically separate; high-speed signals do not cross
  low-speed zones; the parts of one block sit together, the block next to
  the connector it serves.

### 3.2 The ICs' own guidelines

Most ICs publish a layout section: where their capacitors go, which node
must be small, what copper the thermal pad needs, what must not pass
underneath. **Those guidelines are followed on a best-effort basis, and the
effort is written down.** Before placement, each IC's datasheet layout
section (and its evaluation board, as in the reference-design review) is
read and its rules are entered in the project's layout guideline table
(`docs/layout-guidelines.md`): the rule, its source, and how the board meets
it or why it does not. A rule not met is a decision, recorded with its
reason, never an omission. Typical entries: a buck's input capacitor loop
and SW node, a current limit resistor's trace, an exposed pad's via field,
a PHY's magnetics placement and the plane under it, a hub's per-pin
decoupling and RBIAS return, an ESD array on the same layer as the pair.

### 3.3 Decoupling and bypassing

- **A capacitor sits at the pin it buffers.** The smallest value is closest
  to the pin, the bulk capacitor behind it, the ground return through a via
  beside the capacitor's ground pad, not through a trace. A capacitor that
  cannot reach its pin is not that pin's capacitor.
- One capacitor per supply pin where the datasheet gives one per pin; shared
  bulk where it says shared.
- Regulators: the input capacitor, switch, catch diode, inductor and output
  capacitor form the smallest loop the parts allow, on one layer, with the
  switching node's copper minimised; the feedback divider next to the FB
  pin and away from the switching node; the timing and limit resistors at
  their pins with short returns.

### 3.4 Connectors, holes and edges

- **Every connector faces outward**, its mating face at the edge, verified
  in the 3D view before anything else is placed. A connector that faces a
  mounting hole, another connector or the board's interior is wrong even
  when the DRC is clean.
- Distances from copper and components to edges, holes, screws and
  neighbours per the parent's Tables 14-1 and 14-2, with the tailorings in
  section 0. The corner squares around the mounting holes carry nothing but
  the holes.

### 3.5 Thermal

- An exposed pad is soldered to a copper area on its layer and tied by a via
  array to the plane below (parent 10; every TI layout section says the
  same). The via count is given in the guideline table.
- Regulators get a top-side copper area sized for the dissipation the
  datasheet's thermal table gives at the board's worst case.

### 3.6 Isolation

An isolated region has its own copper on every layer it uses and no board
plane under it; its creepage to board nets follows the parent's insulation
distances (13.8–13.10) and never less than the directives state; only the
parts that straddle the barrier by design cross it, with the barrier drawn
between their two sides' pins. A DRC rule enforces both the keep-out and the
clearance.

### 3.7 Both sides

The top side carries what must be reached, seen, cooled or kept in a loop:
connectors, ICs, relays, inductors, crystals and their load capacitors,
switches, jumpers, LEDs, test points, bulk and large capacitors, the parts
of a regulator's switching loop, ESD arrays and series parts on
controlled-impedance pairs, ESD protection (3.8), and every part on a
current-carrying class.
Small parts of the remaining kinds (resistors, capacitors, small diodes and
transistors, up to the courtyard area the directives give) may go to the
bottom, **under the pin they serve**, through a via pair at their pads: a
decoupling capacitor under its supply pin is that pin's capacitor when the
top is full. Nothing goes under an exposed pad's via field, within the
hand-soldering margin of a through-hole pad, or taller than the standoffs
allow. The corner keep-outs, the edge zone, the lanes and the isolation
rule apply on both sides. The point of the bottom is the top: the area it
frees is for the blocks' copper zones (section 4), not for more parts. The
assembler is consulted on double-sided reflow before the first order.

### 3.8 ESD protection

- **An ESD diode sits in line with the signal it protects, at the
  connector.** The signal runs from the connector pad to the protection
  device and on from there; nothing else (a switch, a pull-up, a capacitor,
  a series resistor) comes between the connector and the diode, and no stub
  longer than the device's own pad hangs off the signal to reach it. The
  diode's ground pad goes to the plane by a via beside it, never through a
  trace. ESD parts are placed first of all at a connector's signal pins, on
  the connector's side of the board.
- **For differential pairs this is mandatory and flow-through**: the pair
  enters the array on one pin row and leaves on the other, on one layer,
  the array turned so its connector-side pins face the connector and its
  IC-side pins face the IC, the array on the straight path between them.
  The two nets a flow-through array creates (connector side, IC side) are
  both named as a pair and both in the pair's class, so the segment through
  the array is routed at the pair's impedance; the generator refuses a
  build in which a `_P`/`_N` pair is outside a pair class.
- **USB 2.0 is routed as 90 Ω differential controlled impedance**, end to
  end from the receptacle through the ESD array to the transceiver, the
  width and gap of its class computed from the directives' stackup and
  confirmed by the fab's impedance calculator, within 10 %. USB 3.x
  SuperSpeed pairs likewise at 90 Ω (85 Ω only where the device's datasheet
  asks for it), with an array rated for them (well under 1 pF). The same
  holds for every other pair the schematic names: the class states the
  impedance, and the pair never leaves it.

## 4. Copper

- **Planes and power distribution are polygons.** A power net that feeds
  more than one part is a polygon on its layer, never a wide trace where a
  pour will do; the ground plane is one unbroken polygon under every
  high-speed signal (parent 13.12.2 d–f); a power polygon is sized from the
  parent's current rating (13.6, IPC-2152 model, 10 °C rise) and the net
  class states the equivalent trace width.
- **Vias carry current in arrays**: two per layer change for the 3 A
  classes, more for the 6 A class, placed where the polygon narrows.
- Planes stop short of the board edge by the fab's copper-to-edge minimum
  plus a margin, and clear the mounting holes and their standoff pads.
- No copper under crystals, under the magnetics side of an Ethernet jack, or
  under an isolation barrier.

## 5. Routing

- **Controlled-impedance pairs** (parent 13.11): on one layer over an
  unbroken reference plane, no vias except at the pads, end-to-end, the two
  tracks of a pair matched in length, the geometry from the directives'
  stackup and the fab's impedance calculator. USB 2.0 pairs are 90 Ω
  differential, through their ESD array (3.8), from receptacle to
  transceiver.
- **Loops small** (parent 13.12.2 g): every signal has its return directly
  under it; a signal that changes layer gets a ground via beside it.
- **Crystals**: the shortest possible tracks to the IC, load capacitors
  between, a ground ring, no signals routed through the area.
- **High-current paths** follow the class width without necking at pads;
  a sense or limit resistor's trace is short and away from switching nodes.
- **Lanes**: a high-current path is routed inside the lane the directives
  drew for it, at the class width, with nothing else in the corridor; a
  pair's lane runs from its receptacle through its ESD array to its IC. A
  lane's keep-out is narrowed only where the routing proves it wider than
  it needs to be.
- Routing order: section 8.

## 6. Silkscreen

- **Nothing on silkscreen overlaps anything**: not another silk item, not a
  pad or via or mask opening, not the board edge. KiCad's silk checks
  (`silk_overlap`, `silk_over_copper`, `silk_edge_clearance`) are part of the
  final gate and read zero.
- **Reference designators are sized to fit**: 1.0 mm text with a 0.15 mm
  stroke by default, 0.8 mm where space is short and never smaller; placed
  next to the part outside its courtyard, reading in one of two directions
  across the board.
- **Where density defeats that, designators are omitted, deliberately.**
  If a designator at the minimum size cannot sit within 1 mm of its part's
  courtyard without overlapping, it is omitted. In a cluster where more than
  a third of the passives would lose theirs, all of the cluster's passives
  lose theirs and the cluster is outlined and named on silk instead (an IC's
  decoupling, a port's switch and filter), where an outline can be drawn
  without crossing another part; where it cannot (clusters that interleave),
  the host's own designator names the cluster. The fabrication layer keeps
  every designator for the assembly drawing. ICs, connectors, relays,
  polarity marks and pin-1 marks are never omitted: a designator that fits
  nowhere within 1 mm of its part steps out to the nearest free pocket, and
  the generator lists the parts labelled that way for the hand pass.
- Connector names, pin 1, polarity and the markings the directives call for
  are readable with the connectors fitted.
- Bottom-side parts follow the same rule on the bottom silk, mirrored to
  read from the bottom and kept off through-hole pads and via fields.

## 7. Gates

1. `kicad-cli pcb drc --severity-all`: zero errors at every step (the
   documented footprint-internal exceptions aside); zero unconnected items
   at the end of routing; zero silk violations at the end of layout.
2. The 3D view, at placement: every connector faces outward and clears its
   neighbours; nothing stands in a keep-out.
3. The layout guideline table (3.2): every row says met, or why not.
4. The directives: still true of the board; changed where the board changed.

## 8. Placement and routing order

The order the baseboard's layout follows; each step is checked against the
directives before the next starts.

1. **Mechanical first.** The edge connectors against the mechanical drawing
   or a printed 1:1 outline: edge, order, direction, overhang, the holes'
   clearance, the 3D view.
2. **Barriers, isolation and lanes.** Any isolated region is settled next:
   the parts that straddle it, its ground island, the creepage gap, and the
   DRC rule that enforces it, so nothing routed later can cross it by
   accident. The lanes of the high-current paths are laid now, so the parts
   placed next keep out of them.
3. **The pairs.** Controlled-impedance pairs are placed and routed before
   anything else inboard: receptacle, ESD array and controller in a line,
   the pair on the top layer over the unbroken ground plane, no stubs,
   lengths matched.
4. **Power.** Inlet to rails, left to right: the PD stage at its connector,
   the switching regulators with their loops tight and their thermal copper,
   the high-current polygons with their via arrays, then the rails to each
   block as polygons.
5. **Everything else**, block by block, each block's decoupling against its
   pins before its signals leave it. Crystals last within their block, with
   nothing routed under them.
6. **Silkscreen and fabrication**: section 6, then the stackup and
   controlled-impedance notes in the fab drawing.
