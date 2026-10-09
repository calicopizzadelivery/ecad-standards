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
| 14.3.2 Table 14-2 B1 | 0.6 mm between bodies | met as KiCad courtyards (0.25 mm each side) plus a 0.15 mm packing margin: 0.65 mm pad to pad, and two parts' silk outlines (drawn at the courtyard edge) never touch (measured practice, section 9: 0.3 to 0.6 mm) |

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
- **Floods and stitching**: the ground that floods each outer layer and the
  outline it fills (the plane's, notched around any isolated region), the
  isolated region's own ground flood inside the notch, and the stitching
  grid: its pitch, the margin from the edge, the rectangles the vias keep
  out of (an isolated region grown by its creepage) or stay inside.
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

The first board file is produced by the standard's placement engine
([tools/placer.py](tools/placer.py), on KiCad's `pcbnew` Python module; a
project calls it from a wrapper such as the baseboard's `gen/pcb.py`) from the
schematic, the project file and the directives, so the directives are
honoured exactly and the start is reproducible:

1. The outline (with its corner radius), the mounting holes and the keep-out
   rule areas come straight from the directives; the stackup is written into
   the board, layer by layer, with the fab's dielectric thicknesses.
2. Every footprint is loaded from the libraries the schematic names and its
   pads are given the nets from the schematic's XML netlist export, so the
   board starts with a complete ratsnest and the net classes the project
   file assigns. The engine stops when a class the project defines has no
   net: the project's patterns are globs over the full hierarchical name,
   so a sheet-local net (`/USB hub/PORT1_VBUS`) is matched only by a
   pattern that starts with `*`.
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
   lane's copper at the class width. A lane whose pad is not on its net or
   that runs through a fixed part is refused. **A pair lane** lays the two
   member tracks at the class's differential width and gap along one
   centreline, 45-degree corners, escapes from the pads at the pads' own
   pitch (through-hole rows get a straight stub past the row first), the
   two doubled D+/D- pads of a USB-C receptacle bridged behind the pad row
   through staggered vias on the far side, an optional layer change as a
   via pair (the members spread to the via pitch over a millimetre), and a
   length-matching bump on the shorter member where it stands clear of
   every other lane; it checks that the P member leaves one end on the same
   side it arrives at the other, and refuses the lane otherwise, since a
   crossing is fixed in the schematic (section 3.8), not in copper. A
   pair's members are measured over everything laid for the pair, the
   through-hole stubs and pad escapes included (the receptacle bridges
   excepted: they are not signal path), and matched by a bump where one
   fits; where none fits the report says so with the mismatch.
6. **Every other part is placed at the pin it serves, in its city.** The
   islands are read from the sheets (3.1): symbols a wire joins (a pin end
   on a wire's end, on its run, or on another pin), transitively, plus
   symbols whose bodies, grown by 3 mm, touch; a label joins nothing; power
   symbols and parts without a footprint are not members (`ISLAND_REACH`
   caps the body distance a wire may bridge, unlimited by default). The
   regulators of `REGULATORS` go first, as 3.1 says, their SW and VIN pins
   found by the schematic's pin names (or named in the directive); one with
   a `LAYOUTS` template (3.2) has each part's side, ring and orientation
   taken from the figure, the template's pin order setting the placement
   order and a part on several templated pins taking the most specific one
   (the bootstrap capacitor goes to BOOT, not SW). A part's
   host is the placed part it shares the most specific nets with (two-node
   nets count for most, planes for little, a connector or an IC for more
   than a passive, a member of its own island for more than one on the same
   sheet, current-carrying and pair classes for more still); once a member
   of its island that it shares a net with is placed, only island members
   are candidates, and a member waits while its island's hub is not down.
   An IC is never hosted by a passive while a connector or an IC will do; an
   ESD part's connector outranks its island; a crystal's load capacitors
   are its, placed right after it at its ends (section 5); a button's
   debounce and pull-up parts are the button's, wherever the directives put
   it; and a decoupling capacitor lies across its power trace (3.3), facing
   the pin only where that finds no room. A part whose nets are all
   planes (a decoupling or bulk capacitor) belongs to the IC the schematic
   draws it beside (waiting for it if it is not down yet), at that IC's next
   free pin on the rail, a capacitor never to another capacitor outside its
   own island. Every spot a part is tried at keeps the packing margin to its
   own city and the void to every other city on its side, and a ring on
   another city's host starts the void away from it; the fixed parts are
   checked against the same rule, so two anchors of different cities closer
   than the void are refused. The attachment point is the host's
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
   board file (`placement.txt`) records each part's host and ring, then
   each city's extent on the board with the members placed more than 10 mm
   from every other member, then every gap between two cities on one side
   narrower than the void. Those far parts, the members placed apart from their city, and
   the indicator LEDs (which belong where they can be seen, not at the pin
   that drives them), are the first hand work.
7. The rail vias of section 4 are dropped beside the pads of every plane
   net, the class's count each, or what fits (section 4), and reported
   with the pads that fell short and the pads that had no room. The
   report also lists every anchored part with its value beside its
   position: an anchor written under the wrong designator (a regulator's
   place given to a level shifter) is invisible to DRC and to the cities,
   and visible there. Planes are drawn as zones (the ground plane on L2, the rails as regions
   of L3 at their own priorities, an isolated ground island where there is
   one), stopping a millimetre short of the edge, after the rails gate: each
   rail's copper is rasterised with the higher-priority planes of the other
   nets carving it, its planes joined only where their outlines overlap or
   touch (a gap narrower than the raster is a gap in the copper too), and a
   rail in more than one piece stops the generation
   (the router does not join the pieces of a plane net, and a via in a
   carved patch reaches nothing), so such a rail is redrawn or routed
   instead, in a class at the width its current wants (`--rails
   DIRECTIVES.py` prints the pieces while the planes are being drawn);
   a rail via is placed only where its net's plane is the one on top; and a `.kicad_dru` carries the rules the directives need (nothing
   but the isolated classes inside the isolation area; their creepage
   clearance).
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
10. **The rest is routed by FreeRouting** (`tools/autoroute.py`): the board
   goes out as Specctra with every lane track and via locked (fixed) and
   the planes as planes, FreeRouting runs headless on one thread, the
   session comes back and the board is saved at once; in a fresh process the
   copper the router laid on a pair net (a stub to a pad centre, a second
   path round a bridge: the lanes connect those nets by themselves) is
   removed, where the lanes reach every pad of the net (a pair net with a
   pad off the lane keeps the router's copper and the sweep names the pad),
   the router's sub-minimum stubs are floored, the segments on a wide class
   that run on at a pad's width more than a millimetre beyond the pad are
   listed for the hand pass,
   the board is written in canonical order and the gate runs. The class widths the router sees
   are capped at 2 mm (the 6 A class runs on the rails through its vias, not
   as a track); the router runs with a via cost of 25 and a starting rip-up
   cost of 200 (its own 50 and 100 left 116 connections open on the
   baseboard where these leave 99; a 1 mm cap takes more off the wide nets
   and puts some back on the signals, so the cap stays) and with the
   project's clearances as they are (a margin over them turns every lane
   laid at the clearance into a violation in its eyes); a router track the
   gate faults is removed and the gate runs again, the connection joining
   the hand pass. What the autorouter leaves unrouted is
   finished by hand in KiCad, and from then on the board file is the source
   of truth.
11. **The copper after routing** (`tools/copper.py`, run over the routed
   board, and again whenever the routing changes: it adds only what is
   missing). The ground floods of section 4 are drawn on both outer layers
   from the directives (the plane's notched outline; the isolated region's
   own ground inside the notch) at the lowest priority with thermal reliefs,
   and the stitching vias of section 5 are placed from the directives' grid:
   on the grid point, else at the nearest clear spot within half a pitch,
   clear of every pad, track and via by the clearance, out of every via
   keep-out and the directives' rectangles, never within the fill's minimum
   width of another net's zone edge, and dropped where the fill shows one
   cut an island off a rail, and dropped again where the filled floods
   reach it on fewer than two layers (a via the plane alone would hold is
   a dangling via); a pour island that holds a pad of the net but no via
   gets one inside it, where there is room, so no ground pad sits on a
   flood the plane never reaches. The board is saved with its fills, in
   canonical order (saved unfilled, reloaded and filled afresh: KiCad's fill
   of a board loaded without fills is byte-identical from run to run, a
   refill over existing fills is not), so the pours are in the file as it is
   opened and rendered. The DRC gate runs again. A pad the routing crowds so
   the flood reaches it with one spoke is a starved-thermal warning: the
   hand pass moves the track or accepts it where the pad has its own via.
12. The board file is reproducible like the schematic
   ([kicad-generation.md](kicad-generation.md)): `pcbnew` draws random UUIDs
   and writes items in their order, so the generator sorts footprints by
   reference, graphics by content and zones by name, derives every UUID in
   document order, pins the DRC report's date, sorts its entries and
   writes its unconnected items as a tally per net (KiCad names a different
   pair of items for the same missing connection on every run). Two
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

A change to the outline or to the edge connectors is a new generation, not
an edit: the routing is discarded (it stays in the history), the directives
are changed and the board is regenerated. A board that grows is grown as a
band of new board inserted at a cut line between blocks: every block tied to
the edge that moves (its connectors, the ICs behind them, the fixed parts,
the isolated region, the lanes' coordinate legs, the rail rectangles and
the stitching rectangles) moves by the band's width, everything on the other
side stays, and the band is the room gained. The copper pass (step 11)
follows the next routing.

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
- **The schematic's islands are cities on the board.** An island is what a
  sheet joins by wires: a part and everything fanned out from its pins,
  down the last capacitor hung from its supply bus (the wires are its
  streets). A label is a road out of town: what leaves an island by name
  belongs to the island it lands in. On the board each island is a city,
  its parts packed together, and between any two cities lies a **void of
  2 mm** (directive `CITY_GAP`) on each side of the board where no part of
  either stands: the roads, the routing between the blocks, run in the
  voids. The void holds per side, so one channel of a split pair (3.7) may
  sit under the other. The island's hub (its member with the most pins) goes down first,
  at the placed part it shares the most nets with, and every other member
  is placed at a member of its own city that it shares a net with, waiting
  for the hub rather than taking a host elsewhere. Outside the cities stand
  the connectors (they sit where the edge table puts them, and a city drawn
  around a receptacle comes to the receptacle), the holes, the ESD parts
  (3.8: at the connector, whatever island drew them) and a lone symbol
  hosted by a connector; a lone symbol hosted by a city's part joins that
  city. A schematic drawn by [schematic-style.md](schematic-style.md) gives
  these islands for free. The generator reads them from the sheets (section
  2, step 6) and reports each city's extent, its members placed apart from
  it, and every gap between cities narrower than the void.
- **A regulator is the strictest city**, and sits at its input: the bucks
  at the inlet connector, a point-of-load regulator at the rail it draws
  from. Its city is its datasheet's typical application (input ceramics,
  catch diode or low-side switch, inductor, output capacitors, bootstrap,
  feedback, compensation, timing), placed before every other satellite on
  the board so nothing else takes its ground: the inductor and the catch
  diode first, on the side the directives name for the SW pin (`REGULATORS`,
  "the loop flows right"), filled along that side before stepping out so
  the diode stands beside the inductor at the pin; then the input
  capacitors at VIN, smallest nearest; then the output capacitors at the
  inductor's output, smallest nearest; then the rest at their pins.

### 3.2 The ICs' own guidelines

Most ICs publish a layout section: where their capacitors go, which node
must be small, what copper the thermal pad needs, what must not pass
underneath. **Where the datasheet gives a recommended layout or a layout
example, the part's island is placed to it, exactly.** The directives
transcribe the figure as a layout template (`LAYOUTS`): for each pin of the
IC, the side of the IC on which the figure puts the parts hanging from that
pin and those parts' kinds in order outward (the catch diode along the side
at SW, the inductor beyond it; the input bypass at VIN; the bootstrap
capacitor above it; the frequency resistor below; the compensation network
at COMP with the divider beyond), where the output capacitors sit relative
to the inductor, and whether the figure keeps everything on the top side.
The engine places the island from the template in the figure's order, the
switching loop first, and the figure is cited, with the measured result,
in the project's layout-guidelines document. Where the datasheet has no
layout figure but the maker's evaluation board is published, the evaluation
board's layout is the figure (`TEMPLATED` names the IC and its template).
**Guidelines without a figure are followed on a best-effort basis, and the
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
  cannot reach its pin is not that pin's capacitor. It sits on the IC's own
  side of the board unless the first two rings there are full (measured
  practice, section 9: 2 to 2.4 mm from the pin, on the same side nine
  times in ten even on two-sided boards).
