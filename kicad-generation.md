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
- `sch export bom --format-preset CSV --group-by Value,Footprint`.

## What a generator should and should not do

Do: pull symbols from KiCad's own libraries so embedded copies match; build
the missing symbols from datasheet pin tables, and say in the symbol's
Description where the table came from; assign footprints for every part;
write the ERC report and PDF as part of the build; number designators by
sheet.

Do not: lay parts out as islands with a label on every pin (see
[schematic-style.md](schematic-style.md)); embed a commit hash in the output
(it is always one commit behind); keep generating once the files have been
hand-edited.

## Where symbols come from

In order: the KiCad standard libraries (224 of them in KiCad 10); a project
library for what they lack, built from the datasheet; SnapMagic/Ultra
Librarian only if the footprint is exotic — in practice KiCad shipped every
footprint the baseboard needed, including the stacked USB-A and the Panasonic
JW1 relay.
