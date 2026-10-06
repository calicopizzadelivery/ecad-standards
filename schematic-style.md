# Schematic style

The reference is the OpenPilot Revolution flight controller schematic (Altium,
one A3 sheet, D. Ankers for the OpenPilot project): an STM32 in the middle,
every peripheral wired to it, nothing floating. These rules are what makes
that sheet readable, written down so a generator or a person can reproduce it.

## 1. The hub-and-fan-out model

Every sheet has a **hub**: the component the sheet is about — the MCU, the
hub controller, the PHY. It sits near the middle. Its pins **fan out** as
wires: each wire leaves the pin and runs straight to the thing it connects
to. Lanes stay at the pin pitch wherever they can; they keep the pin order,
so they never cross each other.

A part that hangs off a lane — a pull-up, a capacitor to GND, a power
symbol — reaches across the neighbouring rows. It does not get its own
vertical room: it **slides along its lane** past the neighbours' parts and
text, which is why pull-ups on adjacent pins form a staircase in the
reference. It may cross a neighbour's plain wire with its lead (that is a
hop-over), but never with its body or its rail symbol: a row that would hit
either must end first. For this to work the hanging part sits with its near
pin *between* rows (centre 7.62 mm off the lane, pins at 3.81 and 11.43), so
the lane next door crosses only the lead. Two pull-ups on adjacent lanes
need the upper lane's label pushed out past the lower pull-up — leave a
plain stretch of wire for it. Only when a covered row cannot end, because
it is a wire to another part, or when two hanging parts reach each other's
rows, are the rows spread apart — and then every lane that had to move
turns in its own column, outermost first, so nothing crosses; a lane whose
vertical run passes no other pin needs no column of its own.

A chain hanging from a pin on a part's top or bottom edge runs sideways
first (a 10 mm lead) and then hangs its parts, so nothing hangs back into
the body.

What a wire may end in — the **attachments**, in order of preference:

1. **A pin of another part on the sheet.** Connectors, the ESD array between a
   port and its connector, the level translator between the MCU and a header.
   Drawn as a wire, routed Manhattan, one bend where the rows do not line up.
   Better still, **place the part so the rows line up** and the wire is
   straight: an ESD array sits on its connector's D−/D+ rows, a port switch's
   OUT pin on the connector's VBUS row. Two parts on the same rows go in the
   same column only if neither's body straddles the other's wires.
2. **A series part in line** — the 470 Ω on a UART line, the Schottky into
   VREGIN, the inductor after a switch node. The part sits on the wire; the
   wire continues past it.
