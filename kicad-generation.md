# Generating KiCad schematics programmatically

Facts about the KiCad 10 schematic format and `kicad-cli` that a generator
must get right, each one learned by getting it wrong once.

## File format (`.kicad_sch`, version 20260306)

- S-expressions. KiCad is tolerant of whitespace and formatting on read and
  rewrites the file in its own layout on save; `kicad-cli sch upgrade --force`
  is a cheap way to see what KiCad would have written.
- Every symbol used must be embedded under `lib_symbols`, named `Lib:Name`.
  **Sub-units keep the bare name**: `(symbol "Device:R" ... (symbol "R_0_1" ...))`.
  Prefixing the unit names breaks the loader with no diagnostic beyond
  "Failed to load schematic".
- Derived library symbols (`extends`) are flattened when embedded: copy the
  parent's graphics and units, keep the child's properties.
- A symbol instance carries `lib_id`, `at X Y rot`, `unit`, the five standard
  properties (Reference, Value, Footprint, Datasheet, Description), one
  `(pin "n" (uuid ...))` per pin, and `(instances (project "name" (path
  "/<root-uuid>[/<sheet-uuid>]" (reference ...) (unit ...))))`. The path is
  the root schematic's uuid followed by the uuid of the `(sheet ...)` element
  in the parent — not the sub-sheet file's own uuid.
- Power symbols come from `power.kicad_sym`; **the net name is the Value
  field**, so a `+5V` symbol with Value `+5V_PORTS` makes a net called
  `+5V_PORTS`. GND symbols are the same part with a different value.
- `(global_label "NET" (shape passive) (at X Y rot) ...)` for nets that cross
  sheets; `(label ...)` for local nets. A global label used on only one sheet
  draws a warning; a local label on two sheets silently makes two nets.
- Everything that connects must sit on the **1.27 mm grid**. A part placed at
  (40, 60) mm is off grid and ERC reports every pin.
- **Connectivity is by endpoints.** A wire end, pin end, label point or
  power-symbol pin that lands anywhere on another wire joins it; two wires
  crossing mid-segment do not. So a route may cross a lane (ugly, legal) but
  a lane end on a route, or a capacitor's GND pin on a bus, is a short that
  ERC only reports as "two net names on the same items" — if it reports it
  at all. Check endpoints geometrically (below).
- A multi-unit project symbol with `Datasheet "~"` is reported by ERC as
  "doesn't match copy in library": KiCad folds `~` to the empty string when
  it loads a library but not when it loads the schematic's embedded copy, and
  only the multi-unit comparison notices. Write `""`.
- Several units of one symbol: one `(symbol "Name_1_1" ...)` per unit, pins
  and graphics inside each; instances with the same Reference and different
  `(unit n)`; the loader derives the unit count from the names.
- **A field's drawn angle is the symbol's rotation plus the field's own
  angle.** A resistor placed at 90° with its Reference at angle 0 prints its
  reference vertically. Write the fields at 90° on a symbol rotated 90° or
  270°. When the sum comes to 180°, KiCad shows the text upright but mirrors
  its justification: a field written `(justify right)` reads as
  left-justified. Swap it when writing.
- `(mirror y)` after `(at X Y rot)` flips a symbol left-to-right. Pins keep
  their top/bottom sides, horizontal pin angles swap, library field positions
  mirror and their left/right justification swaps. Use it instead of a 180°
  rotation whenever a part has pins on its top or bottom edge.
- `power:GND` at rotation 0 hangs below its connection point; any rail
  symbol at rotation 0 stands above it. Rotation 180 turns either one
  upside down, which is the thing to check for after every generation.

## Pin geometry

