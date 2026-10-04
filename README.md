# ecad-standards

House rules for electronic design: how a schematic should read, how a KiCad
project is laid out, and what a generator that produces either has to get
right. Written while building
[sbc-development-baseboard](https://github.com/calicopizzadelivery/sbc-development-baseboard),
and meant to be the standard every project after it follows.

| Document | What it settles |
|---|---|
| [schematic-style.md](schematic-style.md) | How a sheet is composed so a human can read it: the hub-and-fan-out model, where connectors go, when a net label is allowed |
| [kicad-project.md](kicad-project.md) | Repository layout, sheet split, reference designator ranges, what gets committed, the verify-before-fab list |
| [kicad-generation.md](kicad-generation.md) | What we learned about the KiCad 10 file format and `kicad-cli` while generating schematics programmatically — the facts a generator must obey |

The short version of the style: **a sheet is organised around its main
component.** Nets leave that component as wires, fanning out in order, and
whatever a net connects to is drawn at the end of its wire — a connector at
the page edge, a series part in line, a pull-up hanging from its rail. Parts
do not float; labels are for nets that leave the page.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
