#!/usr/bin/env python3
"""Turn the owner's hand placement into directives.

    tools/handplace.py BOARD.kicad_pcb [--gen DIR]            harvest: write DIR/hand_placement.py, update CONNECTORS
    tools/handplace.py BOARD.kicad_pcb --check GEN.kicad_pcb  verify: list the parts the generated board places elsewhere

The harvest reads every footprint's pose (x, y, rotation, side) off the hand-placed board and writes them to
DIR/hand_placement.py as `HAND = {ref: (x, y, rot, side)}`; the directives merge HAND over FIXED, and a fixed part
outranks its anchor and the engine's own rule. The edge connectors (CONNECTORS in DIR/layout.py) keep their own table,
which the lanes and the edge rules read, so their tuples are rewritten in place; the mounting holes (HOLES) are not
footprints the engine places and are skipped. The harvest prints what moved since the previous harvest, so the commit
that carries it can say so. After regenerating, `--check` compares the generated board with the hand-placed one and
exits 1 if any part sits elsewhere, is missing, or is on the generated board only: the generator must reproduce the
hand placement exactly. The directives import the file with a guard, so the first harvest of a project works:

    try:
        from hand_placement import HAND
    except ImportError:
        HAND = {}
    FIXED = {**FIXED, **HAND}

The harvest takes the parts' poses only: the designators follow the silkscreen rule (layout.md section 6) on every
regeneration, and the routing is the router's and the hand pass's (section 2, step 10).
"""
import argparse, os, re, sys


def poses(path):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    out = {}
    for fp in b.GetFootprints():
        rot = round(fp.GetOrientationDegrees(), 1) % 360
        out[fp.GetReference()] = (fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6,                    # exact: nm in the file
                                  rot if rot <= 180 else rot - 360, "B" if fp.IsFlipped() else "F")
    return out


def mm(v):
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def differs(a, b):
    return abs(a[0] - b[0]) > 0.0005 or abs(a[1] - b[1]) > 0.0005 or abs(((a[2] - b[2]) + 180) % 360 - 180) > 0.05 or a[3] != b[3]


def refkey(ref):
    return re.sub(r"\d", "", ref), int(re.sub(r"\D", "", ref) or 0)


def harvest(board, gen_dir):
    hand = poses(board)
    sys.path.insert(0, gen_dir)
    import layout
    try:
        from hand_placement import HAND as earlier
    except ImportError:
        earlier = {}
    entries, connectors = {}, {}
    for r in sorted(hand, key=refkey):
        x, y, rot, side = hand[r]
        if r in layout.CONNECTORS:
            if side == "B":
                raise SystemExit(f"{r} is an edge connector on the bottom; the edge rule (layout.md 3.4) keeps them on top")
            if differs(hand[r], (*layout.CONNECTORS[r][:3], "F")):
                connectors[r] = (x, y, rot)
        elif r not in layout.HOLES:
            entries[r] = (x, y, rot, side)
    path = os.path.join(gen_dir, "layout.py")
    text = open(path, encoding="utf-8").read()
    if connectors:                                                # rewrite inside the CONNECTORS table only
        m = re.search(r"^CONNECTORS\s*=\s*\{", text, re.M)
        assert m, "layout.py has no CONNECTORS table"
        end = text.index("\n}", m.end())
        block = text[m.end():end]
        for r, (x, y, rot) in connectors.items():
            block, n = re.subn(r'"%s": \([^)]*\)' % re.escape(r), f'"{r}": ({mm(x)}, {mm(y)}, {rot:g})', block, count=1)
            assert n == 1, f"CONNECTORS has no entry for {r}"
        text = text[:m.end()] + block + text[end:]
        open(path, "w", encoding="utf-8").write(text)
    with open(os.path.join(gen_dir, "hand_placement.py"), "w", encoding="utf-8") as f:
        f.write('"""The owner\'s hand placement, harvested from the saved board by the standard\'s tools/handplace.py: every part\'s pose\n'
                '(x, y, rotation, side) as FIXED entries. layout.py merges them over FIXED; a fixed part outranks its anchor and the\n'
                'engine\'s own rule. Move parts in KiCad and harvest again rather than editing this file."""\n')
        f.write("HAND = {\n")
        for r, (x, y, rot, side) in entries.items():
            f.write(f'    "{r}": ({mm(x)}, {mm(y)}, {rot:g}, "{side}"),\n')
        f.write("}\n")
    moved = sorted((r for r in entries if r in earlier and differs(entries[r], earlier[r])), key=refkey)
    new = sorted((r for r in entries if r not in earlier), key=refkey)
    gone = sorted((r for r in earlier if r not in entries), key=refkey)
    print(f"hand_placement.py: {len(entries)} parts ({sum(1 for v in entries.values() if v[3] == 'B')} on the bottom); "
          f"connectors rewritten in layout.py: {', '.join(connectors) or 'none'}")
    print(f"since the previous harvest: {len(moved)} moved ({' '.join(moved) or '-'}); {len(new)} new ({' '.join(new) or '-'}); "
          f"{len(gone)} gone ({' '.join(gone) or '-'})")
    return 0


def check(board, gen):
    hand, out = poses(board), poses(gen)
    missing = sorted((r for r in hand if r not in out), key=refkey)
    extra = sorted((r for r in out if r not in hand), key=refkey)
    moved = sorted((r for r in hand if r in out and differs(hand[r], out[r])), key=refkey)
    for r in moved:
        print(f"{r}: hand {hand[r]} generated {out[r]}")
    print(f"generated board against the hand placement: {len(moved)} placed elsewhere, {len(missing)} missing"
          + (f" ({' '.join(missing)})" if missing else "") + f", {len(extra)} not on the hand-placed board" + (f" ({' '.join(extra)})" if extra else ""))
    return 1 if moved or missing or extra else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help="the hand-placed board")
    ap.add_argument("--gen", default=".", help="the directives directory (layout.py; hand_placement.py is written beside it)")
    ap.add_argument("--check", metavar="GEN.kicad_pcb", help="compare the generated board with the hand-placed one instead of harvesting")
    a = ap.parse_args()
    sys.exit(check(a.board, a.check) if a.check else harvest(a.board, os.path.abspath(a.gen)))
