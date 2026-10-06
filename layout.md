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

The first board file is produced by a generator from the schematic, the
project file and the directives, so that the directives are honoured
exactly and the start is reproducible:

1. The outline, holes and keep-outs come straight from the directives.
2. Every footprint is loaded from the libraries the schematic names, its
   pads given the nets from the schematic's netlist export.
3. Edge connectors are placed on their edges in the stated order and
   direction; inboard parts are placed in groups by function next to the
   connector they serve, on a coarse grid, as a starting point and nothing
   more.
4. The net classes come from the project file; the stackup is written into
   the board.
5. The gate is `kicad-cli pcb drc --severity-all`: zero violations other
   than unconnected items, before any routing.

From there the board file is the source of truth. Placement is refined and
routing is done in KiCad; the generator is not run again over a board that
has been edited. The schematic generator stays usable, because its derived
UUIDs keep the footprints linked (see
[kicad-generation.md](kicad-generation.md)).

## 3. Placement and routing order

Recorded as the baseboard's layout proceeds.
