#!/usr/bin/env python3
"""Route what the lanes left with FreeRouting (layout.md section 5), keeping every locked track and via.

    autoroute.py BOARD.kicad_pcb FREEROUTING_BIN [--passes N] [--threads N]

Exports the board to Specctra (locked tracks go out as fixed, planes as planes), runs FreeRouting headless,
imports the session back, writes the board in canonical order (placer.canonical) and runs the DRC gate.
"""
import os, re, sys, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collections
import pcbnew


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board"); ap.add_argument("freerouting")
    ap.add_argument("--passes", type=int, default=50); ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=14400, help="seconds FreeRouting may run (a 140 x 100 mm board with 500 connections takes two to three hours)")
    ap.add_argument("--plane-layers", nargs="*", default=["In1.Cu", "In2.Cu"], help="layers the router may not route on (planes only)")
    ap.add_argument("--keepout-grow", type=float, default=2.0, help="a rule area named for a .kicad_dru rule goes out as a hard keep-out grown by this much (mm)")
    ap.add_argument("--max-width", type=float, default=2.0, help="cap on the class track widths given to the router (mm): the rails carry the current through the rail vias; the 6 A class is capped")
    ap.add_argument("--clearance-margin", type=int, default=10, help="um added to every clearance the router sees (0: the project's figures as they are)")
    ap.add_argument("--small-vias", action="store_true", help="every class may also use the default (smallest) via: a class via that fits nowhere beside a pad fails the connection")
    ap.add_argument("--fanout", choices=["on", "off"], default=None, help="FreeRouting's fanout stage (escape vias from SMD pads before routing; on by default in 2.4), through the design's autoroute_settings")
    ap.add_argument("--via-costs", type=int, default=None, help="FreeRouting's via cost (50 by default: lower, more layer changes and often more connections)")
    ap.add_argument("--ripup-costs", type=int, default=None, help="FreeRouting's starting rip-up cost (100 by default)")
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
    if a.clearance_margin:                                            # over the project's figure (the typed smd_smd quarter left alone): the router's rounding then never trips DRC
        txt = re.sub(r"\(clearance (\d+)\)", lambda m: f"(clearance {int(m.group(1)) + a.clearance_margin})", txt)
    if a.small_vias:                                                  # the default via joins every class's use_via list, after the class's own
        vias = re.findall(r'"(Via\[[^"]*?_(\d+):(\d+)_um)"', txt)
        if vias:
            small = min({v for v in vias}, key=lambda v: int(v[1]))[0]
            txt = re.sub(r'\(use_via "([^"]+)"\)', lambda m: m.group(0) if m.group(1) == small else f'(use_via "{m.group(1)}" "{small}")', txt)
    if a.fanout or a.via_costs is not None or a.ripup_costs is not None:   # read by FreeRouting from the structure: "Applied DSN autoroute settings to routing job"
        block = (f"(autoroute_settings (fanout {a.fanout or 'on'}) (autoroute on) (postroute on) (vias on) (via_costs {a.via_costs if a.via_costs is not None else 50}) "
                 f"(plane_via_costs 5) (start_ripup_costs {a.ripup_costs if a.ripup_costs is not None else 100}) (start_pass_no 1))")
        txt = txt.replace("(structure\n", "(structure\n    " + block + "\n", 1)
    open(dsn, "w", encoding="utf-8").write(txt)
    print(f"exported {os.path.basename(dsn)} with {fixed} fixed items, {dropped} corridors left out, {hardened} regions hardened, widths capped at {a.max_width} mm, planes on {' '.join(a.plane_layers)}; routing up to {a.passes} passes")
    try:
        r = subprocess.run([a.freerouting, "-de", dsn, "-do", ses, "-mp", str(a.passes), "-mt", str(a.threads)],
                           capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"FreeRouting did not finish within {a.timeout} s: raise --timeout or lower --passes (the session file is written only at the end)")
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
    # only where the lanes reach every pad of the net: a pair net with a pad off the lane (a shunt capacitor the directives
    # did not put on the lane's path) keeps the router's copper, and the sweep says so
    locked = [t for t in b.GetTracks() if t.IsLocked()]
    def reached(pad):
        """A locked track ends inside the pad, or a locked via sits in it (a lane ends inside the pad, not at its centre)."""
        for t in locked:
            if str(t.GetNetname()) != str(pad.GetNetname()):
                continue
            if t.GetClass() == "PCB_VIA" and pad.HitTest(t.GetPosition()):
                return True
            if t.GetClass() == "PCB_TRACK" and (pad.HitTest(t.GetStart()) or pad.HitTest(t.GetEnd())):
                return True
        return False
    off_lane = {}
    for pad in b.GetPads():
        n = str(pad.GetNetname())
        if n in pair_nets and not reached(pad):
            off_lane.setdefault(n, []).append(f"{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}")
    swept = pair_nets - set(off_lane)
    removed = 0
    for item in list(b.GetTracks()):
        if not item.IsLocked() and str(item.GetNetname()) in swept:
            b.Remove(item); removed += 1
    if removed:
        print(f"   {removed} router tracks and vias on pair nets removed (the lanes carry the pairs)")
    for n in sorted(off_lane):
        print(f"   pair net {n}: pads off the lane ({' '.join(off_lane[n])}), the router's copper kept")
    if removed:
        pcbnew.SaveBoard(board_path, b, True)
    # the rest in yet another process: a board reloaded after items were removed comes back as a bare pointer too
    subprocess.run([sys.executable, os.path.abspath(__file__), "--post2", board_path], check=True)


