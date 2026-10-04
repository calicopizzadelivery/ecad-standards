# KiCad project layout

```
<project>/
  docs/hardware-spec.md        the specification the schematic implements
  docs/protocol.md             if the board has a console
  docs/block-diagram.{py,svg,png}   kept current with every configuration change
  hardware/kicad/README.md     how the KiCad project came to be, and the handoff rule
  hardware/kicad/<board>/      the KiCad project: .kicad_pro, root .kicad_sch, one .kicad_sch
                               per sub-sheet, project .kicad_sym, sym-lib-table, the ERC
                               report, the PDF, the BOM
  hardware/kicad/gen/          the generator, if one produced the first pass
  hardware/datasheets/         the parts that matter
```

## Sheets

One root sheet with the sheet index, the mounting holes and the
verify-before-fab list; one sub-sheet per functional block, named for the
block (`power`, `mcu`, `hub`, not `sheet2`). A3 landscape. Each sub-sheet's
title says what it is for.

## Reference designators

Numbered by sheet: `R1xx`, `C1xx`, `U1xx` on the first sub-sheet, `2xx` on the
second, and so on. Connectors keep the J-numbers the specification gave them
(`J1` is always the upstream USB-C), because people wire to them by number.
Relays are `K`, crystals `Y`, solder jumpers `JP`, holes `H`.

## What is committed

Everything KiCad needs to open the project, plus the artefacts a reviewer
needs without KiCad: `erc.txt`, the PDF, `bom.csv`. Not committed:
`*.kicad_prl`, `*-backups/`, `fp-info-cache`.

## The handoff rule

If a generator produced the first pass, say so in `hardware/kicad/README.md`,
and say that **the KiCad files become the source of truth the moment anyone
edits them in KiCad**. Re-running a generator over hand edits destroys them.

## Verify-before-fab

A list on the root sheet and in the spec's open questions: every pad mapping
inferred rather than read, every placeholder value (compensation networks),
every pin assignment taken from a third-party firmware's conventions, and
every "the datasheet should say X" that was not confirmed. The list exists so
that nobody has to remember what was assumed.
