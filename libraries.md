# Libraries

Symbols and footprints are shared through one repository,
[ecad-libraries](https://github.com/calicopizzadelivery/ecad-libraries), laid
out the way KiCad's own libraries are: `symbols/*.kicad_sym` split by
category, `footprints/*.pretty/` with one file per footprint, 3D models under
`3dmodels/` on Git LFS, and library-table fragments to copy into a project.

## Where a part's symbol comes from

In order: KiCad's standard libraries, as they ship; the house library, for
what KiCad lacks or draws in a way the sheet style cannot use; never a
third-party download pasted in unreviewed. A house symbol is built from the
part's datasheet pin table by `tools/build_symbols.py`, and its Description
says which table. The builder, not the `.kicad_sym` it writes, is the source
of truth: the generated files are committed next to it, `--check` fails when
they differ, and CI runs that check on every push, so nobody edits the output
by hand and loses the change at the next build.

## What a house symbol obeys

The KiCad Library Conventions (KLC) wherever they do not conflict with the
sheet style: every pin on the 100 mil grid of the origin, 2.54 mm pin length,
20 mil pin-name offset, Reference and Value placed off the pins, the five
standard fields plus keywords and a footprint filter that matches the default
footprint, a real datasheet link (never `~`, which a multi-unit symbol turns
into a library mismatch). On top of that the house rules from
schematic-style.md: pin names never overlap (rows kept clear for vertical
names), supply pins along the top and grounds along the bottom so the sheet
can bus them, a connector's pins all on the side that faces the sheet edge.
The KLC checker (`kicad-library-utils`) runs in CI as a report; the four
findings that are house decisions are listed in the library README, and
everything else it reports is a defect.

## Pin order inside a symbol

The order of pins along a side is a layout decision, not a datasheet
fact, and it is made so the sheet draws straight. A USB pair is listed D-
above D+, the order of the two lines through a USBLC6 array, so an array
placed on the pair's rows wires with no crossing. The XTAL pair goes at the
bottom of its column when the clock cluster has to hang below everything
else on that side (the USB2517). A pin whose lane carries a long chain
(a VBUS-detect divider) gets clear rows beside it where a connector placed
on the neighbouring rows has stubs of its own.

## Using the library from a project

The library is a git submodule at `hardware/kicad/libs`, pinned to a tag.
The project's `sym-lib-table` and `fp-lib-table` (copied from
`tables/`) refer to it as `${KIPRJMOD}/../libs/...`, so a clone with
`--recurse-submodules` opens in KiCad with no per-machine setup. Nothing goes
in KiCad's global tables. A project takes a newer library revision on
purpose: update the submodule pointer, then *Update Symbols from Library* in
KiCad or regenerate, and re-run the gates. A generator loads the house
symbols from the submodule's files (the baseboard's `kisym.EXTRA_LIBS`), so
the copies it embeds in the schematic are byte-equal to the library.

## Footprints and models

KiCad's footprints are used while they fit the part exactly. A footprint that
has to be made goes in `footprints/calico.pretty`, is checked with
`check_footprint.py`, and is named by the KLC pattern (IPC-7351 for
packages). 3D models go with it under `3dmodels/`, on LFS.

## Part numbers

The libraries hold geometry and pinouts, not purchasing data. When a BOM has
to carry manufacturer part numbers and suppliers, the KiCad answer is a
database library (`.kicad_dbl` over a database, a SQLite file in the same
repository will do) mapping each row to a symbol, a footprint and fields.
Not set up yet; the first project that needs it adds it here.