- **A decoupling capacitor lies across the power trace it decouples**: its
  axis along the IC's edge, its power pad nearest the pin, so the trace from
  the pin runs straight into that pad and the ground pad sits beside it with
  its via, rather than the capacitor pointing at the pin with the trace
  running past both pads. Where the pin's ring has no room for the
  capacitor across the trace, it faces the pin instead, and the generator
  says so.
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
top is full. A bottom part tucks about 1.75 mm inside the IC's courtyard
edge, under its pin row, and keeps 0.5 mm from through-hole pads (both
measured, section 9; wave or selective soldering needs the assembler's own
figure). Nothing goes under an exposed pad's via field or stands taller
than the standoffs allow. On two-sided boards in practice about half the
parts and two thirds of the passives are underneath.
**A mirrored pair of channels at one connector may be split between the
sides**: where a stacked receptacle or a dual part carries two identical
channels, the directives (`SIDES`) put one channel's switch or driver and
its capacitors on the bottom and the other's on top, the bottom one beside
the connector where the top one sits over it, so the area in front of the
connector holds one channel per side instead of two side by side. The
channel's indicator LED and its series resistor stay on top, and so does
its ESD. The corner keep-outs, the edge zone, the lanes and the isolation
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
  Which channel of the array carries D+ is a layout fact: with the array's
  connector-side pins toward the receptacle, the pair crosses itself unless
  the channel on the receptacle's D+ side carries D+. The schematic is drawn
  to that (the house library's USBLC6-2SC6-IO2up draws the array with its
  I/O2 row on top for the case where D- lies on the I/O2 end), and the
  generator's crossing check says which arrays need it.
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