3. **A pull-up or pull-down hanging off the wire** with a junction dot: the
   resistor goes up to its rail symbol or down to GND; the wire carries on.
   Decoupling that belongs to one pin (a VREG output's 1 µF) is drawn the same
   way.
4. **A power symbol.** Rails stand up from the wire end, GND hangs below.
5. **A net label**, and only when the net leaves the page or genuinely cannot
   be wired without crossing half the sheet. A label is a jump; every jump
   costs the reader a search.

A sheet may still have **islands** — a sub-circuit wired to nothing on the
page but a label, such as a relay driver whose only input is an MCU GPIO from
another sheet. Use them sparingly, and wire the island internally: the FET,
its flyback diode, its LED and its connector are one cluster, not four parts
with labels.

## 2. Where things go

- **Connectors at the page edges**, facing outward: things that go to the
  outside world on the left or right edge, on the side the board's own
  connector faces. Inputs on the left, outputs on the right, when there is a
  choice.
- **Power flows left to right and top to bottom**: inlet, protection,
  regulator, output filter, rail symbol — in that order along one line, as the
  reference draws its LDOs across the top of the sheet.
- **Decoupling sits beside the IC it decouples**, as a **ladder**: the
  capacitors hang from the supply bus into a GND rail below them, and that
  rail ends in one GND symbol pointing down. Not scattered, not labelled, and
  never hung upward with the GND symbols pointing at the sky.
- **Same-rail power pins share a bus**: VDD pins tied together with one
  wire along the top of the symbol and one rail symbol at its end, not six
  rail symbols. The ladder hangs from that bus beyond the pins, so the bus is
  the IC's supply and its capacitors in one glance; the bus sits high enough
  (12.7 mm above the pins) that the ladder's GND rail clears them. GND pins
  get the same treatment along the bottom. A pin of another rail between two
  bus pins (a VREGIN between VDDs) cannot be crossed by the bus: start the
  bus at the first pin past it.
- **Supply pins packed at the pin pitch** (VDDA next to VDDIO next to the
  core regulator output) cannot each carry a rail symbol straight up: the
  names print over each other and over the neighbour's wire. Jog them: a
  short stub up, a run sideways, then the symbol, each one further out, so the
  names fan apart.
- **Strap resistors to one rail share a bus too**: ten LED-strap pull-downs
  are ten resistors in a column with one vertical wire and one GND symbol,
  not ten GND symbols stepping down the page. The same goes for five or more
  pull-ups to one rail on adjacent pins (a row of /FAULT inputs with their
  I2C neighbours): hung one per lane they stagger past each other's text and
  walk off the sheet; drawn as a column to one rail symbol they take one
  width. A bus needs every lane's content to end at the same x, so the
  labels on those lanes are given one fixed step.
- **Pin names inside a symbol never overlap.** A pin on the top or bottom
  edge prints its name vertically into the body, so a project symbol keeps
  whole rows clear above the first side pin (and below the last) for the
  longest such name — `VDD33PLL` needs four rows, `GND` two. Side-pin names
  must not meet in the middle either: the body is as wide as the longest
  left name plus the longest right name plus a gap. Extend the body; never
  shrink or abbreviate the name. The layout gate checks every symbol on the
  sheet for this, library symbols included.
- **Two parts on one footprint are two units**: a stacked USB-A receptacle
  is drawn as two single-port connectors with the same reference, so each
  port's switch, ESD and receptacle form one cluster. Both units carry one
  Value (KiCad flags differing unit values as an annotation error); the port
  is told by the unit letter KiCad adds to the reference and by its cluster's
  labels.
- **Clock sources flow downward.** A crystal (or oscillator) hangs below
  its two XTAL lanes: the lanes run out past everything else on that side
  of the hub to two columns a crystal's pin pitch apart, turn down at right
  angles, and drop straight through the crystal's pins, which lie across the
  columns, on into one load capacitor each and into one shared GND rail with
  one GND symbol under the crystal (its own ground pins join the same rail).
  The upper lane takes the outer column, so the two never cross; every wire
  meets the crystal and the capacitors at a right angle; the texts sit on the
  outer side. The capacitors are never hung off the XTAL lanes next to the
  hub with the crystal somewhere else: the clock source is one cluster. Put
  the XTAL pins at the bottom of their column in a project symbol when the
  sheet is crowded below them, so the cluster hangs under everything else.
- **Reset circuits flow downward.** The pull-up, the capacitor and the
  button of a reset (or enable) network are one cluster at the end of the
  reset lane, drawn top to bottom: the rail symbol, the pull-up resistor, the
  node row where the lane arrives from the hub, the capacitor straight below
  in the same column, one GND symbol under it. A push button, when there is
  one, hangs from the lane one column nearer the hub and shares the GND rail;
  nothing else sits on those columns. The lane carries the net's label
  between the hub and the cluster. A header that also drives the reset gets
  the same label on its pin, not a wire into the cluster: the node already
  takes three wires and a fourth would make a four-way junction. The parts
  are never strung along the lane with the resistor hung up here and the
  capacitor hung down there: the time constant is one column. Texts: the
  button's toward the hub, under the lane; the resistor's and capacitor's
  away from it.
- **Sub-circuits are clusters.** A port is its switch, its ESD, its connector
  and its LED in one group, repeated per port, aligned so the eye can diff
  them.
- **Differential pairs are named for the router.** Every high-speed pair
  (USB 2.0 D+/D−, and any other) carries net names `<base>_P` and `<base>_N`,
  the suffixes KiCad's PCB editor recognises as a pair, with the same base on
  both. Name every segment: the pass-through pins of an ESD array are two
  nets, so the hub side (`HUB_DN4_P/N`) and the connector side
  (`PORT1_D_P/N`) are both named, and so is the short stretch between a
  series resistor and the chip (`FTDI_USB_P/N`). The base name says where
  the segment goes: `<connector>_D` at a receptacle (`J3_D`, `PORT1_D`),
  `<chip>_USB` at a device (`K64_USB`, `FTDI_USB`), `HUB_UP` and `HUB_DNn`
  at the hub. A pair's labels sit at wire ends, on the two rows, with the
  text running along the pair's own wire: at the ESD array's pin ends, at
  the receptacle's pin ends, at the lane ends before the series parts. The
  rows of the ESD array and the receptacle line up, so the pair runs
  straight between them. The pairs form a net class in the project
  file (`USB`: differential width and gap set from the stackup before
  routing), and the sheet note says they are 90 Ω pairs routed as pairs with
  no stubs.

## 2a. The sheet itself

- **Nothing touches the frame.** KiCad's default A3 sheet draws its inner
  frame line 12 mm in from every edge; every part outline, every piece of
  text, every wire and label stays at least 3 mm inside that line (so within
  15 mm to 405 mm across, 15 mm to 282 mm down). A connector "at the edge"
  means near it, not on it.
- **The title block is kept clear.** On the default A3 sheet it occupies the
  lower right, from x = 300 mm and y = 253 mm to the frame. Nothing is placed
  in or over it, including a regulator's output filter or a note; a block that
  needs that corner's width goes up a row instead.
- **Notes sit at the top left**, under the frame line, and any note that must
  sit low on the page sits above the frame's bottom margin, not on it.
- The layout gate checks all three: a body, text or wire past the frame or
  on the title block is a reported defect, like a wire through a part.

## 2b. Direction and flow

- **GND symbols point down. Rail symbols point up. No exceptions.** Power
  flows downward through a sheet: rails at the top of a part or a cluster,
  GND at the bottom. A GND on a top-edge pin or a rail on a bottom-edge pin
  is drawn with a jog (stub, run sideways, symbol the right way up), not by
  rotating the symbol. A connector that must face the other way is
  **mirrored, not rotated**, so its VTref stays on top and its GND pins stay
  on the bottom.
- **Spread the power symbols out.** One GND symbol at the end of a ladder's
  GND rail, one at the end of a strap bus, one at the bottom of a part; not
  a GND symbol under every capacitor, and not two rail names printed on top of
  each other. A symbol that would hang over the next row's wire (a header's
  GND pin above a routed pin) gets a hook: up, sideways, then the symbol.