Library coordinates have Y up; schematic coordinates have Y down. For an
instance at (X, Y) with rotation R, a pin at library (x, y) lands at
(X + dx, Y + dy) where (dx, dy) = (x, −y) rotated by R counter-clockwise on
screen: R = 90 maps (dx, dy) → (dy, −dx); 180 → (−dx, −dy); 270 → (−dy, dx).
A pin's library angle says where its body is: angle 0 means the connection
point is on the body's left. Calibrate this against `kicad-cli sch export
netlist` before building anything large — a resistor at each of the four
rotations, wired to computed points, must land on four distinct nets.

## `kicad-cli`

- `sch erc --severity-all --format report` is the gate. Zero violations is
  achievable and should be demanded; the categories KiCad ignores by default
  (global label used once, four-way junctions) are listed at the end of the
  report.
- `sch export netlist --format kicadxml` is the truth about connectivity.
  Local nets are reported with their sheet path (`/Power/PD_SCL`); diff the
  netlist before and after a re-layout and the sets of (ref, pin) per net
  must be identical.
- `sch export pdf` renders every sheet; look at it. Overlapping labels and
  wires that merge are invisible in the netlist and obvious on paper.
  `pdftoppm -r 60` for the whole page, `-r 150` with `-x -y -W -H` for the
  block you are fixing.
- `sch export bom --format-preset CSV --group-by Value,Footprint`.

## Four gates a generated schematic must pass

1. `kicad-cli sch erc --severity-all`: zero.
2. A **geometry check** over each sheet file: every wire end and every pin end
   that lies on a wire it does not terminate is a contact; the only acceptable
   ones are a lane overlapping its own stub (same net). The baseboard's
   `gen/check_geom.py` does this.
3. A **wire-level connectivity trace** of each sheet: union wires by touching
   endpoints, attach labels, power symbols and pins, and report any component
   that carries two rails or a rail plus a differently named label. This finds
   the merges ERC under-reports, and names the two symbols so the bridge can
   be found in the generator's wire log. The baseboard's `gen/netcheck.py`.
4. A **layout check**: estimate every text box (fields with their size and
   justification, labels with their frame), every symbol body (the library
   rectangle, or the pin extent for two-pin parts) and every pin stub, then
   report text over a body it does not belong to, text over a wire, text over
   text, wires through a body, and power symbols pointing the wrong way. None
   of these are electrical, all of them are what a reviewer sees first. The
   baseboard's `gen/check_layout.py`.

Log every wire with the call stack that drew it (`<sheet>.wires.json`) while
developing a layout engine: a contact at (x, y) then names its author.

When re-laying out an existing schematic, diff the netlist before and after
with passives identified by prefix and value rather than reference, since the
references renumber; what remains must be intended. A "closest net" diff
misleads on two-node nets (a D+ net and a D− net share half their members),
so also walk the paths that matter pin by pin — connector D+ through the ESD
array to the controller, each VBUS tap, each I2C bus — and assert each one
is a single net. A tap that lands one segment too far along a lane connects
to the wrong side of a resistor and every other check stays green.

## What a generator should and should not do

Do: pull symbols from KiCad's own libraries so embedded copies match; build
the missing symbols from datasheet pin tables, and say in the symbol's
Description where the table came from; assign footprints for every part;
write the ERC report and PDF as part of the build; number designators by
sheet.

Do not: lay parts out as islands with a label on every pin (see
[schematic-style.md](schematic-style.md)); embed a commit hash in the output
(it is always one commit behind); keep generating once the files have been
hand-edited; use a generic symbol (`D_TVS`) for a part the library has
(`Diode:PESD5V0S1UL` knows its cathode and its SOD-882) — the generic one
leaves the pinning and package to be verified by hand.

## The fan-out engine, in one paragraph

Per side of the hub: sort the lanes by pin, keep them on pin pitch and keep
their pin gaps; analyse each chain for hanging elements (reach above/below,
how far their text spreads back and on, which distances from the lane are
its body and symbol), whether the row is finite (ends in a label, a power
symbol, an in-line part) or a route (a wire to another part), and the
stretches of the lane that carry a part or text. Spread rows apart only where
a hanging element would cross a route, or two elements reach each other's
rows (a small one, like a power symbol, stacks under a tall one; two tall
ones go side by side, and the cheaper of the two slides). Then slide each
hanging element past the neighbours' parts and text it reaches across — past
their wire too where its body or symbol would be hit — iterating to a fixed
point. Place the stack centred on the pin group, or flush with its first pin
when something fixed (a joined pair of pins, a part on the same rows) must
stay put. Lanes that moved turn in staggered columns, one column only where
the vertical run passes another pin; route channels start beyond the widest
chain, one per route, or at a given x when the default would land on a
capacitor row.

## Where symbols come from

In order: the KiCad standard libraries (224 of them in KiCad 10); a project
library for what they lack, built from the datasheet; SnapMagic/Ultra
Librarian only if the footprint is exotic — in practice KiCad shipped every
footprint the baseboard needed, including the stacked USB-A and the Panasonic
JW1 relay.