- **Pads join zones through thermal reliefs** (0.5 mm gap, 0.5 mm spokes,
  four where the pad allows), solid only where the directives ask for it
  (a regulator's exposed pad, a current path); zones fill with a 0.2 to
  0.25 mm minimum width at 0.25 to 0.3 mm clearance. A pad the routing
  crowds so a zone reaches it with one spoke is connected: the generator
  writes that check (`starved_thermal`) into the project file at warning
  severity, KiCad's default being error, so the DRC gate lists it for the
  hand pass instead of failing on it. (Practice: every zone
  on every reference board uses thermal reliefs, at 0.5 mm; zone minimum
  width 0.2 mm, clearance 0.24 mm.)
- **Ground floods the outer layers** around the routing, stitched to the
  plane (section 5), so return paths and shielding do not depend on the
  plane alone. The floods are standard practice, not a finishing touch: the
  generator draws them on both outer layers from the directives (section 2,
  step 11) at the lowest priority, with thermal reliefs, on the plane's
  outline, notched around any isolated region, which floods its own ground
  inside the notch. (Practice: ground pours cover about half of each outer
  layer; three to five power pours per board carry the rails.)

- **Planes and power distribution are polygons.** A power net that feeds
  more than one part is a polygon on its layer, never a wide trace where a
  pour will do; the ground plane is one unbroken polygon under every
  high-speed signal (parent 13.12.2 d–f); a power polygon is sized from the
  parent's current rating (13.6, IPC-2152 model, 10 °C rise) and the net
  class states the equivalent trace width.
- **Vias carry current in arrays**: two per layer change for the 3 A
  classes, more for the 6 A class, placed where the polygon narrows. The
  generator drops them before the router (section 2, step 7): every SMD pad
  on a net that has a plane or rail under it gets its class's vias
  (`RAIL_VIAS`, one by default) beside it, joined by a stub at the class
  width or the pad's narrower side where that is less, outward from the part, clear of every other pad, via and lane by
  the clearance and of other nets' pads by a solder-mask web, so the current
  reaches the copper that carries it and the router has nothing to route
  for that pad. Where the class's vias do not fit beside a pad (a 1.2 mm via
  between 0402 pads seldom does) the engine places what fits, fewer of
  them and then the default via, down to one, and reports the shortfall
  for the hand pass: a rail reached by one small via is a rail reached, a
  rail reached by none is an island. A pad with no room beside it (the packed rings leave none
  at many) is reported and left to the router and the hand pass; a
  through-hole pad reaches the planes by itself.
