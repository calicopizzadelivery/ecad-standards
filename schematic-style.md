# Schematic style

The reference is the OpenPilot Revolution flight controller schematic (Altium,
one A3 sheet, D. Ankers for the OpenPilot project): an STM32 in the middle,
every peripheral wired to it, nothing floating. These rules are what makes
that sheet readable, written down so a generator or a person can reproduce it.

## 1. The hub-and-fan-out model

Every sheet has a **hub**: the component the sheet is about — the MCU, the
hub controller, the PHY. It sits near the middle. Its pins **fan out** as
wires: each wire leaves the pin, turns once, and runs to the thing it
connects to, with the wires spreading from the pin pitch to whatever spacing
the attached parts need. Lanes keep the pin order, so they never cross each
other; the outermost pins turn first.

What a wire may end in — the **attachments**, in order of preference:

1. **A pin of another part on the sheet.** Connectors, the ESD array between a
   port and its connector, the level translator between the MCU and a header.
   Drawn as a wire, routed Manhattan, one bend where the rows do not line up.
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
- **Decoupling sits beside the IC it decouples**, as a row of capacitors
  under one wire from the rail symbol, each capacitor to its own GND symbol.
  Not scattered, not labelled.
- **Same-rail power pins share a bus**: VDD pins tied together with one
  vertical wire and one rail symbol at its end, not six rail symbols.
- **Crystals and their load capacitors** sit together beside the XTAL pins;
  reset circuits beside the reset pin.
- **Sub-circuits are clusters.** A port is its switch, its ESD, its connector
  and its LED in one group, repeated per port, aligned so the eye can diff
  them.

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
- One junction dot per T; none at corners.
- Unused pins marked with a no-connect cross at the pin, never left bare.
- A small note next to any strap or jumper saying what each setting means.

## 5. Things we do not do

- A label on every pin with the parts floating elsewhere. That is a netlist
  printed as a picture, and nobody can review it.
- Rail symbols at every power pin when a bus would do.
- Wires that cross a block of unrelated parts to reach their destination —
  move the destination.
