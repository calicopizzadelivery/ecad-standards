#!/usr/bin/env python3
"""The standard's engine talking to the running KiCad PCB editor over KiCad's IPC API (kicad-python), so the hand
pass is checked while it happens instead of after a save and a regeneration.

    tools/live.py poses [--check]                 the live placement (ref x y rot side); --check: against the saved file
    tools/live.py check DIRECTIVES_DIR [--select] the lane check (lanecheck.py) on the live placement; --select picks out the
                                                  parts it names in the editor
    tools/live.py select REF [REF ...]            select parts in the editor (the owner's eye goes to them)
    tools/live.py clear                           clear the editor's selection
    tools/live.py harvest DIRECTIVES_DIR          handplace.py over the live placement, without saving the board first

Needs KiCad 9 or later with the API switched on (Preferences > Plugins > Enable KiCad API) and the board open in the PCB
editor, and the kicad-python package (tools/bootstrap.sh makes a venv with it beside the system's pcbnew module).
Reads only: the tool never writes the board; what it checks is written to a scratch copy."""
import argparse, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def connect():
    try:
        from kipy import KiCad
    except ImportError:
        raise SystemExit("kicad-python is not installed: run tools/bootstrap.sh and use its .venv/bin/python")
    try:
        k = KiCad()
        k.ping()
    except Exception as e:
        raise SystemExit(f"no KiCad answering on the API socket ({e}): is the API enabled in Preferences > Plugins, and the board open?")
    try:
        return k, k.get_board()
    except Exception as e:
        raise SystemExit(f"KiCad is running but no board is open in the PCB editor ({e})")


def live_poses(board):
    """{ref: (x, y, rot, side)} off the open board, mm and degrees in (-180, 180]."""
    from kipy.board_types import BoardLayer
    out = {}
    for fp in board.get_footprints():
        rot = round(fp.orientation.degrees, 6) % 360
        out[fp.reference_field.text.value] = (fp.position.x / 1e6, fp.position.y / 1e6, rot if rot <= 180 else rot - 360,
                                              "B" if fp.layer == BoardLayer.BL_B_Cu else "F")
    return out


def board_path(board):
    """The open board's file: the API gives the file name and the project folder separately."""
    doc = board.document
    if not doc.board_filename:
        raise SystemExit("the API gave no file name for the open board")
    return os.path.join(doc.project.path, doc.board_filename) if doc.project.path else doc.board_filename


def stale(board, poses):
    """The parts whose live pose differs from the saved file by less than a micrometre: the editor holds an older copy
    (the file was regenerated after it loaded), and a live harvest would carry the old nanometres."""
    import pcbnew
    b = pcbnew.LoadBoard(board_path(board))
    out = []
    for fp in b.GetFootprints():
        p = poses.get(fp.GetReference())
        if p and (abs(p[0] * 1e6 - fp.GetPosition().x) > 0.5 or abs(p[1] * 1e6 - fp.GetPosition().y) > 0.5) \
                and abs(p[0] * 1e6 - fp.GetPosition().x) < 1000 and abs(p[1] * 1e6 - fp.GetPosition().y) < 1000:
            out.append(fp.GetReference())
    return out


def scratch_board(board, poses, folder):
    """A copy of the saved board with the live poses applied (pcbnew), for the checks and the harvest."""
    import pcbnew
    src = board_path(board)
    b = pcbnew.LoadBoard(src)
    for fp in b.GetFootprints():
        p = poses.get(fp.GetReference())
        if p is None:
            continue
        x, y, rot, side = p
        if (side == "B") != fp.IsFlipped():
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rot); fp.SetPosition(pcbnew.VECTOR2I(int(round(x * 1e6)), int(round(y * 1e6))))
    out = os.path.join(folder, os.path.basename(src))
    pcbnew.SaveBoard(out, b)
    for f in os.listdir(os.path.dirname(src)):                        # the net classes come from the project file beside the board
        if f.endswith(".kicad_pro"):
            shutil.copy(os.path.join(os.path.dirname(src), f), folder)
    return out


def select(board, refs):
    fps = [fp for fp in board.get_footprints() if fp.reference_field.text.value in refs]
    board.clear_selection()
    if fps:
        board.add_to_selection(fps)
    return [fp.reference_field.text.value for fp in fps]


def cmd_poses(a):
    k, board = connect()
    poses = live_poses(board)
    sys.path.insert(0, HERE)
    import handplace
    if a.check:
        saved = handplace.poses(board_path(board))
        moved = sorted((r for r in poses if r in saved and handplace.differs(poses[r], saved[r])), key=handplace.refkey)
        for r in moved:
            print(f"{r}: live {tuple(round(v, 3) if isinstance(v, float) else v for v in poses[r])} saved {tuple(round(v, 3) if isinstance(v, float) else v for v in saved[r])}")
        print(f"live board against the saved file: {len(moved)} moved, {len([r for r in poses if r not in saved])} not in the file, {len([r for r in saved if r not in poses])} not on the live board")
        old = stale(board, poses)
        if old:
            print(f"the editor's copy is older than the file by under a micrometre in {len(old)} parts: reload the board in the editor before a live harvest")
        return 1 if moved else 0
    for r in sorted(poses, key=handplace.refkey):
        x, y, rot, side = poses[r]
        print(f"{r:6s} {x:8.3f} {y:8.3f} {rot:7.1f} {side}")
    return 0


def cmd_check(a):
    k, board = connect()
    sys.path.insert(0, HERE)
    import lanecheck
    with tempfile.TemporaryDirectory(prefix="live-") as folder:
        ctx = lanecheck.Context(scratch_board(board, live_poses(board), folder), a.gen_dir)
        findings, lines = lanecheck.check(ctx, getattr(ctx.L, "LANES", {}))
    print("\n".join(lines))
    refs = sorted({f["ref"] for f in findings if f["ref"] and f["kind"] in ("through", "via", "under")})
    if a.select:
        picked = select(board, set(refs))
        print(f"selected in the editor: {' '.join(picked) or 'nothing'}")
    elif refs:
        print(f"parts on a lane: {' '.join(refs)}")
    return 1 if findings else 0


def cmd_select(a):
    k, board = connect()
    picked = select(board, set(a.refs))
    missing = [r for r in a.refs if r not in picked]
    print(f"selected: {' '.join(picked) or 'nothing'}" + (f"; not on the board: {' '.join(missing)}" if missing else ""))
    return 0


def cmd_clear(a):
    k, board = connect()
    board.clear_selection(); print("selection cleared")
    return 0


def cmd_harvest(a):
    k, board = connect()
    sys.path.insert(0, HERE)
    import handplace
    poses = live_poses(board)
    old = stale(board, poses)
    if old:
        print(f"the editor's copy is older than the file by under a micrometre in {len(old)} parts ({' '.join(old[:6])}{' ...' if len(old) > 6 else ''}): "
              "reload the board in the editor (File > Revert) and harvest again")
        return 1
    with tempfile.TemporaryDirectory(prefix="live-") as folder:
        return handplace.harvest(scratch_board(board, poses, folder), os.path.abspath(a.gen_dir))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poses"); p.add_argument("--check", action="store_true"); p.set_defaults(fn=cmd_poses)
    p = sub.add_parser("check"); p.add_argument("gen_dir"); p.add_argument("--select", action="store_true"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("select"); p.add_argument("refs", nargs="+"); p.set_defaults(fn=cmd_select)
    p = sub.add_parser("clear"); p.set_defaults(fn=cmd_clear)
    p = sub.add_parser("harvest"); p.add_argument("gen_dir"); p.set_defaults(fn=cmd_harvest)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