- Planes stop short of the board edge by the fab's copper-to-edge minimum
  plus a margin, and clear the mounting holes and their standoff pads.
- No copper under crystals, under the magnetics side of an Ethernet jack, or
  under an isolation barrier. The generator draws the directives'
  `COPPER_VOIDS` as rule areas on every copper layer that no plane or pour
  enters (a jack whose pins span its body gets the whole body; its pins'
  tracks still pass).

## 5. Routing

- **Controlled-impedance pairs** (parent 13.11): on one layer over an
  unbroken reference plane, no vias except at the pads, end-to-end, the two
  tracks of a pair matched in length within 1 mm, the geometry from the
  directives' stackup and the fab's impedance calculator. USB 2.0 pairs are
  90 Ω differential, through their ESD array (3.8), from receptacle to
  transceiver. Practice (section 9) matches pairs to 0.8 mm (1.4 mm at the
  90th percentile) but changes layers on two pairs in three, with three or
  four vias: this standard keeps the parent's rule and, where a change of
  layer is unavoidable, makes it once, both tracks together, with a ground
  via beside the pair at the change. A pair class whose width and gap are
  KiCad's defaults is not a pair class; the numbers come from the stackup.
  A USB-C receptacle's doubled D+/D- pads (A6/B6, A7/B7, interleaved along
  the row) cannot both be joined on the receptacle's layer without a
  crossing: the pair leaves from two adjacent pads and the other two are
  bridged behind the row through vias on the far side, as the reference
  boards do, the vias staggered so no stub comes within clearance.