def post2(board_path):
    """The floors, the canonical save and the DRC gate, in a process that has removed nothing."""
    b = pcbnew.LoadBoard(board_path)
    floor = b.GetDesignSettings().m_TrackMinWidth                   # the router's pad-entry stubs can come in under the fab's floor
    widened = 0
    for tr in b.GetTracks():
        if tr.GetClass() == "PCB_TRACK" and 0 < tr.GetWidth() < floor:
            tr.SetWidth(floor); widened += 1
    if widened:
        print(f"   {widened} stubs widened to the {floor / 1e6:.3f} mm floor")
    # the router necks a wide class down to the pad's width at a pad entry and sometimes runs on at that width: every
    # unlocked segment on a class wider than the default that is narrower than the class (capped at the 2 mm the router
    # is given) and touches no pad of its net is listed for the hand pass
    ds = b.GetDesignSettings(); cap = int(2.0e6)
    pads = collections.defaultdict(list)
    for pad in b.GetPads():
        pads[str(pad.GetNetname())].append(pad)
    def beyond(tr, pads_of_net):
        """The length of the segment outside every pad of its net, sampled every 0.05 mm."""
        a, e = tr.GetStart(), tr.GetEnd(); n = max(2, int(tr.GetLength() / 50000)); out = 0
        for k in range(n):
            x, y = a.x + (e.x - a.x) * (k + 0.5) / n, a.y + (e.y - a.y) * (k + 0.5) / n
            if not any(p.HitTest(pcbnew.VECTOR2I(int(x), int(y))) for p in pads_of_net):
                out += 1
        return tr.GetLength() / 1e6 * out / n
    necks = collections.defaultdict(lambda: [0, 0.0])
    for tr in b.GetTracks():
        if tr.GetClass() != "PCB_TRACK" or tr.IsLocked():
            continue
        nc = tr.GetNetClassName(); cw = ds.m_NetSettings.GetNetclasses()[nc].GetTrackWidth() if nc in ds.m_NetSettings.GetNetclasses() else 0
        if cw <= ds.m_NetSettings.GetDefaultNetclass().GetTrackWidth() or tr.GetWidth() >= min(cw, cap) - 10000:
            continue
        n = str(tr.GetNetname()); over = beyond(tr, pads[n])
        if over <= 1.0:
            continue                                                  # the entry stub itself: a pad's width until the wide track clears the neighbours
        necks[n][0] += 1; necks[n][1] += over
    if necks:
        print("   router necks below the class width more than 1 mm beyond the pads (for the hand pass): " +
              ", ".join(f"{n} {k} segments, {l:.1f} mm beyond" for n, (k, l) in sorted(necks.items())))
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
    # a router track DRC faults (a clearance the router's rounding missed) is not finished copper: it comes out, in a
    # fresh process, and the gate runs again; the connection joins the hand pass's list
    subprocess.run([sys.executable, os.path.abspath(__file__), "--post3", board_path], check=True)


def post3(board_path):
    """Remove the unlocked tracks and vias the DRC report faults (errors other than unconnected items), then gate again."""
    rep = os.path.join(os.path.dirname(board_path), "drc.txt")
    if not os.path.exists(rep):
        return
    txt = open(rep, encoding="utf-8").read()
    faulted = []                                                      # (kind, x, y, net, layer, length) the report names, in mm
    for block in re.split(r"\n(?=\[)", txt):
        head = block.splitlines()[0] if block else ""
        if not head.startswith("[") or head.startswith("[unconnected_items]") or "; error" not in block:
            continue
        for m in re.finditer(r"@\(([-\d.]+) mm, ([-\d.]+) mm\): (Track|Via) \[([^\]]*)\] on ([\w.]+(?: - [\w.]+)?)(?:, length ([\d.]+) mm)?", block):
            faulted.append((m.group(3), float(m.group(1)), float(m.group(2)), m.group(4), m.group(5), float(m.group(6)) if m.group(6) else None))
    if not faulted:
        return
    def near(a, b):
        return abs(a - b) < 0.002
    b = pcbnew.LoadBoard(board_path); removed = 0
    for item in list(b.GetTracks()):                                  # the item the report names: kind, a position, net, layer and length
        if item.IsLocked():
            continue
        net = str(item.GetNetname())
        if item.GetClass() == "PCB_VIA":
            x, y = item.GetPosition().x / 1e6, item.GetPosition().y / 1e6
            hit = any(k == "Via" and n == net and near(x, fx) and near(y, fy) for k, fx, fy, n, _, _ in faulted)
        else:
            pts = [(item.GetStart().x / 1e6, item.GetStart().y / 1e6), (item.GetEnd().x / 1e6, item.GetEnd().y / 1e6)]
            layer, length = b.GetLayerName(item.GetLayer()), item.GetLength() / 1e6
            hit = any(k == "Track" and n == net and l == layer and (fl is None or near(length, fl)) and any(near(x, fx) and near(y, fy) for x, y in pts)
                      for k, fx, fy, n, l, fl in faulted)
        if hit:
            b.Remove(item); removed += 1
    if removed:
        pcbnew.SaveBoard(board_path, b, True)
        print(f"   {removed} router tracks and vias in DRC violation removed: those connections join the hand pass")
        subprocess.run([sys.executable, os.path.abspath(__file__), "--post2", board_path], check=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--post":
        post(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--post2":
        post2(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--post3":
        post3(sys.argv[2])
    else:
        main()