- **A connector's VBUS lane is a short plain stretch** ending in its label,
  with the divider or whatever senses it drawn at the MCU pin that reads it;
  then the ESD array's VBUS pin, or anything else that taps the rail, lands on
  that stretch with a junction and no second label. A tap must land *before*
  a series resistor, not after it — the netlist, not the picture, is the
  check.
- **Wires go around parts, not through them.** A route to a part's pin
  arrives from the side the pin faces; a route channel runs where nothing is
  placed; a lane that would pass through another part's body means the part
  is in the wrong place. Crossing another wire is acceptable when nothing
  else works; crossing a part never is. Where wires do cross, draw the
  hop-over (KiCad: Schematic Setup → Formatting → Hop-over size, and
  `kicad-cli sch export pdf --draw-hop-over`).
- **Reference and value text sits beside its part and overlaps nothing**:
  not the part's own outline, not a neighbour's, not a wire, and not each
  other. For a multi-pin part, use the library's own field positions (they
  were placed around that outline); for a project box symbol, reference
  above the top-left corner, value above the top-right corner when the top
  edge has no pins and the body is wide enough for both, otherwise the two
  stacked above the top-left corner (a 15 mm body cannot carry "U406" and
  "TPS2553DBV" on one line), or below the body on whichever side the bottom
  pins leave free. A part turned on its side carries both texts on the side
  that has no pins. Two-pin parts: text beside a vertical part, above a
  horizontal one, and on the side away from the pin for a part hanging off a
  lane. In-line parts on adjacent rows cannot both carry text between them
  at 2.54 mm pitch; the lower one slides along its lane past the upper one.
  A rail symbol's name is as wide as the name: two pull-ups whose rails
  stand 7.62 mm apart print "+3V3_PD" over "VBUS_IN"; space them by the
  names, not by the symbols.