- **Signal tracks** are 0.2 mm wide at 0.15 mm clearance by default (the
  net class the generator writes), down to the fab's minimum where a pitch
  demands it; vias 0.6 mm on a 0.3 mm drill, through-hole only, no blind or
  micro vias without a reason in the directives. (Practice: 0.18 to 0.2 mm
  tracks at 0.13 to 0.15 mm, vias 0.56 to 0.6 mm on 0.3 to 0.4 mm drills,
  no blind or micro vias on any reference board.)
- **Loops small** (parent 13.12.2 g): every signal has its return directly
  under it; a signal that changes layer gets a ground via beside it.
- **Crystals**: the shortest possible tracks to the IC, a ground ring, no
  signals routed through the area. The crystal lies along the IC's edge at
  the oscillator pins, a signal pad at each end, and **its load capacitors
  flank it, one at each end**, each at the end nearer the crystal pad of
  its own net, turned across the IC's edge with its signal pad on the trace
  from that pad to the pin and its ground pad outward, so each trace runs
  pin, capacitor pad, crystal pad in a line. The capacitors belong to the
  crystal, not to the IC's pin, are placed right after it, and stay on its
  side of the board; where an end is already taken (a lane entering the
  same row of pins, a neighbouring city's void), the capacitor takes the
  crystal's far side, still against it.
- **High-current paths** follow the class width without necking at pads;
  a sense or limit resistor's trace is short and away from switching nodes.
  The class width comes from the current and the parent's IPC-2152 limit,
  not from habit: reference boards run power at 0.5 mm with a 1 mm 90th
  percentile, which is why their rails are pours rather than tracks.
- **Layers**: on a four-layer board the outer layers route and carry
  ground pours, the first inner layer is the unbroken ground plane, the
  second inner layer carries the power planes and the few tracks that must
  cross the board. (Practice: the bottom carries 40 to 45 % of the track
  length, the inner layers 3 to 12 %, and every multilayer reference board
  has a ground plane on an inner layer, most on the one under the top.)
- **Ground stitching**: the outer ground pours are tied to the plane with
  vias at about four per square centimetre, and a ground via sits beside
  every signal that changes layer. The generator places the grid (section
  2, step 11): 5 mm pitch, four per square centimetre, each via moved to
  the nearest clear spot within half a pitch where the routing is in the
  way, none within an isolated region's creepage, none cutting a sliver off
  another net's zone. (Practice: 3 to 5 ground vias per cm².)
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
- **Reference designators are sized to fit**: 0.8 mm text with a 0.12 mm
  stroke by default, 0.7 mm with a 0.1 mm stroke where space is short and
  never smaller (measured practice, section 9: 0.8 mm typical, 0.65 to
  0.72 mm the smallest in use; the fab's silk minimum is 0.1 mm); placed
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
   lengths matched. The generator lays them from the directives' pair lanes
   (section 2) and reports each pair's lengths, mismatch and layer changes.
4. **Power.** Inlet to rails, left to right: the PD stage at its connector,
   the switching regulators with their loops tight and their thermal copper,
   the high-current polygons with their via arrays, then the rails to each
   block as polygons.
5. **Everything else**, block by block, each block's decoupling against its
   pins before its signals leave it. Crystals last within their block, with
   nothing routed under them. The autorouter does this pass over the locked
   lanes and the planes (section 2, step 10) and the copper tool adds the
   ground floods and stitching (step 11); the hand pass finishes what they
   leave.
6. **Silkscreen and fabrication**: section 6, then the stackup and
   controlled-impedance notes in the fab drawing.

## 9. Measured practice

The numbers in this standard are checked against boards other people laid
out well. The baseboard project mirrors them locally
(`scripts/reference-boards.txt`, `scripts/fetch-reference-boards.sh`) and
measures them with `scripts/harvest-placement.py`, which reads each board
with `pcbnew` and reports, per board, the decoupling capacitors' distance
from their supply pin and whether they share the IC's side, the ESD parts'
distance from their connector, the crystals' from their IC, the nearest
pad-to-pad gap, the parts' distance from the edge, how far bottom parts
tuck under an IC and keep from through-hole pads, the designators'
visibility and size, and the track widths. The method and the table are in
the project's `docs/reference-boards.md`; the medians that set the rules
above (2026-10-06, 81 boards: Olimex 48, MNT Reform 14, SparkFun 16,
Raspberry Pi 3; routing rows from the same boards, the pair rows from the
49 with routed pairs, the layer rows from the 51 multilayer boards):

| Measure | Reference boards (median; two-sided boards) | This standard |
|---|---|---|
| Decoupling, pad to supply pin | 2.4 mm (2.0) | at the pin, first ring |
| Decoupling on the IC's side | 100 % (89 %) | the IC's side first |
| Pad-to-pad gap, 10th percentile | 0.34 mm (0.30) | 0.65 mm (courtyards + 0.15) |
| Parts to the board edge, minimum | 0.73 mm (0.65); SparkFun 2.9 | 3 mm (ECSS tailoring, the assembler consulted) |
| Bottom parts tucked under an IC | 1.8 to 2.0 mm | 1.75 mm |
| Bottom parts to through-hole pads | 0.5 mm | 0.5 mm |
| Parts on the bottom, two-sided boards | 55 % (passives 63 %, ICs 33 %) | small passives, section 3.7 |
| Designators visible | 92 % (SparkFun hides all) | all that fit, section 6 |
| Designator height | 0.8 mm typical, 0.65-0.72 smallest | 0.8 / 0.7 mm |
| ESD part to its connector | 3.5-6.8 mm | at the connector, first ring |
| Crystal to its IC | 5.9 mm | at the IC, first ring |
| Courtyard area over board area | 54 % (76 %) | a density to expect |
| Signal track width (multilayer boards) | 0.2 mm (0.18) | 0.2 mm default |
| Clearance in the boards' rules | 0.15 mm (0.13) | 0.15 mm default, fab minimum 0.127 |
| Via diameter / drill | 0.6 / 0.4 mm (0.56 / 0.3) | 0.6 / 0.3 mm |
| Blind or micro vias | none | none without a reason |
| Power track width, median / 90th | 0.5 / 1.0 mm | by current, as pours |
| Track length on the bottom layer | 45 % (39 %) | both outer layers route |
| Track length on inner layers | 3 % (12 %) | planes, a few crossings |
| Inner ground plane on multilayer boards | all; under the top on most | In1 unbroken ground |
| Ground vias per cm² | 3.3 (4.2) | about 4: a 5 mm grid, less what the routing blocks |
| Pads to zones by thermal relief | 100 % of zones, 0.5 mm gap and spoke | thermal, 0.5 / 0.5 |
| Zone minimum width / clearance | 0.2 / 0.24 mm | 0.25 / 0.3 mm |
| Ground pour share of the outer layers | 44 % | floods on both outer layers, drawn from the directives |
| Pair gap / width in the copper (49 boards with pairs) | 0.15 / 0.13 mm | from the stackup (90 Ω) |
| Pair length mismatch, median / 90th | 0.8 / 1.4 mm | within 1 mm |
| Pairs on a single layer | 1 in 3; 3 vias per pair | one layer, no vias (parent 13.11) |

A rule that practice contradicts is either changed here with the number,
or kept with its reason stated (the edge zone keeps the parent's conveyor
allowance). Rerun the harvest when the mirror grows; a new project checks
its own board against the table with the same script.
