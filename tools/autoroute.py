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
    ap.add_argument("--plane-layers", nargs="*", default=["In1.Cu", "In2.Cu"], help="layers the router may not route on (planes only)")
    ap.add_argument("--keepout-grow", type=float, default=2.0, help="a rule area named for a .kicad_dru rule goes out as a hard keep-out grown by this much (mm)")
    ap.add_argument("--max-width", type=float, default=2.0, help="cap on the class track widths given to the router (mm): the rails carry the current through the rail vias; the 6 A class is capped")
    a = ap.parse_args()
    board_path = os.path.abspath(a.board); work = os.path.splitext(board_path)[0]
    b = pcbnew.LoadBoard(board_path)
    fixed = sum(1 for t in b.GetTracks() if t.IsLocked())
    dsn, ses = work + ".dsn", work + ".ses"
    # the export copy: KiCad writes every rule area as a blanket keep-out, so the areas that only keep parts out (the
    # lanes' corridors, an isolation region named for a rule) are dropped from it; the real board keeps them
    export = pcbnew.LoadBoard(board_path)
    dropped, hardened = 0, 0
    for z in list(export.Zones()):
        if z.GetIsRuleArea() and not z.GetDoNotAllowTracks():
            if str(z.GetZoneName()).startswith("lane"):              # a corridor for parts: the router routes through it
                export.Remove(z); dropped += 1
            else:                                                     # a region a .kicad_dru rule guards by class: the router cannot, so it keeps out, grown by the creepage
                z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True)
                z.Outline().Inflate(pcbnew.FromMM(a.keepout_grow), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.05)); hardened += 1
    if not pcbnew.ExportSpecctraDSN(export, dsn):
        raise SystemExit("DSN export failed")
    txt = open(dsn, encoding="utf-8").read()
    for layer in a.plane_layers:                                     # plane layers are not for routing: vias reach them
        txt = re.sub(r"(\(layer %s\n\s*\(type )signal" % re.escape(layer), r"\1power", txt, count=1)
    cap = int(round(a.max_width * 1000))                              # the DSN is in um
    txt = re.sub(r"\(width (\d+)\)", lambda m: f"(width {min(int(m.group(1)), cap)})", txt)
    open(dsn, "w", encoding="utf-8").write(txt)
    print(f"exported {os.path.basename(dsn)} with {fixed} fixed items, {dropped} corridors left out, {hardened} regions hardened, widths capped at {a.max_width} mm, planes on {' '.join(a.plane_layers)}; routing up to {a.passes} passes")
    r = subprocess.run([a.freerouting, "-de", dsn, "-do", ses, "-mp", str(a.passes), "-mt", str(a.threads)],
                       capture_output=True, text=True, timeout=a.timeout)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if "unrouted" in l.lower() or "completed" in l.lower()][-3:]
    for l in tail:
        print("   " + l[-160:])
    if not os.path.exists(ses):
        raise SystemExit("FreeRouting produced no session file")
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise SystemExit("SES import failed")
    pcbnew.SaveBoard(board_path, b, True)                           # save at once: the board object is not usable after the import
    # the rest in a fresh process: after ImportSpecctraSES even a reloaded board can come back as a bare SWIG pointer
    subprocess.run([sys.executable, os.path.abspath(__file__), "--post", board_path], check=True)


def post(board_path):
    """After the session import, in its own process: the router's pad-entry stubs floored to the fab's minimum width,
    the Specctra files removed, the board written in canonical order, the DRC gate run."""
    b = pcbnew.LoadBoard(board_path)
    # the pairs are the generator's: whatever the router added on a pair net (a stub to a pad centre, a second path round
    # a bridge) comes out; the locked lanes connect those nets by themselves
    pair_nets = {str(n) for n in b.GetNetsByName().keys() if str(n).endswith(("_P", "_N"))}
    pair_nets = {n for n in pair_nets if (n[:-1] + ("N" if n.endswith("P") else "P")) in pair_nets}
    removed = 0
    for item in list(b.GetTracks()):
        if not item.IsLocked() and str(item.GetNetname()) in pair_nets:
            b.Remove(item); removed += 1
    if removed:
        print(f"   {removed} router tracks and vias on pair nets removed (the lanes carry the pairs)")
        pcbnew.SaveBoard(board_path, b, True); b = pcbnew.LoadBoard(board_path)
    floor = b.GetDesignSettings().m_TrackMinWidth                   # the router's pad-entry stubs can come in under the fab's floor
    widened = 0
    for tr in b.GetTracks():
        if tr.GetClass() == "PCB_TRACK" and 0 < tr.GetWidth() < floor:
            tr.SetWidth(floor); widened += 1
    if widened:
        print(f"   {widened} stubs widened to the {floor / 1e6:.3f} mm floor")
    pcbnew.SaveBoard(board_path, b, True)
    base = os.path.splitext(board_path)[0]
    for f in (base + ".dsn", base + ".ses"):
        if os.path.exists(f):
            os.remove(f)
    import placer
    text = open(board_path, encoding="utf-8").read()
    placer.PROJECT = os.path.splitext(os.path.basename(board_path))[0]
    open(board_path, "w", encoding="utf-8").write(placer.canonical(text))
    text = open(board_path, encoding="utf-8").read()
    print(f"imported the session: {text.count(chr(10) + chr(9) + '(segment')} tracks, {text.count(chr(10) + chr(9) + '(via')} vias")
    placer.drc_gate(board_path)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--post":
        post(sys.argv[2])
    else:
        main()
