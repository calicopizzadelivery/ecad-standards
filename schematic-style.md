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
  not ten GND symbols stepping down the page.
- **Two parts on one footprint are two units**: a stacked USB-A receptacle
  is drawn as two single-port connectors with the same reference, so each
  port's switch, ESD and receptacle form one cluster.
- **Crystals and their load capacitors** sit together beside the XTAL pins;
  reset circuits beside the reset pin.
- **Sub-circuits are clusters.** A port is its switch, its ESD, its connector
  and its LED in one group, repeated per port, aligned so the eye can diff
  them.

## 2a. Direction and flow

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
  not the part's own outline, not a neighbour's, not a wire. For a multi-pin
  part, use the library's own field positions (they were placed around that
  outline); for a project box symbol, reference above the top-left corner,
  value above the top-right corner when the top edge has no pins, otherwise
  below the body on whichever side the bottom pins leave free. Two-pin
  parts: text beside a vertical part, above a horizontal one, and on the
  side away from the pin for a part hanging off a lane. In-line parts on
  adjacent rows cannot both carry text between them at 2.54 mm pitch; the
  lower one slides along its lane past the upper one.

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
- Pull-ups vertical, rail symbol directly above, junction dot on the wire.
  A pull-up on a net that continues elsewhere may instead be drawn flat:
  pin — net label on the wire — resistor — rail. It reads the same and needs
  no vertical room, which matters next to a tall neighbour.
- Sense dividers flat: rail — R — tap (label on the wire) — R — GND.
- A hanging part's text sits beside the part, on the side away from the pin;
  an in-line part's reference and value sit above it (reference left of
  centre, value right of it, both at 1.0 mm), inside the row pitch, never on
  the next row. The next element on the lane starts past that text.
- One junction dot per T; none at corners.
- Unused pins marked with a no-connect cross at the pin, never left bare.
- A small note next to any strap or jumper saying what each setting means.

## 5. Things we do not do

- A label on every pin with the parts floating elsewhere. That is a netlist
  printed as a picture, and nobody can review it.
- Rail symbols at every power pin when a bus would do.
- Wires that cross a block of unrelated parts to reach their destination —
  move the destination.