- **Pin numbers are text too.** They sit along the pin outside the body and
  are checked like any other text: a route that lands on a pin from the
  side the pin does not face runs over its number, and a connector placed
  so that its pin stubs fall on a route column has its numbers crossed.

## 3. What goes on the sheet besides parts

- A **title block** with the sheet's purpose as its title, not "Sheet 3".
- A **note block** at the top-left: what the sheet does, the one or two
  decisions that are not obvious from the drawing, and anything that must be
  verified before fab. Short lines, plain language.
- **Net labels are named for what the signal is**, from the point of view of
  the board: `TGT_UART_TX` is the board transmitting. Active-low signals end
  in `_N`. Rails are `+3V3`, `+5V_PORTS`, `VBUS_IN`: sign, voltage, qualifier.
- **Reference designators are numbered by sheet** — `R1xx` on sheet 1, `R2xx`
  on sheet 2 — so a designator tells you where to look.
- **Values carry the voltage rating where it matters** (`10u/50V`) and the
  tolerance where it matters (`6.49k 1%`).

## 4. Things the reference does that we keep

- Series resistors drawn in line with the wire they are in series with.
- **Power connections flow upward, grounds downward.** A pull-up is a
  vertical resistor with its rail symbol directly above and a junction dot on
  the wire; a pull-down, a bias resistor or a capacitor to ground hangs below
  its wire with the GND symbol under it. A resistor drawn in line with a rail
  symbol at the end of the wire belongs only to a true series path — an LED
  and its resistor to the rail, a discharge path — never to a pull-up or
  pull-down. Where a hanging pull would push a fixed neighbour (a connector's
  CC pull-downs beside its D± rows), drawing it flat is the tolerated
  exception, and only there.
- Sense dividers: the top resistor hangs up to its rail, the bottom one down
  to GND, both from the tap; the tap's wire carries the label or goes to its
  pin. On a connector's VBUS lane the divider moves to the MCU pin that reads
  it, so the lane stays a short plain stretch.
- A hanging part's text sits beside the part, on the side away from the pin;
  an in-line part's reference and value sit above it (reference left of
  centre, value right of it, both at 1.0 mm), inside the row pitch, never on
  the next row. The next element on the lane starts past that text.
- One junction dot per T; none at corners.
- Unused pins marked with a no-connect cross at the pin, never left bare.
- A small note next to any strap or jumper saying what each setting means.

## 5. Component values come from the reference design

Every IC's surrounding parts start from the implementation its maker has
proven: the datasheet's typical application or design example, and the
evaluation board where its schematic is published. Copy the values, then
record, per IC, the source, what it specifies, what the board does and why
any difference exists (`docs/reference-design-review.md` on the baseboard).
A value that was reasoned out rather than taken from the reference is a
placeholder until it is checked against one. This is cheaper than the churn
of finding out at bring-up: the first pass of the baseboard had a buck
compensation guessed, a PHY management pull-up five times too weak, a
hub's VBUS detect divider above its supply, and a UART bridge without its
USB series resistors, all caught by this comparison.

## 6. Things we do not do

- A label on every pin with the parts floating elsewhere. That is a netlist
  printed as a picture, and nobody can review it.
- Rail symbols at every power pin when a bus would do.
- Wires that cross a block of unrelated parts to reach their destination —
  move the destination.
