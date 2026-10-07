#!/usr/bin/env python3
"""Route what the lanes left with FreeRouting (layout.md section 5), keeping every locked track and via.

    autoroute.py BOARD.kicad_pcb FREEROUTING_BIN [--passes N] [--threads N]

Exports the board to Specctra (locked tracks go out as fixed, planes as planes), runs FreeRouting headless,
imports the session back, writes the board in canonical order (placer.canonical) and runs the DRC gate.
"""
import os, re, sys, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board"); ap.add_argument("freerouting")
    ap.add_argument("--passes", type=int, default=50); ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args()
    board_path = os.path.abspath(a.board); work = os.path.splitext(board_path)[0]
    b = pcbnew.LoadBoard(board_path)
    fixed = sum(1 for t in b.GetTracks() if t.IsLocked())
    dsn, ses = work + ".dsn", work + ".ses"
    if not pcbnew.ExportSpecctraDSN(b, dsn):
        raise SystemExit("DSN export failed")
    print(f"exported {os.path.basename(dsn)} with {fixed} fixed items; routing up to {a.passes} passes")
    r = subprocess.run([a.freerouting, "-de", dsn, "-do", ses, "-mp", str(a.passes), "-mt", str(a.threads)],
                       capture_output=True, text=True, timeout=a.timeout)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if "unrouted" in l.lower() or "completed" in l.lower()][-3:]
    for l in tail:
        print("   " + l[-160:])
    if not os.path.exists(ses):
        raise SystemExit("FreeRouting produced no session file")
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise SystemExit("SES import failed")
    pcbnew.SaveBoard(board_path, b, True)
    os.remove(dsn); os.remove(ses)
    import placer
    t = open(board_path, encoding="utf-8").read()
    placer.PROJECT = os.path.splitext(os.path.basename(board_path))[0]
    open(board_path, "w", encoding="utf-8").write(placer.canonical(t))
    print(f"imported the session: {sum(1 for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK')} tracks, {sum(1 for t in b.GetTracks() if t.GetClass() == 'PCB_VIA')} vias")
    placer.drc_gate(board_path)


if __name__ == "__main__":
    main()
