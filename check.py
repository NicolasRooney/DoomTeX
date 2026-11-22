#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""Regression harness for the TeX Doom renderer.

Run this after every change.  It is deliberately strict: several of the bugs
found while building this renderer produced a PDF that compiled with zero
errors and looked plausible, and were only caught by these invariants.

    python3 check.py            # full run
    python3 check.py --quick    # skip the 320x200 benchmark
    python3 check.py --clean    # delete build/ and stop

If an IWAD (doom2.wad, doom.wad, doom1.wad) happens to be in this directory it
is exercised too.  It is not required, and is never distributed with this
project.

Everything a run writes -- some three hundred fixtures, and a log and a PDF
for each -- goes into build/, beside a copy of the engine.  Nothing is written
next to the sources.

Exit status is nonzero if any check fails.
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

VOID_RAMP = 240          # \hx{250}: the filler emitted for a column that
                         # never closed, i.e. a hole in the map
VIEWHEIGHT = 41          # \VIEWH in doom.tex
FAIL = []

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "build")
# The interpreter running this, quoted: it is used to run play.py and gif.py
# through a shell, and a Python installed under "Program Files" is not rare.
PY = f'"{sys.executable}"'
# What a fixture needs beside it: the engine, the map it is checked against,
# and the two tools this file runs as programs rather than imports.
STAGED = ("doom.tex", "wad.tex", "play.tex", "test.wad", "play.py", "gif.py")
IWADS = ("doom2.wad", "doom.wad", "doom1.wad")


def stage():
    """Make build/ a place the checks can run in, and go there.

    The engine is COPIED in on every run rather than found through
    TEXINPUTS.  A fixture says `\\input doom.tex' and \\pdffiledump takes a
    bare filename, and how either of those searches a path is one of the
    things that differs between TeX Live and MiKTeX; a file in the current
    directory is found by both.  Copying every time is also what stops a
    stale engine in build/ from being the one that gets tested.

    An IWAD is fifteen megabytes, so it is hard-linked where the filesystem
    allows and copied only where it does not -- and removed again if it has
    gone from the source directory, or the run would go on exercising a WAD
    the user thought they had taken away.
    """
    os.makedirs(BUILD, exist_ok=True)
    for f in STAGED:
        shutil.copy2(os.path.join(ROOT, f), os.path.join(BUILD, f))
    for w in IWADS:
        src, dst = os.path.join(ROOT, w), os.path.join(BUILD, w)
        if not os.path.isfile(src):
            if os.path.exists(dst):
                os.remove(dst)
            continue
        if os.path.exists(dst):
            if os.path.samefile(src, dst):
                continue
            os.remove(dst)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    os.chdir(BUILD)


def sh(cmd, stdin=None, timeout=3600):
    """Run a command through the shell and capture it.

    An hour, because the slowest fixture loads a whole commercial map on
    whatever \\pdffiledump this machine has, and MiKTeX's takes minutes over
    what TeX Live does in seconds.  A timeout that fires is an exception,
    and an exception loses every check after it.
    """
    return subprocess.run(cmd, shell=True, input=stdin, capture_output=True,
                          text=True, timeout=timeout)


# play.py says why this is extra_mem_TOP and why it is asked for two ways;
# the harness needs it for the one fixture that loads a map bigger than
# MAP01.  Worked out once, because it costs a run of `pdftex --version'.
EXTRA_MEM = 30000000
_PDFTEX = None


def pdftex():
    """The pdftex command line, with room for a large map."""
    global _PDFTEX
    if _PDFTEX is None:
        os.environ.setdefault("extra_mem_top", str(EXTRA_MEM))
        _PDFTEX = "pdftex"
        if "MiKTeX" in sh("pdftex --version").stdout:
            _PDFTEX += f" --extra-mem-top={EXTRA_MEM}"
    return _PDFTEX


def build(texfile, stdin=None, interactive=False):
    """Compile and return (seconds, error_count, log).

    Errors are counted from the .log file, not from stdout.  Under normal
    interaction mode an error prompt consumes a line of stdin, and if that
    line happens to be `q` -- which it will be, because interactive fixtures
    feed it a key sequence -- TeX switches to batchmode and every later error
    goes to the log alone.  Counting stdout made \\dtUse crash sixteen times
    while `interactive: compiles clean` reported zero.
    """
    flag = "" if interactive else "-interaction=nonstopmode"
    t0 = time.time()
    r = sh(f"{pdftex()} {flag} {texfile}", stdin=stdin)
    logfile = os.path.splitext(texfile)[0] + ".log"
    try:
        errs = len(re.findall(r"^!", open(logfile, errors="replace").read(),
                              re.M))
    except OSError:
        errs = len(re.findall(r"^!", r.stdout, re.M))
    return time.time() - t0, errs, r.stdout


def overfull(texfile):
    """Count Overfull warnings in a fixture's .log.

    From the log, not stdout: pdfTeX wraps terminal output at max_print_line
    and an interactive fixture can be switched to batchmode part way through
    by an error prompt eating a key, after which nothing reaches stdout at all.
    """
    logfile = os.path.splitext(texfile)[0] + ".log"
    try:
        return len(re.findall(r"^Overfull", open(logfile, errors="replace")
                              .read(), re.M))
    except OSError:
        return -1


def frames(pdf):
    """Decode a PDF into a list of frames, each a list of column bytestrings.

    A PDF that was never written is no frames, not an exception.  A run that
    dies before its first page leaves nothing behind, and the check reading
    it should go red on "0 frames" like any other -- which is what a mutation
    that made the key loop run off the end of its input was the first to ask.
    """
    try:
        d = open(pdf, "rb").read()
    except OSError:
        return []
    out = []
    for m in re.finditer(rb"stream\r?\n", d):
        s = m.end()
        e = d.find(b"endstream", s)
        try:
            raw = zlib.decompress(d[s:e])
        except Exception:
            continue
        if b"BI" not in raw:
            continue
        cols = re.findall(rb"ID\s+([0-9A-Fa-f\s]+?)>\s*EI", raw)
        out.append([bytes.fromhex(c.replace(b"\n", b"").replace(b" ", b"").decode())
                    for c in cols])
    return out


def track(log):
    """Per-frame (x, y, sector, floor, eye) from the render log.

    pdfTeX hard-wraps terminal output at max_print_line without inserting or
    dropping any characters, so deleting the newlines reconstructs the stream
    exactly.  Matching the unflattened log silently misses wrapped frames.
    """
    flat = log.replace("\r", "").replace("\n", "")
    return [tuple(int(v) for v in m) for m in re.findall(
        r"frame \d+ at (-?\d+),(-?\d+) sec (-?\d+) floor (-?\d+) eye (-?\d+)",
        flat)]


def hud(log):
    """Per-frame (health, armour, ammo, weapon, taken) from the render log.

    Flattened for the same reason track() flattens: pdfTeX wraps terminal
    output at max_print_line mid-token, so a frame whose line straddled a
    wrap would silently vanish from the result.
    """
    flat = log.replace("\r", "").replace("\n", "")
    return [tuple(int(v) for v in m) for m in re.findall(
        r"hp (-?\d+) ar (-?\d+) am (-?\d+) wp (-?\d+) got (-?\d+)", flat)]


# play.tex's sixteen densities, brightest first.  Written out here rather
# than parsed out of play.tex so that this file states what the picture is
# supposed to be instead of agreeing with whatever the engine now does.
DENSITY = "@@NNWMoc*+=-:.. "


def pdfpal(pdf):
    """The palette the render was actually drawn with.

    \\dtSetupPDF writes it into the PDF as an /Indexed /DeviceRGB object, so
    this works for the built-in ramps as well as for a WAD's PLAYPAL and
    cannot be given the wrong one.
    """
    d = open(pdf, "rb").read()
    m = re.search(rb"/Indexed\s*/DeviceRGB\s*255\s*<([0-9A-Fa-f\s]+)>", d)
    return bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode()) if m else None


def asciirows(log, width):
    """The engine's own ASCII viewport rows, out of a fixture's terminal.

    Identified by being exactly `width' characters and made of nothing but
    the density ramp, which nothing else pdfTeX prints can be.  Keep the
    width under max_print_line in a fixture that uses this: a wider row is
    hard-wrapped and arrives as two lines.

    The prompt has to come off first.  `\\read16 to \\dt@key' prints
    `\\dt@key=' with NO newline after it, so the first row of every frame
    but the first arrives glued to it -- and a filter looking for a line of
    pure density characters drops exactly those, quietly, giving 29 rows a
    frame instead of 30 and never saying so.  It is the same shape as §6's
    prompt trap seen from the reading end.
    """
    return [r for r in (ln.split("\\dt@key=")[-1]
                        for ln in log.replace("\r", "").split("\n"))
            if len(r) == width and all(c in DENSITY for c in r)]


def viewport(frame, pal, n, point=False):
    """What play.tex's box filter must print for this frame.

    A second implementation of \\dt@trN / \\dt@trF, from the decoded pixels
    and the decoded palette.  Every division truncates, because TeX's
    \\divide does and \\numexpr -- which rounds -- is deliberately not used
    on this path.

    `point' is the control: the top-left pixel of each block instead of the
    mean of it, i.e. the implementation this replaced.
    """
    w, h = len(frame), len(frame[0])
    lum = [(2 * pal[3 * i] + 5 * pal[3 * i + 1] + pal[3 * i + 2]) // 8
           for i in range(256)]
    rows = []
    for cy in range(h // (2 * n)):
        row = []
        for cx in range(w // n):
            if point:
                m = lum[frame[cx * n][cy * 2 * n]]
            else:
                s = sum(lum[frame[x][y]]
                        for x in range(cx * n, cx * n + n)
                        for y in range(cy * 2 * n, cy * 2 * n + 2 * n))
                m = s // (n * 2 * n)
            row.append(DENSITY[15 - min(15, m // 16)])
        rows.append("".join(row))
    return rows


def pipeframes(log):
    """The frames \\DTPIPE wrote, decoded the way play.py decodes them."""
    out, sec, w, h, buf = [], None, 0, 0, []
    for ln in log.replace("\r", "").split("\n"):
        if "[DTF]" in ln:
            w, h = (int(v) for v in re.findall(r"\d+", ln.split("[DTF]")[1])[:2])
            sec, buf = "f", []
            continue
        if "[DTE]" in ln:
            if sec == "f":
                raw = bytes.fromhex("".join(buf))
                if len(raw) == w * h:
                    out.append([raw[x * h:(x + 1) * h] for x in range(w)])
            sec = None
            continue
        # Hex and nothing else: pdfTeX's own page markers share this stream.
        if sec == "f" and ln.strip() and re.fullmatch(r"[0-9A-Fa-f]+",
                                                      ln.strip()):
            buf.append(ln.strip())
    return out


def pipestates(log):
    """The [DTS] lines, with max_print_line's wrap undone.

    A section runs from its marker to the next one and is joined with no
    separator, which is exactly what pdfTeX's hard wrap needs -- it splits
    mid-token without inserting or dropping a character.
    """
    out, cur = [], None
    for ln in log.replace("\r", "").split("\n"):
        if "[DTS]" in ln:
            cur = [ln.split("[DTS]", 1)[1]]
        elif cur is not None:
            if "[DTF]" in ln:
                out.append("".join(cur).strip())
                cur = None
            else:
                cur.append(ln)
    return out


def firstline(r):
    """A subprocess's first line of output, for a check's detail column.

    A detail is one line by convention -- anything longer breaks up the run's
    output and makes a passing check look like a stack trace.
    """
    return (r.stdout.strip().splitlines() or [r.stderr.strip()[:70]] or [""])[0]


def scaled_rows(frame, s):
    """One decoded frame as the row-major, integer-scaled plane a GIF holds.

    The PDF stores columns and a GIF stores rows, so this is the transpose
    gif.py has to do, written out again here rather than imported.
    """
    w, h = len(frame), len(frame[0])
    out = []
    for y in range(h):
        row = bytes(frame[x][y] for x in range(w) for _ in range(s))
        out.extend([row] * s)
    return b"".join(out)


def ungif(path):
    """Decode an animated GIF into a list of composited index planes.

    Written from the DECODER's side of the spec deliberately.  The one thing
    an encoder and a matching decoder can quietly agree to get wrong is when
    the codes widen: the decoder runs exactly one code behind, because the
    entry it must add ends with the first character of the code it has not
    read yet, so a decoder whose table grows in step with the encoder's is a
    decoder that agrees with a broken encoder.  gif.py carries a `+ 1' for
    that offset and this is only a check on it because it was derived from
    the other end.  (An earlier gif.py had the `+ 1' missing; every frame
    round-tripped perfectly against a decoder that shared the mistake, and a
    foreign one refused the file outright.)
    """
    d = open(path, "rb").read()
    if d[:6] != b"GIF89a" or not d[10] & 0x80:
        raise ValueError(f"{path}: not a GIF89a with a global colour table")
    w, h = d[6] | d[7] << 8, d[8] | d[9] << 8
    p = 13 + 3 * (2 << (d[10] & 7))
    out, canvas, trans, hast = [], bytearray(w * h), 0, False
    while p < len(d) and d[p] != 0x3B:
        if d[p] == 0x21:                                   # extension
            label, p = d[p + 1], p + 2
            if label == 0xF9:
                blk = d[p + 1:p + 1 + d[p]]
                hast, trans = bool(blk[0] & 1), blk[3]
            while d[p]:
                p += 1 + d[p]
            p += 1
            continue
        if d[p] != 0x2C:
            raise ValueError(f"{path}: byte {d[p]:#x} at {p}")
        iw, ih = d[p + 5] | d[p + 6] << 8, d[p + 7] | d[p + 8] << 8
        p += 10
        mcs, p = d[p], p + 1
        sub = bytearray()
        while d[p]:
            sub += d[p + 1:p + 1 + d[p]]
            p += 1 + d[p]
        p += 1
        px = _unlzw(bytes(sub), mcs)
        if len(px) != iw * ih:
            raise ValueError(f"{path}: {len(px)} pixels, expected {iw * ih}")
        for i, v in enumerate(px):
            if not (hast and v == trans):
                canvas[i] = v
        out.append(bytes(canvas))
        hast = False
    return w, h, out


def _unlzw(data, mcs):
    clear, end = 1 << mcs, (1 << mcs) + 1
    size = mcs + 1
    # Two placeholders, not two more literals: codes 256 and 257 are the
    # clear and end markers and stand for no byte at all.
    fresh = [bytes((i,)) for i in range(clear)] + [b"", b""]
    table = list(fresh)
    out, acc, n, pos, prev = bytearray(), 0, 0, 0, None
    while True:
        while n < size:
            if pos >= len(data):
                return bytes(out)
            acc |= data[pos] << n
            n, pos = n + 8, pos + 1
        code, acc, n = acc & ((1 << size) - 1), acc >> size, n - size
        if code == clear:
            table = list(fresh)
            size, prev = mcs + 1, None
            continue
        if code == end:
            return bytes(out)
        entry = table[code] if code < len(table) else prev + prev[:1]
        out += entry
        if prev is not None:
            table.append(prev + entry[:1])
            # After adding, not before: the entry just added is the one the
            # encoder added a code ago.
            if len(table) == (1 << size) and size < 12:
                size += 1
        prev = entry


def lump(wad, name):
    """A named lump's bytes, or None.  Used to ask the WAD how wide a patch
    is rather than hardcoding it, so a check about layout stays a statement
    about Doom's data."""
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    for i in range(n):
        pos, size, nm = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        if nm.rstrip(b"\0") == name.encode():
            return d[pos:pos + size]
    return None


def map_lumps(wad, mapname):
    """The lumps belonging to one map, by name.

    A map's lumps are the ones FOLLOWING its marker, so they cannot be found
    by name alone -- every map in an IWAD has a lump called LINEDEFS.
    """
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    ents = []
    for i in range(n):
        p, s, nm = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        ents.append((nm.rstrip(b"\0").decode("latin1"), p, s))
    names = [e[0] for e in ents]
    if mapname not in names:
        return None
    m = names.index(mapname)
    out = {}
    for nm, p, s in ents[m + 1:m + 11]:
        if nm in ("THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SECTORS",
                  "SEGS", "SSECTORS", "NODES", "REJECT", "BLOCKMAP"):
            out.setdefault(nm, d[p:p + s])
        else:
            break
    return out


def masked_middles(wad, mapname, axis_only=True):
    """Two-sided linedefs that name a MIDDLE texture, with everything needed
    to stand in front of one and say where its top edge must land.

    check.py's own route through LINEDEFS and SIDEDEFS, so a disagreement
    with the engine is a real disagreement and not two readings of one bug.
    Only axis-aligned lines are returned: a wall square to the view has one
    depth for every column it covers, which is what lets the expected row be
    a single number rather than an interpolation.
    """
    L = map_lumps(wad, mapname)
    if not L or not all(k in L for k in
                        ("VERTEXES", "LINEDEFS", "SIDEDEFS", "SECTORS")):
        return []
    V = [struct.unpack("<hh", L["VERTEXES"][4 * i:4 * i + 4])
         for i in range(len(L["VERTEXES"]) // 4)]
    SD, SC = L["SIDEDEFS"], L["SECTORS"]
    sides = [(struct.unpack("<h", SD[30 * i + 2:30 * i + 4])[0],
              SD[30 * i + 20:30 * i + 28].rstrip(b"\0").decode("latin1")
              .upper(),
              struct.unpack("<h", SD[30 * i + 28:30 * i + 30])[0])
             for i in range(len(SD) // 30)]
    secs = [struct.unpack("<hh", SC[26 * i:26 * i + 4])
            for i in range(len(SC) // 26)]
    out, LD = [], L["LINEDEFS"]
    for i in range(len(LD) // 14):
        v1, v2, fl, _sp, _tag, r, l = struct.unpack("<7h", LD[14 * i:14 * i + 14])
        if r < 0 or l < 0 or v1 >= len(V) or v2 >= len(V):
            continue
        a, b = V[v1], V[v2]
        if axis_only and a[0] != b[0] and a[1] != b[1]:
            continue
        for s, other in ((r, l), (l, r)):
            if s >= len(sides) or other >= len(sides):
                continue
            rowoff, tex, sec = sides[s]
            if not tex or tex == "-" or sec >= len(secs):
                continue
            osec = sides[other][2]
            if osec >= len(secs):
                continue
            # the front side runs a->b; the back side sees the same segment
            # the other way round, which is what puts the camera on its side
            aa, bb = (a, b) if s == r else (b, a)
            out.append(dict(line=i, tex=tex, a=aa, b=bb,
                            front=secs[sec], back=secs[osec],
                            pegbottom=bool(fl & 16), rowoff=rowoff))
    return out


def sight_model(wad, mapname):
    """An independent P_CheckSight over a map's LINEDEFS.

    Deliberately a second implementation, and deliberately over LINEDEFS
    where the engine walks SEGS: the segs partition the linedefs exactly, so
    two routes that disagree have found a real bug in one of them.  Returns
    a function (ax, ay, bx, by) -> True when nothing blocks the line.

    A line stops sight when it is one-sided, or when the opening between its
    two sectors has closed.  That is the engine's rule, and it is the whole
    rule -- neither side models the height of the line of sight.
    """
    L = map_lumps(wad, mapname)
    if not L or "LINEDEFS" not in L:
        return None
    v = L["VERTEXES"]
    verts = [struct.unpack("<hh", v[4 * i:4 * i + 4]) for i in range(len(v) // 4)]
    sd = L["SIDEDEFS"]
    sides = [struct.unpack("<h", sd[30 * i + 28:30 * i + 30])[0]
             for i in range(len(sd) // 30)]
    sc = L["SECTORS"]
    secs = [struct.unpack("<hh", sc[26 * i:26 * i + 4])
            for i in range(len(sc) // 26)]          # (floor, ceiling)
    ld = L["LINEDEFS"]
    lines = []
    for i in range(len(ld) // 14):
        a, b, fl, sp, tag, r, l = struct.unpack("<hhhhhhh", ld[14 * i:14 * i + 14])
        lines.append((verts[a], verts[b], r, l))

    def blocks(r, l):
        if r < 0 or l < 0 or r >= len(sides) or l >= len(sides):
            return True
        fs, bs = sides[r], sides[l]
        if not (0 <= fs < len(secs)) or not (0 <= bs < len(secs)):
            return True
        top = min(secs[fs][1], secs[bs][1])
        bot = max(secs[fs][0], secs[bs][0])
        return top <= bot

    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    def see(ax, ay, bx, by):
        for (p, q, r, l) in lines:
            d1 = cross(ax, ay, bx, by, p[0], p[1])
            d2 = cross(ax, ay, bx, by, q[0], q[1])
            if d1 == 0 or d2 == 0 or (d1 < 0) == (d2 < 0):
                continue
            d3 = cross(p[0], p[1], q[0], q[1], ax, ay)
            d4 = cross(p[0], p[1], q[0], q[1], bx, by)
            if d3 == 0 or d4 == 0 or (d3 < 0) == (d4 < 0):
                continue
            if blocks(r, l):
                return False
        return True

    return see


def boxdiff(f, g, x0, x1, y0, y1):
    """(differing pixels, leftmost x, rightmost x) inside a screen-space box."""
    d = [x for x in range(x0, min(x1, len(f)))
         for y in range(y0, min(y1, len(f[0]))) if f[x][y] != g[x][y]]
    return (len(d), min(d) if d else -1, max(d) if d else -1)


def ramp(frame, row):
    """How many pixels of a frame sit on one palette ramp."""
    return sum(1 for c in frame for b in c if b // 16 == row)


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAIL.append(label)
    return ok


def edges(frame):
    """Rows where the surface changes -- the geometry, independent of shade.

    NB this keys on the *ramp* index, so it is not independent of which ramp a
    sector was assigned.  Reassigning sector ramps breaks this comparison, not
    just pixel identity.
    """
    return {(x, y) for x in range(len(frame))
            for y in range(1, len(frame[0]))
            if frame[x][y] // 16 != frame[x][y - 1] // 16}


def playpal(wad):
    """The first 768 bytes of a WAD's PLAYPAL lump, or None."""
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    for i in range(n):
        pos, _, name = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        if name.rstrip(b"\0") == b"PLAYPAL":
            return d[pos:pos + 768]
    return None


def ramp_rows(pal):
    """Palette rows that darken monotonically across the shades 0..13 that the
    renderer can emit.

    A real PLAYPAL is not sixteen ramps of sixteen: some rows hold two
    eight-entry ramps and some mix unrelated colours.  Basing a surface on one
    of those makes it jump in brightness halfway down.
    """
    lum = [0.3 * pal[3 * i] + 0.59 * pal[3 * i + 1] + 0.11 * pal[3 * i + 2]
           for i in range(256)]
    return {r for r in range(16)
            if all(lum[r * 16 + c] >= lum[r * 16 + c + 1] for c in range(13))}


def composite(wad, want, fill=True):
    """Composite a named wall texture from a WAD, independently of the engine.

    Deliberately a second implementation rather than a shared one: the point
    is that two routes through PNAMES, TEXTURE1 and the patch format agree.
    Returns (width, height, {(u, v): palette byte}), or None.

    `fill' is the wad.tex behaviour: a texel no post covers reads as palette
    0, because \\dt@zclear puts it there.  Pass False to see the holes, which
    is what tells a masked middle worth checking transparency against from
    one that happens to be a solid wall hung in a doorway.
    """
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    lumps = []
    for i in range(n):
        p, s, nm = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        lumps.append((nm.rstrip(b"\0").decode("latin1").upper(), p, s))
    first = {}
    for nm, p, s in lumps:
        first.setdefault(nm, (p, s))
    if "PNAMES" not in first:
        return None
    pp = first["PNAMES"][0]
    npat = struct.unpack("<i", d[pp:pp + 4])[0]
    pnames = [d[pp + 4 + 8 * i:pp + 12 + 8 * i].rstrip(b"\0")
              .decode("latin1").upper() for i in range(npat)]

    rec = None
    for lump in ("TEXTURE1", "TEXTURE2"):
        if lump not in first or rec:
            continue
        tp = first[lump][0]
        cnt = struct.unpack("<i", d[tp:tp + 4])[0]
        offs = struct.unpack("<%di" % cnt, d[tp + 4:tp + 4 + 4 * cnt])
        for o in offs:
            nm = d[tp + o:tp + o + 8].rstrip(b"\0").decode("latin1").upper()
            if nm == want.upper():
                rec = tp + o
                break
    if rec is None:
        return None
    w, h = struct.unpack("<hh", d[rec + 12:rec + 16])
    pc = struct.unpack("<h", d[rec + 20:rec + 22])[0]
    tex = {}
    for k in range(pc):
        q = rec + 22 + 10 * k
        ox, oy, pi = struct.unpack("<hhh", d[q:q + 6])
        if not 0 <= pi < npat or pnames[pi] not in first:
            continue
        pq = first[pnames[pi]][0]
        pw = struct.unpack("<h", d[pq:pq + 2])[0]
        cols = struct.unpack("<%di" % pw, d[pq + 8:pq + 8 + 4 * pw])
        for c in range(pw):
            x = ox + c
            if not 0 <= x < w:
                continue
            z = pq + cols[c]
            while d[z] != 255:
                td, ln = d[z], d[z + 1]
                for i in range(ln):
                    y = oy + td + i
                    if 0 <= y < h:
                        tex[(x, y)] = d[z + 3 + i]
                z += 4 + ln
    if fill:                                # holes read as 0, as in wad.tex
        for x in range(w):
            for y in range(h):
                tex.setdefault((x, y), 0)
    return w, h, tex


def patch_count(wad, want):
    """How many patches a named texture is built from, or 0 if there is none.

    The one fact about a texture composite() does not hand back, and the one
    M23's masked middles turn on: a single patch brings its own posts, and
    anything else has to have them built.
    """
    for lump_name in ("TEXTURE1", "TEXTURE2"):
        t = lump(wad, lump_name)
        if not t:
            continue
        cnt = struct.unpack("<i", t[:4])[0]
        for o in struct.unpack("<%di" % cnt, t[4:4 + 4 * cnt]):
            if t[o:o + 8].rstrip(b"\0").decode("latin1").upper() == want.upper():
                return struct.unpack("<h", t[o + 20:o + 22])[0]
    return 0


def expected_posts(tex, h, col):
    r"""One column of a texture as \dt@maskbuild should leave it.

    `tex' is composite(..., fill=False)'s dictionary, so a texel is present
    only if some patch drew it.  Each run of drawn rows is one post --
    {top}{length}{pixels} -- and the list ends {-1}{0}{}, which is
    \dt@readpic's own shape.
    """
    out, y = [], 0
    while y < h:
        if (col, y) not in tex:
            y += 1
            continue
        top = y
        px = []
        while y < h and (col, y) in tex:
            px.append(tex[(col, y)])
            y += 1
        out.append("{%d}{%d}{%s}" % (top, len(px), bytes(px).hex().upper()))
    return "".join(out) + "{-1}{0}{}"


def sprite_frames(wad):
    """{four-letter sprite name: set of frame letters} for every sprite lump.

    A lump is NAME, a frame letter and a rotation, and optionally a second
    frame and rotation when one picture serves two views mirrored -- so both
    letters count.
    """
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    names = [d[off + 16 * i + 8:off + 16 * i + 16].rstrip(b"\0")
             .decode("latin1").upper() for i in range(n)]
    if "S_START" not in names or "S_END" not in names:
        return {}
    out = {}
    for nm in names[names.index("S_START") + 1:names.index("S_END")]:
        if len(nm) >= 6:
            out.setdefault(nm[:4], set()).add(nm[4])
            if len(nm) >= 8:
                out[nm[:4]].add(nm[6])
    return out


def colormap(wad, table):
    """One 256-byte table of a WAD's COLORMAP, or None."""
    cm = lump(wad, "COLORMAP")
    if not cm or len(cm) < 256 * (table + 1):
        return None
    return cm[256 * table:256 * table + 256]


def m23_iwad(iwad, mapname, normal):
    """M23 against a real IWAD: what the built-in map has no artwork to show.

    `normal' is frame 0 of the plain walk -- the start of the map, at the
    default resolution, with nothing switched on -- which is the control the
    two view checks below are read against.
    """
    print("powers on the view, and the tables against the WAD (M23)")
    # ---- every frame the tables name is a picture in this WAD ---------------
    # A wrong letter in \dtMonAnim does not crash: the loader asks for a lump
    # that is not there, the rotation-0 fallback misses too, and the monster
    # turns into a coloured billboard for exactly the frames nobody watched.
    # So the tables are read out of doom.tex and held against the directory.
    # A sprite name with NO lump at all is a monster this game does not have
    # -- Doom has no mancubus -- and is skipped rather than failed.
    src = open("doom.tex", encoding="utf-8", errors="replace").read()
    have = sprite_frames(iwad)
    stype = dict(re.findall(r"\\dtSpriteType\{(\d+)\}\{(\w{4})\w\}", src))
    asked = []
    for t, _state, letters in re.findall(
            r"\\dtMonAnim\{(\d+)\}\{(\d)\}\{([A-Z]+)\}", src):
        if t in stype:
            asked += [(stype[t], c) for c in letters]
    for _t, _state, name, letters in re.findall(
            r"\\dtMisAnim\{(\d+)\}\{(\d)\}\{(\w{4})\}\{([A-Z]+)\}", src):
        asked += [(name, c) for c in letters]
    asked = sorted({a for a in asked if a[0] in have})
    missing = [n + c for n, c in asked if c not in have[n]]
    check("every frame the monster and missile tables name is in the WAD",
          len(asked) > 100 and not missing,
          f"{len(asked)} frames of {len({n for n, _ in asked})} sprites, "
          f"missing {missing[:6] if missing else 'none'}")

    # ---- the loader reads byte 22 of a sector, not some other byte -----------
    # The engine says how many secret sectors it found; check.py counts
    # special 9 in the SECTORS lump for itself.  A special read two bytes
    # late is the sector's TAG, and nothing on the built-in map could see
    # that -- its specials are all given by hand.
    L = map_lumps(iwad, mapname)
    sec = L["SECTORS"] if L and "SECTORS" in L else b""
    nine = sum(1 for i in range(len(sec) // 26)
               if struct.unpack("<h", sec[26 * i + 22:26 * i + 24])[0] == 9)
    try:
        said = re.search(r"\[doom\.tex\] (\d+) secret sectors",
                         flat(open("iwad.log", errors="replace").read()))
    except OSError:
        said = None
    check("the loader finds the secret sectors the map marks",
          nine > 0 and bool(said) and int(said.group(1)) == nine,
          f"{said.group(1) if said else 'none'} reported against {nine} "
          f"sectors carrying special 9 in {mapname}")

    # ---- invulnerability: the whole view through the inverse map ------------
    # COLORMAP's table 32 maps every colour to one of a few dozen greys, so
    # "was drawn through it" is a statement about each pixel on its own: it
    # is in that table's image or it is not.  The sky is exempt in Doom and
    # here, so \SkyRun is stubbed to a marker the way the sky checks do it,
    # and the status bar is not part of the view at all.
    inv = colormap(iwad, 32)
    if inv is None:
        check("this WAD has an inverse colormap", False, "COLORMAP is short")
        return
    S32 = set(inv)
    mark = next(v for v in range(255, 0, -1) if v not in S32)

    def power(job, hook):
        open(job + ".tex", "w").write(
            f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
            "\\def\\DTFRAMES{1}\n\\catcode`\\@=11\n"
            "\\def\\DTHOOK{\\def\\dtStepFwd##1{}" + hook +
            "\\dtPlayerTick\\message{^^JFIXCM \\the\\dtFIXCM\\space end}"
            "\\def\\SkyRun##1##2{\\Rep{\\numexpr(##2)-(##1)\\relax}"
            "{\\hx{%d}}}}\n\\input doom.tex\n" % mark)
        _, e, lg = build(job + ".tex")
        fx = re.search(r"FIXCM (-?\d+) end", flat(lg))
        fr = frames(job + ".pdf")
        return e, (int(fx.group(1)) if fx else None), (fr[0] if fr else None)

    def view(frame):
        """Every pixel above the status bar that is not sky."""
        hh = len(frame[0])
        bar = hh - hh * 32 // 200
        return [b for c in frame for b in c[:bar] if b != mark]

    e1, fx1, god = power("iwadgod", "\\dtINVUL=1000 ")
    e2, fx2, lamp = power("iwadlamp", "\\dtLAMP=1000 ")
    check("power fixtures compile clean", e1 + e2 == 0, f"{e1 + e2} errors")
    if god and lamp and normal:
        gv = view(god)
        out = sum(1 for b in gv if b not in S32)
        nv = [b for c in normal for b in c[:len(c) - len(c) * 32 // 200]]
        nout = sum(1 for b in nv if b not in S32)
        check("invulnerability draws the whole view through the inverse map",
              fx1 == 32 and len(gv) > 1000 and out == 0,
              f"table {fx1}; {out} of {len(gv)} view pixels are outside its "
              f"{len(S32)} greys")
        # ...which is only a statement if the ordinary view is NOT already
        # made of those greys.
        check("and the same view without it is mostly not those colours",
              nout * 4 > len(nv),
              f"{nout} of {len(nv)} pixels of the plain start view are "
              f"outside the table")
        # The visor is table 1 for everything, which no pixel can show by
        # itself -- table 1's image is nearly the whole palette.  What it
        # can show is that the dark end of the view came up: light and
        # distance stop mattering, so the picture is brighter and nothing in
        # it is darker than the plain one's brightest shade of the same...
        pal = playpal(iwad)
        lum = [0.3 * pal[3 * i] + 0.59 * pal[3 * i + 1] + 0.11 * pal[3 * i + 2]
               for i in range(256)]
        lv = view(lamp)
        ml = sum(lum[b] for b in lv) / max(1, len(lv))
        mn = sum(lum[b] for b in nv) / max(1, len(nv))
        check("light amplification draws it through table 1, and it is "
              "brighter", fx2 == 1 and ml > mn * 1.1 and lamp != god,
              f"table {fx2}; mean luma {ml:.1f} against {mn:.1f} for the "
              f"plain view")
    else:
        check("power fixtures rendered a frame", False, "no frame decoded")

    d = open(iwad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    maps = [nm for nm in (d[off + 16 * i + 8:off + 16 * i + 16].rstrip(b"\0")
                          .decode("latin1") for i in range(n))
            if re.fullmatch(r"MAP\d\d|E\dM\d", nm)]

    # ---- a teleporter on a real map -----------------------------------------
    # Everything the built-in fixtures say about teleporting they say about
    # things dropped in by hand, which have no recorded sector and are
    # located on the spot.  A thing wad.tex places carries the sector the
    # loader found for it, and that is the path every real teleporter takes
    # -- Doom II has four hundred of them.  So: the first map with a
    # teleporter line, that line fired from \DTHOOK, and the player has to
    # arrive ON a teleport destination, IN a sector carrying the line's tag.
    # Both of those are read out of the lumps here, not asked of the engine.
    tele = None
    for mp in maps:
        ML = map_lumps(iwad, mp)
        if not ML or "LINEDEFS" not in ML:
            continue
        LD = ML["LINEDEFS"]
        for i in range(len(LD) // 14):
            sp, tag = struct.unpack("<hh", LD[14 * i + 6:14 * i + 10])
            if sp in (39, 97) and tag:
                tele = (mp, i, tag, ML)
                break
        if tele:
            break
    if tele:
        mp, line, tag, ML = tele
        TH, SC = ML["THINGS"], ML["SECTORS"]
        dests = {struct.unpack("<hh", TH[10 * i:10 * i + 4])
                 for i in range(len(TH) // 10)
                 if struct.unpack("<h", TH[10 * i + 6:10 * i + 8])[0] == 14}
        open("iwadtele.tex", "w").write(
            f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mp}}}\n"
            "\\def\\DTFRAMES{0}\\def\\DTNOMON{1}\n"
            "\\def\\DTHOOK{\\message{^^JTELEA "
            "\\number\\numexpr\\PX/65536\\relax\\space"
            "\\number\\numexpr\\PY/65536\\relax\\space end}"
            f"\\dtFireLine{{{line}}}\\dtLocatePlayer"
            "\\message{^^JTELEB \\number\\numexpr\\PX/65536\\relax\\space"
            "\\number\\numexpr\\PY/65536\\relax\\space\\the\\dtSECN\\space"
            "\\the\\dtTELEPORTS\\space end}}\n\\input doom.tex\n")
        _, et, tlog = build("iwadtele.tex")
        a = re.search(r"TELEA (-?\d+) (-?\d+) end", flat(tlog))
        b = re.search(r"TELEB (-?\d+) (-?\d+) (\d+) (\d+) end", flat(tlog))
        ok = False
        detail = "no report from the fixture"
        if a and b:
            x, y, sec, count = (int(v) for v in b.groups())
            stag = (struct.unpack("<h", SC[26 * sec + 24:26 * sec + 26])[0]
                    if 26 * sec + 26 <= len(SC) else None)
            ok = (count == 1 and (x, y) in dests and stag == tag
                  and (x, y) != (int(a.group(1)), int(a.group(2))))
            detail = (f"{mp} line {line}, tag {tag}: from "
                      f"({a.group(1)}, {a.group(2)}) to ({x}, {y}) in sector "
                      f"{sec}, whose tag is {stag}; "
                      f"{'a' if (x, y) in dests else 'NOT a'} destination "
                      f"thing stands there")
        check("a teleporter on a real map lands on its tagged destination",
              et == 0 and ok, detail)
    else:
        print("       (no map in this IWAD has a teleporter -- skipped)")

    # ---- a masked middle built from more than one patch ---------------------
    # No map in an IWAD has to have one, so this looks for the first that
    # does -- Doom II's MAP09 hangs PIPE4, two patches side by side, in a
    # doorway -- and asks the engine for a few of its columns, including the
    # two either side of where one patch stops and the next begins.
    pick = None
    for mp in maps:
        for m in masked_middles(iwad, mp, axis_only=False):
            if patch_count(iwad, m["tex"]) > 1:
                pick = (mp, m["tex"])
                break
        if pick:
            break
    if not pick:
        print("       (no map in this IWAD names a multi-patch masked "
              "middle -- skipped)")
        return
    mp, tex = pick
    w, h, texels = composite(iwad, tex, fill=False)
    cols = sorted({0, w // 2 - 1, w // 2, w - 1})
    open("iwadmulti.tex", "w").write(
        f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mp}}}\n"
        "\\def\\DTFRAMES{0}\\def\\DTNOMON{1}\n"
        "\\def\\DTHOOK{" + "".join(f"\\dtMaskCol{{{tex}}}{{{c}}}"
                                   for c in cols) + "}\n\\input doom.tex\n")
    _, e3, mlog = build("iwadmulti.tex")
    fl = flat(mlog)
    bad = []
    for c in cols:
        got = re.search(r"\[maskcol\] %s %d ((?:\{[^{}]*\})+)" % (tex, c), fl)
        if not got or got.group(1) != expected_posts(texels, h, c):
            bad.append(c)
    check("a masked middle of several patches gets its posts built",
          e3 == 0 and not bad,
          f"{tex} on {mp}, {patch_count(iwad, tex)} patches, {w}x{h}: "
          f"columns {cols} against an independent composite, "
          f"{len(bad)} wrong")


def read_flat(wad, want):
    """Read a named 64x64 flat out of a WAD, independently of the engine.

    A flat has no patches, no posts and no PNAMES indirection -- it is 4096
    raw bytes between F_START and F_END -- so this is short, but it is still a
    second implementation and that is the point.  Returns bytes or None.
    """
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    ents = []
    for i in range(n):
        p, s, nm = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        ents.append((nm.rstrip(b"\0").decode("latin1").upper(), p, s))
    names = [e[0] for e in ents]
    if "F_START" not in names or "F_END" not in names:
        return None
    lo, hi = names.index("F_START"), names.index("F_END")
    for nm, p, s in ents[lo + 1:hi]:
        if nm == want.upper() and s >= 4096:
            return d[p:p + 4096]
    return None


def asymmetric(f):
    """Does this flat differ from its own transpose?

    A flat is indexed [world y][world x].  Swapping the two renders every
    floor in the map mirrored about its diagonal, which on a flat that
    happens to be symmetric -- and plenty of Doom's tiles nearly are -- is
    not visible at all.  So the probe has to be chosen, not assumed.
    """
    return any(f[64 * y + x] != f[64 * x + y]
               for y in range(64) for x in range(64))


def geometry_checks(label, pdf):
    """Column-length and void-pixel invariants for a rendered PDF."""
    fr = frames(pdf)
    if not fr:
        check(f"{label}: frames decoded", False, "none")
        return fr
    w, h = len(fr[0]), len(fr[0][0])
    bad = [(i, x) for i, f in enumerate(fr)
           for x, c in enumerate(f) if len(c) != h]
    check(f"{label}: every column exactly H bytes", not bad,
          f"{w}x{h}, {len(bad)} bad" if bad else f"{w}x{h}")
    void = sum(1 for f in fr for c in f for b in c if b // 16 * 16 == VOID_RAMP)
    check(f"{label}: no void pixels", void == 0, f"{void} void")
    return fr


FIXTURES = (
    ("demo.tex", "\\input doom.tex\n"),
    ("runwad.tex", "\\def\\DTWAD{test.wad}\n"
                   "\\def\\DTMAP{E1M1}\n\\input doom.tex\n"),
    ("interactive.tex", "\\def\\DTPLAY{1}\n"
                        "\\def\\DTW{76}\\def\\DTH{60}"
                        "\\def\\DTPIX{6}\n\\input doom.tex\n"),
    # M22: the same 152x120 render and the same walk, shown to the terminal
    # two different ways.  76 is deliberate -- a viewport row wider than
    # max_print_line is hard-wrapped and asciirows() would never see it.
    ("ascbox.tex", "\\def\\DTPLAY{1}\\def\\DTASC{2}\n"
                   "\\def\\DTW{152}\\def\\DTH{120}"
                   "\\def\\DTPIX{2}\n\\input doom.tex\n"),
    ("ascone.tex", "\\def\\DTPLAY{1}\\def\\DTASC{1}\n"
                   "\\def\\DTW{152}\\def\\DTH{120}"
                   "\\def\\DTPIX{2}\n\\input doom.tex\n"),
    ("ascpipe.tex", "\\def\\DTPLAY{1}\\def\\DTPIPE{1}\n"
                    "\\def\\DTW{152}\\def\\DTH{120}"
                    "\\def\\DTPIX{2}\n\\input doom.tex\n"),
    # M3: 40 frames east from (100,256) would reach x=1700 unobstructed; the
    # built-in map's east wall is at x=1024.
    ("walk.tex", "\\def\\DTFRAMES{40}\\def\\DTW{40}\\def\\DTH{24}\n"
                 "\\input doom.tex\n"),
    # M3: 45 degrees into the north wall, which must slide rather than stop.
    ("slide.tex", "\\def\\DTFRAMES{12}\\def\\DTW{40}\\def\\DTH{24}\n"
                  "\\def\\DTANG{1024}\n\\input doom.tex\n"),
    # M3: straight west into the wall at x=0.  Testing only where a move lands
    # lets a 40-unit step cross a wall with both ends outside the 16-unit
    # radius, so this walked clean through into the void.
    ("tunnel.tex", "\\def\\DTFRAMES{8}\\def\\DTW{24}\\def\\DTH{16}\n"
                   "\\def\\DTANG{4096}\n\\input doom.tex\n"),
    # M3: walk and turn continuously, which drags the player along every wall
    # in the map at every angle.
    ("stress.tex", "\\def\\DTFRAMES{120}\\def\\DTW{24}\\def\\DTH{16}\n"
                   "\\def\\DTTURN{307}\n\\input doom.tex\n"),
    # M4: a sprite behind a portal, with that portal open and then shut.
    # \DTHOOK drops a health pickup (ramp 80, a row the built-in map never
    # uses) into room 1, and the shut variant collapses room 1's ceiling onto
    # its floor, which is what a closed door looks like to the renderer.
    ("dooropen.tex", "\\def\\DTHOOK{\\dtAddThing{470}{256}{2011}{24}}\n"
                     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
                     "\\input doom.tex\n"),
    ("doorshut.tex", "\\def\\DTHOOK{\\dtAddThing{470}{256}{2011}{24}%\n"
                     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}}\n"
                     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
                     "\\input doom.tex\n"),
    # M5: a door installed by hand on the built-in map, with a pickup behind
    # it.  The ceiling rises from the floor to 92, so the pickup must be
    # invisible on the first frame and visible later -- and the player must
    # not get through until the opening clears 56 units of headroom.
    ("dooranim.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\expandafter\\xdef\\csname dtDT1\\endcsname{1}%\n"
     "\\expandafter\\xdef\\csname dtDS1\\endcsname{1}%\n"
     "\\expandafter\\xdef\\csname dtDW1\\endcsname{0}%\n"
     "\\expandafter\\xdef\\csname dtDC1\\endcsname{24}%\n"
     "\\expandafter\\xdef\\csname dtDO1\\endcsname{92}%\n"
     "\\expandafter\\xdef\\csname dtDX0\\endcsname{1}%\n"
     "\\global\\dtNDOOR=1\n"
     "\\dtAddThing{470}{256}{2011}{24}}\n"
     "\\def\\DTFRAMES{12}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # M5: the same door, left shut.  The player must not get through it.
    ("doorstuck.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\global\\dtNDOOR=0}\n"
     "\\def\\DTFRAMES{14}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # M7: sprite silhouettes.  A portal narrows a column without closing it,
    # so \CD -- one depth per column -- has nothing to say about a door part
    # way open or a step.  Both fixtures stand still and put a tall billboard
    # on ramp 208, a row the built-in map never uses, so every pixel of it is
    # attributable.
    #
    # The door rises 8 units a frame from its own floor, so the sprite behind
    # it must be revealed gradually.  Drawing it whole the instant the door
    # cracks open is the bug this pins.
    ("sildoor.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\expandafter\\xdef\\csname dtDT1\\endcsname{1}%\n"
     "\\expandafter\\xdef\\csname dtDS1\\endcsname{1}%\n"
     "\\expandafter\\xdef\\csname dtDW1\\endcsname{0}%\n"
     "\\expandafter\\xdef\\csname dtDC1\\endcsname{24}%\n"
     "\\expandafter\\xdef\\csname dtDO1\\endcsname{92}%\n"
     "\\expandafter\\xdef\\csname dtDX0\\endcsname{1}%\n"
     "\\global\\dtNDOOR=1\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\def\\dtStepFwd##1{}%\n"
     "\\dtAddThing{470}{256}{3004}{24}}\n"
     "\\def\\DTFRAMES{10}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # ...and a sprite in the far room behind a platform raised above the eye.
    # Nothing closes a column here at all, so the whole artifact lives in the
    # lower span.
    ("silstep.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSf1\\endcsname{80}%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{200}%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\def\\dtStepFwd##1{}%\n"
     "\\dtAddThing{700}{256}{3004}{-16}}\n"
     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # The control for both: the same scenes with the silhouette lookup
    # neutralised, which is exactly the old single-depth behaviour.  Without
    # it "0 pixels" would also be what an empty fixture reports.
    #
    # \DTHOOK is tokenised when this file is read, before doom.tex makes @ a
    # letter, so a stub naming \dt@thingclip has to set the catcode here.
    ("silstepno.tex",
     "\\catcode`\\@=11\n"
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSf1\\endcsname{80}%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{200}%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\def\\dtStepFwd##1{}%\n"
     "\\def\\dt@thingclip{\\dtSPCT=0 \\dtSPCB=\\SCRH }%\n"
     "\\dtAddThing{700}{256}{3004}{-16}}\n"
     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # M8: the arctangent and the rotation it picks.  No WAD needed -- this is
    # pure arithmetic, and getting it wrong turns every monster to face the
    # same way, which looks plausible enough to survive a glance at a render.
    ("rotmath.tex",
     "\\catcode`\\@=11\n"
     "\\def\\DTFRAMES{0}\\def\\DTW{16}\\def\\DTH{16}\n"
     "\\def\\DTHOOK{%\n"
     "\\def\\A##1##2{\\dtAtan{##1}{##2}\\message{ATAN ##1 ##2 \\the\\dtANG}}%\n"
     "\\A{100}{0}\\A{100}{100}\\A{0}{100}\\A{-100}{100}%\n"
     "\\A{-100}{0}\\A{-100}{-100}\\A{0}{-100}\\A{100}{-100}\\A{1000}{414}%\n"
     "\\def\\R##1##2##3{\\dtAtan{0-(##1)}{0-(##2)}%\n"
     "\\dtnorm\\dtAQ{\\dtANG-(##3)+4608}\\divide\\dtAQ by1024 "
     "\\advance\\dtAQ by1\n"
     "\\message{ROT ##1 ##2 ##3 \\the\\dtAQ}}%\n"
     "\\R{100}{0}{0}\\R{100}{100}{0}\\R{0}{100}{0}\\R{-100}{100}{0}%\n"
     "\\R{-100}{0}{0}\\R{-100}{-100}{0}\\R{0}{-100}{0}\\R{100}{-100}{0}%\n"
     "\\R{100}{0}{2048}\\R{0}{100}{2048}}\n"
     "\\input doom.tex\n"),
    # M4: \\dtSplice in isolation, via \\DTHOOK.  Seven pixels, three replaced
    # starting at index two: 00 11 | AA BB CC | (22 33 44 dropped) | 55 66.
    ("splice.tex", "\\def\\DTHOOK{\\immediate\\write16{SPLICE:"
                   "\\dtSplice{00112233445566}{2}{3}{AABBCC}}}\n"
                   "\\def\\DTFRAMES{0}\\def\\DTW{8}\\def\\DTH{8}\n"
                   "\\input doom.tex\n"),
    # M6: the use key.  Nothing above this line ever calls \dtUse -- the door
    # fixtures all write dtDS/dtSc straight from \DTHOOK -- which is how a
    # calling-convention bug in \dt@usenear survived every check in this file.
    #
    # Linedef 3 of the built-in map is the portal between rooms 0 and 1, so
    # giving it special 1 and dropping room 1's ceiling onto its floor builds
    # a real shut door out of geometry that is already tested.  \dtFindDoors
    # runs before \DTHOOK, so the fixture calls it again; \dt@markdoor guards
    # on dtDT, so a second pass is harmless.  Order matters: dtSc1 must be
    # shut *before* the mark, because that is what records the shut height.
    ("usewalk.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\dtAddLineSpec{3}{1}\\dtFindDoors}\n"
     "\\def\\DTUSE{1}\n"
     "\\def\\DTFRAMES{30}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\input doom.tex\n"),
    # ...and the same walk with no use key, which must never get through.
    ("usewalkno.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\dtAddLineSpec{3}{1}\\dtFindDoors}\n"
     "\\def\\DTFRAMES{30}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\input doom.tex\n"),
    # M6: the same door pressed from the key loop, which is the path that
    # actually failed in \DTPLAY mode.
    ("useplay.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\dtAddLineSpec{3}{1}\\dtFindDoors}\n"
     "\\def\\DTPLAY{1}\\def\\DTW{24}\\def\\DTH{16}\\def\\DTPIX{2}\n"
     "\\input doom.tex\n"),
    # M6: door surfaces.  \DOORRAMP and \TRAKRAMP are moved onto rows the
    # built-in map never uses -- 208 and 16 -- so any pixel on them can only
    # have come from the door selection in \dtSegDraw.  Room 1's own wall
    # ramp is already 128, the default \DOORRAMP, so leaving it there would
    # have made the check pass for the wrong reason.
    ("doorface.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\dtAddLineSpec{3}{1}\\dtFindDoors \\DOORRAMP=208 }\n"
     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\input doom.tex\n"),
    ("doorplain.tex",
     "\\def\\DTHOOK{\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
     "\\DOORRAMP=208 }\n"
     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\input doom.tex\n"),
    # The track is the door sector's own one-sided walls, so it needs the
    # doorway open to be visible at all.
    ("doortrack.tex",
     "\\def\\DTHOOK{\\dtAddLineSpec{3}{1}\\dtFindDoors \\TRAKRAMP=16 }\n"
     "\\def\\DTFRAMES{1}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\input doom.tex\n"),
    # M10: frame animation.  The picker and the modulus are unit-tested here
    # rather than inferred from a render, because a wrong letter still draws a
    # picture and a modulus that rounds is off by one tic for one frame in six.
    ("animmath.tex",
     "\\def\\DTFRAMES{1}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\def\\DTHOOK{%\n"
     "\\message{^^JANIMAT4 \\dtAnimAt{0}{ABCD} \\dtAnimAt{1}{ABCD}"
     " \\dtAnimAt{2}{ABCD} \\dtAnimAt{3}{ABCD}}%\n"
     "\\message{^^JANIMAT6 \\dtAnimAt{0}{ABCDCB} \\dtAnimAt{3}{ABCDCB}"
     " \\dtAnimAt{4}{ABCDCB} \\dtAnimAt{5}{ABCDCB}}%\n"
     # 2035 is the barrel: two frames, six tics.  2014 is the health bonus:
     # six frames, six tics, running up the letters and back down.
     "\\dtTI=0 \\dtANIM=0  \\dtAnimIndex{2035}\\message{^^JIDXA \\the\\dtMFA}%\n"
     "\\dtTI=0 \\dtANIM=6  \\dtAnimIndex{2035}\\message{^^JIDXB \\the\\dtMFA}%\n"
     "\\dtTI=0 \\dtANIM=12 \\dtAnimIndex{2035}\\message{^^JIDXC \\the\\dtMFA}%\n"
     "\\dtTI=1 \\dtANIM=0  \\dtAnimIndex{2035}\\message{^^JIDXD \\the\\dtMFA}%\n"
     "\\dtTI=1 \\dtANIM=6  \\dtAnimIndex{2035}\\message{^^JIDXE \\the\\dtMFA}%\n"
     "\\dtTI=0 \\dtANIM=5  \\dtAnimIndex{2035}\\message{^^JIDXF \\the\\dtMFA}%\n"
     "\\dtTI=0 \\dtANIM=30 \\dtAnimIndex{2014}\\message{^^JIDXG \\the\\dtMFA}%\n"
     "\\dtTI=0 \\dtANIM=36 \\dtAnimIndex{2014}\\message{^^JIDXH \\the\\dtMFA}}\n"
     "\\input doom.tex\n"),
    # M13: a walkover trigger, and the reason tags exist at all.  Linedef 3 is
    # the portal between rooms 0 and 1 -- the same line the use-key fixtures
    # build a door out of -- but here it carries a W1 open-and-stay special
    # tagged 9, and room 2 is the sector tagged 9, shut onto its own floor.
    # So the line that is crossed is NOT the door that opens: the trigger is
    # in one room and the sector it moves is two rooms away, which is the
    # whole difference between a tag and a back sector.
    #
    # The walk then either reaches room 2 or is stopped at its portal, and
    # which of those happened is already a number in the frame log.
    ("walkover.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc2\\endcsname{-16}%\n"
     "\\dtAddLineSpec{3}{2}\\dtAddLineTag{3}{9}\\dtAddSectorTag{2}{9}%\n"
     "\\dtFindDoors}\n"
     "\\def\\DTFRAMES{30}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\input doom.tex\n"),
    # The control keeps the shut room AND the tags and drops only the special,
    # so the geometry either side is identical and a walk that gets through
    # can only have got through by crossing something that fired.
    ("walkoverno.tex",
     "\\def\\DTHOOK{%\n"
     "\\expandafter\\xdef\\csname dtSc2\\endcsname{-16}%\n"
     "\\dtAddLineTag{3}{9}\\dtAddSectorTag{2}{9}%\n"
     "\\dtFindDoors}\n"
     "\\def\\DTFRAMES{30}\\def\\DTW{24}\\def\\DTH{16}\n"
     "\\input doom.tex\n"),
    # M14: the give rules, asked directly.  Every one of these is a rule out
    # of P_TouchSpecialThing that a render cannot show: a medikit refused at
    # full health looks exactly like a medikit nobody walked into.
    ("giveunit.tex",
     "\\catcode`\\@=11\n"
     "\\def\\DTFRAMES{0}\\def\\DTW{16}\\def\\DTH{16}\n"
     "\\def\\R#1{\\message{^^JGIVE #1 hp=\\the\\dtHEALTH\\space"
     " ar=\\the\\dtARMOR/\\the\\dtARMCL\\space b=\\csname dtUA0\\endcsname"
     "\\space s=\\csname dtUA1\\endcsname\\space wp=\\the\\dtWEAP\\space"
     " max=\\csname dtUM0\\endcsname\\space got=\\the\\dtGOT}}\n"
     "\\def\\DTHOOK{%\n"
     "\\dtGOT=0 \\dtGiveBody{25}\\R{MEDIFULL}%\n"
     "\\dtHEALTH=50 \\dtGOT=0 \\dtGiveBody{25}\\R{MEDIHURT}%\n"
     "\\dtGOT=0 \\dtGiveAmmo{0}{1}\\R{CLIP}%\n"
     "\\dtGOT=0 \\dtGiveWeapon{2}\\R{SHOTGUN}%\n"
     "\\dtGOT=0 \\dtGiveWeapon{2}\\R{SHOTGUNTWO}%\n"
     "\\dtWEAP=1 \\dtGOT=0 \\dtGiveWeapon{2}\\R{SHOTGUNTHREE}%\n"
     "\\dtGOT=0 \\dtGiveArmor{1}\\R{GREEN}%\n"
     "\\dtGOT=0 \\dtGiveArmor{1}\\R{GREENTWO}%\n"
     "\\dtGOT=0 \\dtGiveArmor{2}\\R{BLUE}%\n"
     "\\dtGOT=0 \\dt@gpack\\R{PACK}%\n"
     "\\dtGOT=0 \\dt@gpack\\R{PACKTWO}}\n"
     "\\input doom.tex\n"),
    # M14: which face, as a name.  The thresholds are arithmetic and a wrong
    # one still draws a perfectly good face, so this asks the picker rather
    # than looking at pixels.
    ("faceunit.tex",
     "\\catcode`\\@=11\n"
     "\\def\\DTFRAMES{0}\\def\\DTW{16}\\def\\DTH{16}\n"
     "\\def\\F#1#2{\\dtHEALTH=#1 \\dtANIM=#2 \\dt@facepick\n"
     "\\message{^^JFACE #1 #2 \\dtFaceKey}}\n"
     "\\def\\DTHOOK{\\F{100}{0}\\F{99}{0}\\F{80}{0}\\F{79}{0}\\F{50}{0}%\n"
     "\\F{20}{0}\\F{1}{0}\\F{0}{0}\\F{107}{0}%\n"
     "\\F{100}{17}\\F{100}{34}\\F{100}{51}}\n"
     "\\input doom.tex\n"),
    # M14: a pickup on the scripted walk.  The billboard is forced onto ramp
    # 208 -- a row the built-in level never uses -- so "the stimpack is gone"
    # is a pixel count rather than a claim about a log line.  The render
    # happens before the step, so the thing is drawn in frames 0..4 and the
    # walk collects it on the fifth step.
    ("pickwalk.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\dtAddThing{300}{256}{2011}{0}\\dtHEALTH=50 }\n"
     "\\def\\DTFRAMES{8}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # The control: the same walk with the touch scan stubbed.  Without it
    # "health never changed" would also be what an empty room reports, and
    # the stimpack staying visible would look like a working refusal.
    ("pickwalkno.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\def\\dtTouchThings{}%\n"
     "\\dtAddThing{300}{256}{2011}{0}\\dtHEALTH=50 }\n"
     "\\def\\DTFRAMES{8}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{100}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # M14: "is it still there" needs the player to STAND somewhere it would
    # be visible from, and a forward walk cannot provide that -- once the
    # walk is close enough to collect a thing, the next step puts it behind
    # the camera, so the control reports zero pixels of it too and the check
    # passes whatever the engine does.  The first version of this did exactly
    # that.  So these stand: one 1-unit step in the hook drives the touch
    # scan through the ordinary movement path, and then the walk is frozen
    # and every frame looks at the spot where the thing was.
    #
    # 280 to a thing at 310 is 30 units, inside the 36-unit box.
    ("pickstand.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\dtAddThing{310}{256}{2011}{0}\\dtHEALTH=50 %\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    ("pickstandno.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\def\\dtTouchThings{}%\n"
     "\\dtAddThing{310}{256}{2011}{0}\\dtHEALTH=50 %\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # Doom refuses a medikit at full health and leaves it standing.  The pair
    # is what makes it a rule rather than dead code: the same medikit on the
    # same floor at 50 health must go.
    ("pickfull.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\dtAddThing{310}{256}{2012}{0}%\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    ("pickhurt.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\dtAddThing{310}{256}{2012}{0}\\dtHEALTH=50 %\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # ...and a health bonus, which is the other side of the same table: it is
    # taken at full health and carries the player past 100.
    ("pickbonus.tex",
     "\\def\\DTHOOK{%\n"
     "\\def\\dtThingClass##1{\\dtTKD=1 \\dtTKR=208 \\dtTKH=56 \\dtTKW=20 }%\n"
     "\\dtAddThing{310}{256}{2014}{0}%\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{120}\\def\\DTH{80}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
    # The same stimpack 60 units above the floor the player is standing on:
    # in range across the floor and out of reach in height.  This one is
    # state only, deliberately -- a thing that high is off the top of the
    # view at the distance the box test needs, so there is nothing to see and
    # the pair with pickstand is the whole of the evidence.
    ("pickledge.tex",
     "\\def\\DTHOOK{%\n"
     "\\dtAddThing{310}{256}{2011}{60}\\dtHEALTH=50 %\n"
     "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}}\n"
     "\\def\\DTFRAMES{3}\\def\\DTW{40}\\def\\DTH{24}\n"
     "\\def\\DTANG{0}\\def\\DTX{280}\\def\\DTY{256}\n"
     "\\input doom.tex\n"),
)

def flat(log):
    """A log with pdfTeX's max_print_line wrap undone -- see track()."""
    return log.replace("\r", "").replace("\n", "")


def runs(seq):
    """[(value, length), ...] for each run of equal values, in order."""
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return [tuple(r) for r in out]


def dumpfix(name, hook, fields, nf=24, pre="", w=24, h=16, stand=False,
            stdin=None):
    r"""Build a built-in-map fixture that reports `fields' once a frame.

    `fields' is a list of (label, TeX) pairs; each frame prints one line
    `DUMP label=value ...' from a wrapper round \dtPlayerTick, which is the
    hook every section since M16 has used.  Returns (errors, log, rows) with
    a row per frame as a dict of ints.

    One helper rather than one per section, because M23 is nine small
    features and each wants two or three numbers nobody else does.
    """
    dump = " ".join(f"{k}={v}\\space" for k, v in fields)
    open(name + ".tex", "w").write(
        "\\catcode`\\@=11\n" + pre
        + f"\\def\\DTFRAMES{{{nf}}}\\def\\DTW{{{w}}}\\def\\DTH{{{h}}}\n"
        "\\def\\DTHOOK{" + hook + "\n"
        + ("\\def\\dtStepFwd##1{}%\n" if stand else "")
        + "\\let\\dt@ptwas\\dtPlayerTick\n"
        "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JDUMP " + dump + "}}%\n"
        "}\n\\input doom.tex\n")
    _, e, lg = build(name + ".tex", stdin=stdin, interactive=stdin is not None)
    pat = "DUMP " + " ".join(f"{k}=(-?\\d+)" for k, _ in fields)
    # re.findall hands back bare strings, not 1-tuples, when the pattern has
    # a single group -- and zipping a field name against the CHARACTERS of
    # "4960" reads it as 4.  Six checks failed on that before one of them
    # was looked at closely enough to see every loss was a leading digit.
    found = re.findall(pat, flat(lg))
    if len(fields) == 1:
        found = [(m,) for m in found]
    rows = [dict(zip((k for k, _ in fields), (int(v) for v in m)))
            for m in found]
    return e, lg, rows


def cs(name):
    r"""\csname name\endcsname, for a dump field."""
    return "\\csname " + name + "\\endcsname"


def m23_checks():
    """M23, on the built-in map: the last of the roadmap.

    Three rooms whose floors, ceilings and lights this file names -- 0/24/-16,
    128/96/144 and 224/176/128 -- so every number below is either one of
    those or arithmetic on them, never a reading taken off a run and written
    back as its own expectation.
    """
    print("sector specials: lights, floors and secrets (M23)")
    L1 = [("l0", cs("dtSl0")), ("l1", cs("dtSl1")), ("l2", cs("dtSl2")),
          ("hp", "\\the\\dtHEALTH"), ("sc", "\\the\\dtSECRETS"),
          ("st", "\\the\\dtNSECRET"), ("burn", "\\the\\dtBURNS")]

    def spec(sector, n):
        return f"\\dtAddSectorSpec{{{sector}}}{{{n}}}"

    # A fast strobe on room 1.  Its neighbours are at 224 and 128, so the
    # dimmest of them is 128 and the light has to alternate between its own
    # 176 and that -- two frames bright and six dark, which is Doom's 5 and
    # 15 tics at two frames to five.
    e1, _, F = dumpfix("litefast", spec(1, 2) + "\\dtSpawnSpecials", L1,
                       nf=48, stand=True)
    e2, _, S = dumpfix("liteslow", spec(1, 3) + "\\dtSpawnSpecials", L1,
                       nf=48, stand=True)
    e3, _, N = dumpfix("liteno", spec(1, 2) + "\\dtSpawnSpecials", L1,
                       nf=48, stand=True, pre="\\def\\DTNOSPEC{1}\n")
    check("light fixtures compile clean", e1 + e2 + e3 == 0,
          f"{e1 + e2 + e3} errors")
    fr, sr = runs(r["l1"] for r in F), runs(r["l1"] for r in S)
    # the first and last runs are cut short by the fixture, so they are out
    fb = {n for v, n in fr[1:-1] if v == 176}
    fd = {n for v, n in fr[1:-1] if v == 128}
    sd = {n for v, n in sr[1:-1] if v == 128}
    check("a strobe alternates between its own light and the dimmest "
          "neighbour's", {v for v, _ in fr} == {176, 128},
          f"room 1 showed {sorted({v for v, _ in fr})}; its neighbours are "
          f"224 and 128")
    check("bright for two frames and dark for six", fb == {2} and fd == {6},
          f"bright runs {sorted(fb)}, dark runs {sorted(fd)}")
    check("and the slow kind is dark for fourteen", sd == {14},
          f"dark runs {sorted(sd)} against the fast kind's {sorted(fd)}")
    check("with \\DTNOSPEC the light never moves",
          bool(N) and {r["l1"] for r in N} == {176},
          f"control showed {sorted({r['l1'] for r in N})}")
    # Specials 12 and 13 are the same strobes STARTED TOGETHER.  Two rooms
    # that do not touch, so neither is the other's dimmest neighbour.
    e4, _, Y = dumpfix("litesync", spec(0, 13) + spec(2, 13)
                       + "\\dtSpawnSpecials", L1, nf=40, stand=True)
    e5, _, U = dumpfix("liteunsync", spec(0, 2) + spec(2, 2)
                       + "\\dtSpawnSpecials", L1, nf=40, stand=True)

    def flips(rows, k):
        return [i for i, (a, b) in enumerate(zip(rows, rows[1:]))
                if a[k] != b[k]]

    check("synchronised strobes change on the same frames",
          e4 + e5 == 0 and bool(flips(Y, "l0"))
          and flips(Y, "l0") == flips(Y, "l2"),
          f"{len(flips(Y, 'l0'))} changes each, on the same frames")
    check("and the plain kind do not", flips(U, "l0") != flips(U, "l2"),
          f"rooms 0 and 2 first change on frames "
          f"{flips(U, 'l0')[:1]} and {flips(U, 'l2')[:1]}")
    # A glow, which is the one that passes through the levels in between --
    # and turns round at both ends, which is what makes it a glow and not a
    # light being switched off slowly.
    e6, _, G = dumpfix("liteglow", spec(1, 8) + "\\dtSpawnSpecials", L1,
                       nf=40, stand=True)
    gl = [r["l1"] for r in G]
    turns = sum(1 for a, b, c in zip(gl, gl[1:], gl[2:])
                if (b - a) * (c - b) < 0)
    check("a glow runs down to the dimmest neighbour and back, repeatedly",
          e6 == 0 and gl and min(gl) == 128 and max(gl) == 176 and turns >= 4
          and len(set(gl)) > 2,
          f"{min(gl) if gl else '?'}..{max(gl) if gl else '?'} through "
          f"{len(set(gl))} levels, {turns} turns in {len(gl)} frames")
    # The two that flicker.  Fire never goes below the dimmest neighbour plus
    # sixteen; the loose connection is either on or off and is mostly on.
    e7, _, R = dumpfix("litefire", spec(1, 17) + "\\dtSpawnSpecials", L1,
                       nf=60, stand=True)
    e8, _, K = dumpfix("liteflash", spec(1, 1) + "\\dtSpawnSpecials", L1,
                       nf=120, stand=True)
    fl = [r["l1"] for r in R]
    kr = runs(r["l1"] for r in K)
    check("a fire flicker stays between its floor and its own light",
          e7 == 0 and fl and set(fl) <= {176, 160, 144} and len(set(fl)) > 1,
          f"levels {sorted(set(fl))}; the floor is 128 + 16")
    check("a flickering light is on or off, and never off for long",
          e8 == 0 and {v for v, _ in kr} == {176, 128}
          and max(n for v, n in kr if v == 128) <= 3
          and sum(n for v, n in kr if v == 176)
          > 3 * sum(n for v, n in kr if v == 128),
          f"off for at most {max([n for v, n in kr if v == 128] or [0])} "
          f"frames at a time, on for "
          f"{sum(n for v, n in kr if v == 176)} of {len(K)}")

    # ---- a thing is lit by the room it is in NOW ----------------------------
    # dtHl is the light a thing was placed in; a thing that also knows its
    # sector has to read that sector as it is this frame, or a strobing room
    # strobes round a monster that does not.  Asked of the macro directly:
    # 100 is a value no room on this map has, so it can only be dtHl.
    e9, tl, _ = dumpfix(
        "thinglit",
        "\\dtAddThing{500}{256}{2035}{24}%\n"
        "\\expandafter\\gdef\\csname dtHl0\\endcsname{100}%\n"
        "\\dtTI=0 \\message{^^JTLA \\dtThingLight}%\n"
        "\\expandafter\\gdef\\csname dtHs0\\endcsname{1}%\n"
        "\\message{^^JTLB \\dtThingLight}%\n"
        "\\expandafter\\gdef\\csname dtSl1\\endcsname{77}%\n"
        "\\message{^^JTLC \\dtThingLight}", L1, nf=0)
    got = [int(v) for v in re.findall(r"TL[ABC] (\d+)", flat(tl))]
    check("a thing is lit by the room it is standing in now",
          e9 == 0 and got == [100, 176, 77],
          f"{got}: its placed light with no sector, then room 1's 176, then "
          f"77 the moment the room changes")

    # ---- floors that burn ---------------------------------------------------
    # Standing in room 1.  Doom hurts on every thirty-second tic of the
    # level clock, which is every thirteenth frame, so forty frames is three
    # hits -- and the AMOUNT is the special's own: 5 for nukage, 10 for
    # hellslime, 20 for the strong kind.
    def burn(name, n, hook="", pre="", x=500, nf=40):
        return dumpfix(name, spec(1, n) + "\\dtSpawnSpecials" + hook, L1,
                       nf=nf, stand=True, pre=pre + f"\\def\\DTX{{{x}}}\n")

    def drops(rows):
        return [a["hp"] - b["hp"] for a, b in zip(rows, rows[1:])
                if a["hp"] != b["hp"]]

    b1, _, B5 = burn("burn5", 5)
    b2, _, B7 = burn("burn7", 7)
    b3, _, B16 = burn("burn16", 16)
    b4, _, BN = burn("burnno", 5, pre="\\def\\DTNOSPEC{1}\n")
    b5, _, BO = burn("burnout", 5, x=100)
    check("floor fixtures compile clean", b1 + b2 + b3 + b4 + b5 == 0,
          f"{b1 + b2 + b3 + b4 + b5} errors")
    check("a burning floor hurts every thirteenth frame, not every frame",
          drops(B5) == [10, 10, 10],
          f"health fell by {drops(B5)} over {len(B5)} frames")
    check("and by the amount its own special names",
          set(drops(B7)) == {5} and set(drops(B16)) == {20},
          f"special 7 took {sorted(set(drops(B7)))}, special 16 took "
          f"{sorted(set(drops(B16)))}, special 5 took 10")
    check("and only while you are standing in it",
          bool(BO) and BO[-1]["hp"] == 100 and bool(BN)
          and BN[-1]["hp"] == 100,
          f"from the room next door {BO[-1]['hp'] if BO else '?'}, and "
          f"under \\DTNOSPEC {BN[-1]['hp'] if BN else '?'}")
    # The suit, and invulnerability, which are two different mechanisms: one
    # is asked by the floor and the other by \dtDamagePlayer.
    b6, _, BS = burn("burnsuit", 5, hook="\\dtSUIT=1000 ")
    b7, _, BI = burn("burngod", 16, hook="\\dtINVUL=1000 ")
    check("the radiation suit stops it",
          b6 == 0 and bool(BS) and BS[-1]["hp"] == 100
          and BS[-1]["burn"] == 0,
          f"health {BS[-1]['hp'] if BS else '?'} in the suit against "
          f"{B5[-1]['hp'] if B5 else '?'} without")
    check("and invulnerability stops even the strong kind",
          b7 == 0 and bool(BI) and BI[-1]["hp"] == 100,
          f"health {BI[-1]['hp'] if BI else '?'} against "
          f"{B16[-1]['hp'] if B16 else '?'} without")
    # Special 11 cannot quite kill you, and ends the level when it nearly
    # has.  Eighty frames are asked for and it must not take them.
    b8, xlog, BX = burn("burnexit", 11, nf=90)
    hx = [r["hp"] for r in BX]
    check("the last floor of an episode ends the level instead of killing "
          "you", b8 == 0 and hx and 0 < hx[-1] < 11 and len(hx) < 90
          and "exit reached" in flat(xlog),
          f"health ran {hx[0] if hx else '?'} -> {hx[-1] if hx else '?'} "
          f"and the level ended after {len(hx)} of 90 frames")

    # ---- secrets ------------------------------------------------------------
    # Room 1 is a secret and the scripted walk goes straight through it, six
    # or seven frames of standing in it -- and it is found ONCE.
    s1, slog, SC = dumpfix("secret", spec(1, 9) + "\\dtSpawnSpecials", L1,
                           nf=20)
    s2, _, SN = dumpfix("secretno", spec(1, 9) + "\\dtSpawnSpecials", L1,
                        nf=20, pre="\\def\\DTNOSPEC{1}\n")
    inside = sum(1 for _, _, s, _, _ in track(slog) if s == 1)
    check("a secret is counted when it is walked into, and once",
          s1 == 0 and bool(SC) and SC[-1]["sc"] == 1 and SC[-1]["st"] == 1
          and inside > 2,
          f"{SC[-1]['sc'] if SC else '?'} of {SC[-1]['st'] if SC else '?'} "
          f"after {inside} frames inside it")
    check("and with \\DTNOSPEC there are none to find",
          s2 == 0 and bool(SN) and SN[-1]["sc"] == 0 and SN[-1]["st"] == 0,
          f"{SN[-1]['sc'] if SN else '?'} of {SN[-1]['st'] if SN else '?'}")

    # ---- a scrolling wall ---------------------------------------------------
    # The built-in map has no sidedefs, so one is made for line 0 by hand:
    # special 48 slides its x offset two and a half units a frame.
    SCR = ("\\dtAddSide{0}{-1}{-1}{-1}{0}{0}\\dtAddLineSide{0}{0}{-1}{0}%\n"
           "\\dtAddLineSpec{0}{48}\\dtFindDoors\n")
    w1, _, W = dumpfix("scroll", SCR, [("x", cs("dtYx0"))], nf=10, stand=True)
    w2, _, WN = dumpfix("scrollno", SCR, [("x", cs("dtYx0"))], nf=10,
                        stand=True, pre="\\def\\DTNOSPEC{1}\n")
    check("a scrolling wall slides two and a half units a frame",
          w1 + w2 == 0 and bool(W) and W[-1]["x"] == 25 and bool(WN)
          and WN[-1]["x"] == 0,
          f"offset {W[-1]['x'] if W else '?'} after {len(W)} frames, and "
          f"{WN[-1]['x'] if WN else '?'} under \\DTNOSPEC")

    # ---- the array two features were sharing --------------------------------
    # dtRS was both a ceiling mover's state and the shade of a screen row, so
    # marking a crusher on sector n repainted row n of every procedural
    # plane.  Nothing has to MOVE for that -- the line is never crossed --
    # but it does have to be a row whose shade is not already 0, and with
    # three sectors that means a view six rows tall: row 2 is one above the
    # horizon there and shades at 3.  Asked of \PlaneRun itself, before and
    # after the mark, because on a view that small there is no ceiling in
    # shot to look at.
    m1, mlog, _ = dumpfix(
        "shademark",
        "\\message{^^JSHADEA \\PlaneRun{2}{3}{0}}%\n"
        "\\dtAddSectorTag{2}{7}\\dtAddLineTag{3}{7}"
        "\\dtAddLineSpec{3}{6}\\dtFindDoors\n"
        "\\message{^^JSHADEB \\PlaneRun{2}{3}{0}}",
        [("hp", "\\the\\dtHEALTH")], nf=0, h=6)
    sh = re.findall(r"SHADE[AB] ([0-9A-F]+)", flat(mlog))
    check("marking a ceiling mover does not repaint the planes",
          m1 == 0 and len(sh) == 2 and sh[0] == sh[1] and sh[0] != "00",
          f"row 2 shades at {sh[0] if sh else '?'} before a crusher is "
          f"marked on sector 2 and {sh[1] if len(sh) > 1 else '?'} after")

    print("teleporters, rising floors and shot switches (M23)")
    POS = [("x", "\\number\\numexpr\\PX/65536\\relax"),
           ("y", "\\number\\numexpr\\PY/65536\\relax"),
           ("a", "\\the\\PA"), ("sec", "\\the\\dtSECN"),
           ("eye", "\\number\\numexpr\\PZ/65536\\relax"),
           ("tp", "\\the\\dtTELEPORTS"), ("kill", "\\the\\dtKILLS")]
    # The portal out of room 0 teleports to tag 5, which is room 2.  TWO
    # destinations are on the map and the wrong one comes first in the thing
    # list: it stands in room 0, whose tag is not 5, so an engine that took
    # the first destination it found would put the player there.  The right
    # one faces north.
    DEST = ("\\dtAddThing{50}{50}{14}{0}\\dtAddThing{900}{400}{14}{-16}%\n"
            "\\expandafter\\gdef\\csname dtHa1\\endcsname{2048}%\n"
            "\\dtAddSectorTag{2}{5}\\dtAddLineTag{3}{5}%\n")
    t1, _, T = dumpfix("tele", DEST + "\\dtAddLineSpec{3}{97}\\dtFindDoors",
                       POS, nf=14)
    t2, _, TN = dumpfix("teleno", DEST + "\\dtFindDoors", POS, nf=14)
    land = next((r for r in T if r["tp"] == 1), None)
    check("teleport fixtures compile clean", t1 + t2 == 0,
          f"{t1 + t2} errors")
    check("crossing a teleporter puts you on the destination its tag names",
          bool(land) and (land["x"], land["y"]) == (900, 400)
          and land["sec"] == 2,
          f"landed at {(land['x'], land['y']) if land else '?'} in sector "
          f"{land['sec'] if land else '?'}; the decoy is at (50, 50)")
    check("facing the way it faces, at eye height at once",
          bool(land) and land["a"] == 2048 and land["eye"] == 25,
          f"angle {land['a'] if land else '?'}, eye "
          f"{land['eye'] if land else '?'} on a floor of -16")
    check("and with no special the walk just carries on",
          bool(TN) and TN[-1]["tp"] == 0 and 384 < TN[-1]["x"] < 700
          and TN[-1]["y"] == 256,
          f"control reached {(TN[-1]['x'], TN[-1]['y']) if TN else '?'}")
    # From the BACK it does nothing.  The same line, walked through westward
    # out of room 1, which is its back sector.
    t3, _, TB = dumpfix("teleback", DEST
                        + "\\dtAddLineSpec{3}{97}\\dtFindDoors", POS, nf=8,
                        pre="\\def\\DTX{500}\\def\\DTANG{4096}\n")
    check("a teleporter only takes you in through its front",
          t3 == 0 and bool(TB) and TB[-1]["tp"] == 0 and TB[-1]["x"] < 384,
          f"walked west across it to x={TB[-1]['x'] if TB else '?'} with "
          f"{TB[-1]['tp'] if TB else '?'} teleports")
    # ...and whatever is standing on the pad dies of it.
    t4, _, TF = dumpfix("telefrag", DEST + "\\dtAddLineSpec{3}{97}"
                        "\\dtFindDoors\\dtAddThing{905}{395}{3004}{-16}"
                        "\\dtSpawnMonsters\\def\\dtThingTick{}", POS, nf=14)
    check("arriving on top of a monster kills it",
          t4 == 0 and bool(TF) and TF[-1]["tp"] == 1 and TF[-1]["kill"] == 1,
          f"{TF[-1]['kill'] if TF else '?'} killed by "
          f"{TF[-1]['tp'] if TF else '?'} teleport")

    # ---- floors that rise ---------------------------------------------------
    FL = [("f0", cs("dtSf0")), ("f1", cs("dtSf1")), ("f2", cs("dtSf2")),
          ("p2", cs("dtSp2")), ("eye", "\\number\\numexpr\\PZ/65536\\relax")]

    def raise_(name, special, sector=2, pre_hook="", nf=24, stand=True, x=100):
        return dumpfix(
            name, pre_hook + f"\\dtAddSectorTag{{{sector}}}{{6}}"
            f"\\dtAddLineTag{{3}}{{6}}\\dtAddLineSpec{{3}}{{{special}}}"
            "\\dtFindDoors\\dtFireLine{3}", FL, nf=nf, stand=stand,
            pre=f"\\def\\DTX{{{x}}}\n")

    # "The next floor up" has to be the NEAREST one above, so the sector that
    # moves needs two higher neighbours to choose between.  Room 1 dropped to
    # -40 has room 2 at -16 and room 0 at 0.
    DROP = "\\expandafter\\xdef\\csname dtSf1\\endcsname{-40}%\n"
    r1, _, RN = raise_("raisenext", 119, sector=1, pre_hook=DROP)
    r2, _, RC = raise_("raiseceil", 5)
    r3, _, RK = raise_("raisecrush", 56)
    r4, _, R24 = raise_("raise24", 58)
    r5, _, RCH = raise_("raisechg", 59)
    r6, _, RT = raise_("raiseturbo", 130, sector=1, pre_hook=DROP)
    check("raise fixtures compile clean", r1 + r2 + r3 + r4 + r5 + r6 == 0,
          f"{r1 + r2 + r3 + r4 + r5 + r6} errors")
    f1 = [r["f1"] for r in RN]
    check("a floor raised to the next one up stops at the NEAREST above it",
          bool(f1) and f1[-1] == -16 and {b - a for a, b in zip(f1, f1[1:])}
          <= {0, 8},
          f"room 1 went -40 -> {f1[-1] if f1 else '?'} in eights; its "
          f"neighbours are at -16 and 0")
    check("one raised to the ceiling stops at the lowest ceiling round it",
          bool(RC) and RC[-1]["f2"] == 96 and bool(RK)
          and RK[-1]["f2"] == 88,
          f"room 2 rose to {RC[-1]['f2'] if RC else '?'} under room 1's "
          f"ceiling of 96, and to {RK[-1]['f2'] if RK else '?'} for the "
          f"kind that stops eight short")
    check("one raised by 24 rises 24", bool(R24) and R24[-1]["f2"] == 8,
          f"room 2 went -16 -> {R24[-1]['f2'] if R24 else '?'}")
    # "...and change": room 2's floor takes the look of the sector in front
    # of the line, which is room 0 -- ramp 32 where its own was 80.
    check("and the changing kind takes the floor of the line's front sector",
          bool(RCH) and RCH[-1]["f2"] == 8 and RCH[-1]["p2"] == 32
          and bool(R24) and R24[-1]["p2"] == 80,
          f"floor ramp {RCH[-1]['p2'] if RCH else '?'} (room 0's) against "
          f"{R24[-1]['p2'] if R24 else '?'} for the kind that does not")
    # The first reading is taken after the first tick, so the -40 it started
    # from is put back on the front: 24 units is one step of sixteen and one
    # of eight, where the ordinary kind takes three of eight.
    ft = [-40] + [r["f1"] for r in RT]
    st = [b - a for a, b in zip(ft, ft[1:]) if b != a]
    check("the turbo kind climbs sixteen a frame",
          ft[-1] == -16 and st == [16, 8]
          and [b - a for a, b in zip([-40] + f1, f1) if b != a] == [8, 8, 8],
          f"-40 to -16 in steps of {st}, against the ordinary kind's "
          f"{[b - a for a, b in zip([-40] + f1, f1) if b != a]}")
    # Standing on it.  The eye has to arrive at the new floor plus 41.
    r7, _, RR = raise_("raiseride", 58, x=800)
    check("and a player standing on a rising floor rides it up",
          r7 == 0 and bool(RR) and RR[-1]["f2"] == 8
          and RR[-1]["eye"] == 8 + VIEWHEIGHT,
          f"eye {RR[-1]['eye'] if RR else '?'} on a floor of "
          f"{RR[-1]['f2'] if RR else '?'}")
    # Turbo lower: the highest neighbouring floor and eight more.  Room 1's
    # neighbours are 0 and -16, so it comes down from 24 to 8.
    r8, _, TL = raise_("turbolow", 36, sector=1)
    check("a turbo lower stops eight above the highest floor round it",
          r8 == 0 and bool(TL) and TL[-1]["f1"] == 8,
          f"room 1 went 24 -> {TL[-1]['f1'] if TL else '?'}")
    # A perpetual lift runs between the lowest and highest floors round it
    # for ever -- room 1 between -16 and 24 -- and a stop line holds it.  The
    # walk crosses line 3 to start it and line 10 to stop it.
    PERP = ("\\dtAddSectorTag{1}{6}\\dtAddLineTag{3}{6}"
            "\\dtAddLineSpec{3}{87}")
    r9, _, PP = dumpfix("perp", PERP + "\\dtFindDoors\\dtFireLine{3}", FL,
                        nf=130, stand=True)
    r10, _, PS = dumpfix("perpstop", PERP + "\\dtAddLineTag{10}{6}"
                         "\\dtAddLineSpec{10}{89}\\dtFindDoors", FL, nf=60)
    pf = [r["f1"] for r in PP]
    pt = sum(1 for a, b in zip(runs(pf), runs(pf)[1:])
             if a[0] in (-16, 24) and a[1] > 5)
    check("a perpetual lift goes down, comes back, and goes down again",
          r9 == 0 and pf and min(pf) == -16 and max(pf) == 24 and pt >= 3,
          f"between {min(pf) if pf else '?'} and {max(pf) if pf else '?'}, "
          f"waiting at an end {pt} times in {len(pf)} frames")
    sf = [r["f1"] for r in PS]
    check("and a stop line holds it where it is",
          r10 == 0 and len(set(sf)) > 1 and len(set(sf[-30:])) == 1
          and len(set(pf[-30:])) > 1,
          f"held at {sf[-1] if sf else '?'} for the last thirty frames; "
          f"unstopped it was still moving through "
          f"{sorted(set(pf[-30:]))[:4]}...")

    # ---- a door you open by shooting it -------------------------------------
    # Room 2 is shut and tagged; the portal out of room 0 is a GR door line.
    # The player never moves: a pistol shot along the facing crosses that
    # line 284 units out and the door two rooms away has to open.
    GD = ("\\expandafter\\xdef\\csname dtSc2\\endcsname{-16}%\n"
          "\\dtAddSectorTag{2}{7}\\dtAddLineTag{3}{7}\\dtAddLineSpec{3}{46}"
          "\\dtFindDoors\n")
    C2 = [("c2", cs("dtSc2")), ("shots", "\\the\\dtSHOTS")]
    g1, _, GS = dumpfix("gundoor", GD + "\\dtFire", C2, nf=16, stand=True)
    g2, _, GN = dumpfix("gundoorno", GD, C2, nf=16, stand=True)
    g3, _, GA = dumpfix("gundooraway", GD + "\\dtFire", C2, nf=16,
                        stand=True, pre="\\def\\DTANG{4096}\n")
    check("gun-door fixtures compile clean", g1 + g2 + g3 == 0,
          f"{g1 + g2 + g3} errors")
    check("shooting a gun-activated line opens the door its tag names",
          bool(GS) and GS[-1]["c2"] == 92 and GS[-1]["shots"] == 1,
          f"one shot, and room 2's ceiling went -16 -> "
          f"{GS[-1]['c2'] if GS else '?'} (room 1's 96 less four)")
    check("and not firing, or firing the other way, leaves it shut",
          bool(GN) and GN[-1]["c2"] == -16 and bool(GA)
          and GA[-1]["c2"] == -16 and GA[-1]["shots"] == 1,
          f"no shot: {GN[-1]['c2'] if GN else '?'}; a shot due west: "
          f"{GA[-1]['c2'] if GA else '?'}")
    # A bullet stops at the first thing it hits, so a monster standing in
    # the way takes the shot and the line behind it never hears about it.
    g4, _, GM = dumpfix("gundoormon", GD + "\\dtAddThing{250}{256}{3004}{0}"
                        "\\dtSpawnMonsters\\def\\dtThingTick{}"
                        "\\expandafter\\gdef\\csname dtOh0\\endcsname{9000}"
                        "\\dtFire", C2, nf=16, stand=True)
    check("and a monster in the way takes the bullet instead",
          g4 == 0 and bool(GM) and GM[-1]["c2"] == -16,
          f"room 2's ceiling stayed at {GM[-1]['c2'] if GM else '?'}")

    print("powers, the tracer, autoaim and the BFG (M23)")
    # The give rules and the two things a power changes that a render cannot
    # show, asked directly.
    # \R is defined in the preamble rather than in the hook: \DTHOOK is itself
    # a \def, and a macro with a parameter cannot be written inside one
    # without doubling every hash.
    e, plog, _ = dumpfix(
        "powunit",
        "\\dt@facepick\\R{BASE}%\n"
        "\\dtDamagePlayer{30}{0}{0}\\dtHURTC=0 \\R{HURT}%\n"
        "\\dtINVUL=100 \\dtDamagePlayer{30}{0}{0}\\R{GOD}%\n"
        "\\dtINVUL=0 "
        # the number keys: shotgun and super shotgun owned, no chainsaw
        "\\expandafter\\gdef\\csname dtUW2\\endcsname{1}%\n"
        "\\expandafter\\gdef\\csname dtUW8\\endcsname{1}%\n"
        "\\dtSelectWeapon{3}\\R{KEYA}\\dtSelectWeapon{3}\\R{KEYB}%\n"
        "\\dtSelectWeapon{3}\\R{KEYC}\\dtSelectWeapon{5}\\R{KEYD}%\n"
        "\\dtSelectWeapon{1}\\R{KEYE}\\dtSelectWeapon{2}\\R{KEYF}",
        [("hp", "\\the\\dtHEALTH")], nf=0,
        pre="\\def\\R#1{\\message{^^JPOW #1 hp=\\the\\dtHEALTH\\space"
            " iv=\\the\\dtINVUL\\space wp=\\the\\dtWEAP\\space"
            " face=\\dtFaceKey\\space end}}\n")
    P = {m[0]: m[1:] for m in re.findall(
        r"POW (\w+) hp=(-?\d+) iv=(\d+) wp=(\d+) face=(\w+) end", flat(plog))}
    check("power unit fixture compiles clean", e == 0 and len(P) == 9,
          f"{e} errors, {len(P)} readings")
    check("invulnerability takes no damage, and the same hit without it "
          "does", P.get("HURT", ("",))[0] == "70"
          and P.get("GOD", ("",))[0] == "70",
          f"30 points took health to {P.get('HURT', ('?',))[0]}; thirty "
          f"more while invulnerable left it at {P.get('GOD', ('?',))[0]}")
    check("and it shows on the face", P.get("GOD", ("",) * 4)[3] == "STFGOD0"
          and P.get("BASE", ("",) * 4)[3].startswith("STFST"),
          f"{P.get('GOD', ('?',) * 4)[3]} against "
          f"{P.get('BASE', ('?',) * 4)[3]}")
    keys = [P.get(k, ("", "", "?"))[2] for k in
            ("KEYA", "KEYB", "KEYC", "KEYD", "KEYE", "KEYF")]
    check("a number key picks its slot, swaps a pair, and refuses a weapon "
          "you do not own", keys == ["2", "8", "2", "2", "0", "1"],
          f"3 3 3 5 1 2 gave weapons {' '.join(keys)}: shotgun, super "
          f"shotgun, shotgun, no rocket launcher, fist, pistol")
    # Picking one up, by the standing fixture pickstand.tex uses: one unit
    # of movement in the hook drives the touch scan and then the walk stops.
    PW = [("iv", "\\the\\dtINVUL"), ("got", "\\the\\dtNPICK")]
    PICK = ("\\dtAddThing{310}{256}{2022}{0}%\n"
            "\\dtStepFwd{65536}\\def\\dtStepFwd##1{}")
    p1, _, PA = dumpfix("powpick", PICK, PW, nf=6, pre="\\def\\DTX{280}\n")
    p2, _, PN = dumpfix("powpickno", PICK, PW, nf=6,
                        pre="\\def\\DTX{280}\\def\\DTNOPOW{1}\n")
    iv = [r["iv"] for r in PA]
    check("an invulnerability sphere is taken and starts counting down",
          p1 == 0 and bool(PA) and PA[-1]["got"] == 1 and iv[0] == 419
          and all(a - b == 1 for a, b in zip(iv, iv[1:])),
          f"{iv[0] if iv else '?'} frames left after the first tick of 420, "
          f"one fewer each frame")
    check("and under \\DTNOPOW it stays on the floor",
          p2 == 0 and bool(PN) and PN[-1]["got"] == 0
          and PN[-1]["iv"] == 0,
          f"taken {PN[-1]['got'] if PN else '?'}, timer "
          f"{PN[-1]['iv'] if PN else '?'}")
    # Berserk: the same punch on the same die, ten times as hard.  The
    # monster is at arm's length with more health than a punch can take.
    MH = [("mh", cs("dtOh0"))]
    FIST = ("\\dtAddThing{140}{256}{3004}{0}\\dtSpawnMonsters"
            "\\def\\dtThingTick{}%\n"
            "\\expandafter\\gdef\\csname dtOh0\\endcsname{5000}%\n"
            "\\dtWEAP=0 ")
    k1, _, KB = dumpfix("berserk", FIST + "\\dtBERSERK=1 \\dtFire", MH,
                        nf=1, stand=True)
    k2, _, KN = dumpfix("berserkno", FIST + "\\dtFire", MH, nf=1, stand=True)
    db = 5000 - KB[-1]["mh"] if KB else 0
    dn = 5000 - KN[-1]["mh"] if KN else 0
    check("a berserk punch lands ten times as hard",
          k1 + k2 == 0 and dn > 0 and db == 10 * dn,
          f"{db} points against {dn} for the same punch without the pack")
    # Partial invisibility: a zombieman that would hit every time it fired
    # now mostly misses.  Sixty frames of standing in front of one.
    HP = [("hp", "\\the\\dtHEALTH")]
    ZOM = "\\dtAddThing{330}{256}{3004}{0}\\dtSpawnMonsters "
    i1, _, IN = dumpfix("blurno", ZOM + "\\dtHEALTH=10000 ", HP, nf=60,
                        stand=True)
    i2, _, IB = dumpfix("blur", ZOM + "\\dtHEALTH=10000 \\dtINVIS=10000 ",
                        HP, nf=60, stand=True)
    ln = 10000 - IN[-1]["hp"] if IN else 0
    lb = 10000 - IB[-1]["hp"] if IB else 0
    check("something aiming at a partly invisible player mostly misses",
          i1 + i2 == 0 and ln > 0 and lb < ln,
          f"{lb} points lost in sixty frames against {ln} when it can see "
          f"you properly")

    # ---- the tracer ---------------------------------------------------------
    # A revenant's missile launched the WRONG way: due south from (300,400),
    # with the player at (100,256).  Flying straight it meets the south wall;
    # homing, it comes round and finds the player.  The revenant that owns it
    # is on the map and asleep, because a missile needs an owner to have a
    # target and this one must not throw anything of its own.
    MS = [("mx", cs("dtHx1")), ("my", cs("dtHy1")), ("ms", cs("dtJs1")),
          ("ma", cs("dtHa1")), ("hp", "\\the\\dtHEALTH"),
          ("booms", "\\the\\dtBOOMS")]
    TR = ("\\dtAddThing{300}{470}{66}{0}\\dtSpawnMonsters"
          "\\def\\dtThingTick{}%\n"
          "\\dtSpawnMissile{9004}{300}{400}{32}{300}{0}{32}{0}")
    h1, _, HM = dumpfix("tracer", TR, MS, nf=30, stand=True)
    h2, _, HN = dumpfix("tracerno", TR, MS, nf=30, stand=True,
                        pre="\\def\\DTNOHOME{1}\n")
    hit = next((r for r in HM if r["ms"] == 1), None)
    hitn = next((r for r in HN if r["ms"] == 1), None)
    check("tracer fixtures compile clean", h1 + h2 == 0, f"{h1 + h2} errors")
    check("a revenant's missile turns in flight and finds the player",
          bool(hit) and HM[-1]["hp"] < 100 and abs(hit["mx"] - 100) < 60
          and abs(hit["my"] - 256) < 60,
          f"launched due south from (300,400), it went off at "
          f"{(hit['mx'], hit['my']) if hit else '?'} beside a player at "
          f"(100,256), who is on {HM[-1]['hp'] if HM else '?'}")
    # ...and it comes ROUND, which is the difference between a tracer and a
    # missile that simply knows where you are: Doom allows it 16.875 degrees
    # every fourth tic, which is 240 of this engine's units a frame.  The
    # fixture needs most of a quarter turn, so it has to spend several
    # frames on it and use the whole allowance on at least one of them.
    fly = [r["ma"] for r in HM if r["ms"] == 0]
    turn = [min((a - b) % 8192, (b - a) % 8192) for a, b in zip(fly, fly[1:])]
    check("turning no faster than Doom's tracer can",
          bool(turn) and max(turn) == 240 and sum(1 for t in turn if t) >= 4,
          f"at most {max(turn) if turn else '?'} units a frame over "
          f"{sum(1 for t in turn if t)} frames of turning")
    check("and with \\DTNOHOME it flies straight into the wall it was "
          "aimed at", bool(hitn) and HN[-1]["hp"] == 100
          and hitn["my"] < 60 and abs(hitn["mx"] - 300) < 8,
          f"control went off at {(hitn['mx'], hitn['my']) if hitn else '?'} "
          f"against the south wall, player untouched")

    # ---- vertical autoaim ---------------------------------------------------
    # A target hanging sixty units up, dead ahead.  The rocket leaves at 32
    # and has to CLIMB toward the middle of it -- 60 + 28 -- where a level
    # one would arrive still at 32.  It goes off against the near side of
    # the target's box rather than at its centre, so it has covered most of
    # that climb and not all of it.  An explosion against a thing is not
    # clamped to the floor, so the z at the impact is the reading.
    MZ = [("z", "\\ifnum\\dtMISBASE<0 0\\else\\csname dtHz\\the\\dtMISBASE"
                "\\endcsname\\fi"),
          ("s", "\\ifnum\\dtMISBASE<0 -1\\else\\csname dtJs\\the\\dtMISBASE"
                "\\endcsname\\fi")]
    AIM = ("\\dtAddThing{330}{256}{3005}{60}\\dtSpawnMonsters"
           "\\def\\dtThingTick{}%\n"
           "\\expandafter\\gdef\\csname dtOh0\\endcsname{9000}%\n"
           "\\dtWEAP=4 \\expandafter\\gdef\\csname dtUW4\\endcsname{1}%\n"
           "\\expandafter\\gdef\\csname dtUA3\\endcsname{9}%\n\\dtFire")
    a1, _, AA = dumpfix("autoaim", AIM, MZ, nf=8, stand=True)
    a2, _, AN = dumpfix("autoaimno", AIM, MZ, nf=8, stand=True,
                        pre="\\def\\DTNOAIM{1}\n")
    za = next((r["z"] for r in AA if r["s"] == 1), None)
    zn = next((r["z"] for r in AN if r["s"] == 1), None)
    check("autoaim fixtures compile clean", a1 + a2 == 0, f"{a1 + a2} errors")
    check("a player's rocket climbs to a target above it",
          za is not None and 56 <= za <= 88 and zn == 32,
          f"it went off at z={za} on its way up to a target whose middle is "
          f"at 88, and at z={zn} with \\DTNOAIM -- the height it left at")

    # ---- the BFG's forty rays -----------------------------------------------
    # Four monsters that cannot die and do not move.  A is dead ahead and
    # takes the ball; B is directly behind A, inside the angle A subtends, so
    # every ray that would reach B meets A first; C is 34 degrees off to the
    # side, inside the fan; D is square on the player's left, outside it.
    # Until M23 the spray was an arc and B was caught with the rest.
    BH = [("a", cs("dtOh0")), ("b", cs("dtOh1")), ("c", cs("dtOh2")),
          ("d", cs("dtOh3")), ("rays", "\\the\\dtBFGHITS"),
          ("booms", "\\the\\dtBOOMS")]
    BFG = ("\\dtAddThing{250}{256}{3004}{0}\\dtAddThing{340}{256}{3004}{0}%\n"
           "\\dtAddThing{250}{356}{3004}{0}\\dtAddThing{100}{420}{3004}{0}%\n"
           "\\dtSpawnMonsters\\def\\dtThingTick{}%\n"
           + "".join(f"\\expandafter\\gdef\\csname dtOh{i}\\endcsname"
                     "{90000}%\n" for i in range(4))
           + "\\dtWEAP=6 \\expandafter\\gdef\\csname dtUW6\\endcsname{1}%\n"
           "\\expandafter\\gdef\\csname dtUA2\\endcsname{40}%\n\\dtFire")
    f1e, _, BF = dumpfix("bfgrays", BFG, BH, nf=8, stand=True)
    end = BF[-1] if BF else None
    check("BFG fixture compiles clean", f1e == 0 and bool(end)
          and end["booms"] == 1, f"{f1e} errors, "
          f"{end['booms'] if end else '?'} explosions")
    check("a BFG's rays catch what is in front and to the side",
          bool(end) and end["a"] < 90000 and end["c"] < 90000,
          f"A lost {90000 - end['a'] if end else '?'}, C -- 34 degrees off "
          f"-- lost {90000 - end['c'] if end else '?'}")
    check("but not what is outside the fan, or hidden behind something",
          bool(end) and end["b"] == 90000 and end["d"] == 90000,
          f"B, directly behind A, lost {90000 - end['b'] if end else '?'}; "
          f"D, square on the left, lost {90000 - end['d'] if end else '?'}")
    check("and forty rays is a handful of hits, not forty",
          bool(end) and 4 <= end["rays"] <= 16,
          f"{end['rays'] if end else '?'} of the forty found something")

    print("the key loop: strafing, weapons and the exit (M23)")
    # z and c step sideways without turning.  Facing east, left is north, so
    # y goes up by a stride and comes back.
    open("strafe.tex", "w").write(
        "\\def\\DTPLAY{1}\\def\\DTW{24}\\def\\DTH{16}\\def\\DTPIX{2}\n"
        "\\input doom.tex\n")
    _, se, slog2 = build("strafe.tex", stdin="z\nz\nc\n3\nq\n",
                         interactive=True)
    ys = [int(v) for v in re.findall(r"\s\d+,(\d+) ang \d+", slog2)]
    check("z and c strafe without turning",
          se == 0 and ys[:4] == [256, 296, 336, 296],
          f"y went {ys[:4]} facing east, forty units a step")
    # The exit ends a PLAYED level too.  Line 3 is a W1 exit; ten forward
    # keys are sent and the engine must stop itself on the eighth.
    open("playexit.tex", "w").write(
        "\\def\\DTHOOK{\\dtAddLineSpec{3}{52}\\dtFindDoors}\n"
        "\\def\\DTPLAY{1}\\def\\DTW{24}\\def\\DTH{16}\\def\\DTPIX{2}\n"
        "\\input doom.tex\n")
    _, pe, plog2 = build("playexit.tex", stdin="w\n" * 12, interactive=True)
    nfr = len(frames("playexit.pdf"))
    check("reaching the exit ends a played level",
          pe == 0 and "level complete" in flat(plog2) and 0 < nfr < 12,
          f"{nfr} frames drawn for twelve keys, and the tally says "
          f"{'level complete' if 'level complete' in flat(plog2) else 'nothing'}")


def main():
    if "--clean" in sys.argv:
        # A few tries, and then the truth.  On Windows a directory that
        # something else still has open -- a terminal sitting in it, a virus
        # scanner half way through a PDF -- empties but will not go, and
        # saying "removed" over a folder that is still there is how a
        # clean-up step stops being believed.
        for _ in range(5):
            shutil.rmtree(BUILD, ignore_errors=True)
            if not os.path.exists(BUILD):
                break
            time.sleep(1)
        if os.path.exists(BUILD):
            left = sum(len(f) for _, _, f in os.walk(BUILD))
            print(f"could not remove {BUILD} ({left} files left in it) -- "
                  f"is something else using it?")
            return 1
        print(f"removed {BUILD}")
        return 0
    quick = "--quick" in sys.argv
    stage()
    # Written afresh every run.  This used to skip a fixture that already
    # existed, which is how an edit to the table above could go untested
    # for as long as the old file stayed on disk.
    for f, body in FIXTURES:
        open(f, "w").write(body)

    print("built-in level")
    secs, errs, demo_log = build("demo.tex")
    check("compiles clean", errs == 0, f"{errs} errors, {secs:.2f}s")
    a = frames("demo.pdf")
    check("16 frames emitted", len(a) == 16, f"got {len(a)}")
    geometry_checks("built-in", "demo.pdf")

    print("WAD-loaded level")
    secs, errs, log = build("runwad.tex")
    check("compiles clean", errs == 0, f"{errs} errors, {secs:.2f}s")
    check("BSP lumps loaded",
          "subsectors" in log and "no BSP" not in log,
          re.search(r"\[wad\] loaded:.*", log).group(0)[:78]
          if re.search(r"\[wad\] loaded:.*", log) else "no load line")
    b = frames("runwad.pdf")
    geometry_checks("WAD", "runwad.pdf")

    # The strongest check available: two completely different paths into the
    # same map arrays -- hand-written \dtAdd* calls versus a parsed binary
    # WAD -- must produce identical geometry.
    if a and b and len(a) == len(b):
        same_geom = all(edges(a[i]) == edges(b[i]) for i in range(len(a)))
        check("WAD geometry identical to built-in", same_geom)
        same_px = all(a[i][x] == b[i][x]
                      for i in range(len(a)) for x in range(len(a[i])))
        print(f"       (pixel-identical: {same_px} -- only if sector ramps "
              f"still coincide)")

    # ---- M1 / M2 ------------------------------------------------------------
    # The built-in map is three rooms in a row: sector 0 spans x<384 at floor
    # 0, sector 1 spans 384..640 at floor 24, sector 2 spans x>640 at floor
    # -16.  The scripted walk runs due east along y=256, through both portals.
    print("point location and floor following (M1, M2)")
    t = track(demo_log)
    check("frames report a position", len(t) == 16, f"got {len(t)}")
    if t:
        want = [0 if x < 384 else 1 if x < 640 else 2 for x, _, _, _, _ in t]
        got = [s for _, _, s, _, _ in t]
        check("subsector lookup finds the right sector", got == want,
              "0 -> 1 -> 2" if got == want else f"{got} vs {want}")

        # The renderer reads floor heights from the sector arrays, so a wrong
        # sector index would still render something; only this catches it.
        floors = {0: 0, 1: 24, 2: -16}
        check("sector floor heights match the map",
              all(floors[s] == f for _, _, s, f, _ in t))

        # View height converges on floor+41, and must be exact once the player
        # has been in the same sector for three frames.
        settled = [(i, s, f, e) for i, (_, _, s, f, e) in enumerate(t)
                   if i >= 2 and t[i - 1][2] == s and t[i - 2][2] == s]
        off = [i for i, s, f, e in settled if e != f + VIEWHEIGHT]
        check("view height settles on floor + 41", not off,
              f"{len(off)} frames off" if off
              else f"{len(settled)} settled frames")

        # ...and it must actually move, or "settles" would be vacuous.
        eyes = sorted({e for _, _, _, _, e in t})
        check("view height tracks the steps", len(eyes) >= 4, f"{eyes}")

    # ---- M3 -----------------------------------------------------------------
    print("collision and sliding (M3)")
    secs, errs, log = build("walk.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    t = track(log)
    if t:
        xs = [x for x, _, _, _, _ in t]
        # East wall at x=1024, player radius 16: the player must never come
        # within the radius of it, and must come to a complete stop.
        check("collision stops the walk at the wall",
              max(xs) <= 1024 - 16 and xs[-1] == xs[-5],
              f"stopped at x={xs[-1]} (wall 1024, radius 16)")
        check("the walk still crossed both portals",
              {s for _, _, s, _, _ in t} == {0, 1, 2},
              "the 24-unit step up was not wrongly blocked")

    secs, errs, log = build("slide.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    t = track(log)
    if t:
        xs = [x for x, _, _, _, _ in t]
        ys = [y for _, y, _, _, _ in t]
        # Walking northeast into the north wall (y=512): y must pin below the
        # wall while x keeps advancing for at least one frame.  A stop rather
        # than a slide would freeze both axes at once.
        pinned = [i for i in range(1, len(ys)) if ys[i] == ys[i - 1]]
        slid = [i for i in pinned if xs[i] != xs[i - 1]]
        check("blocked movement slides along the wall", bool(slid),
              f"slid on frame(s) {slid}" if slid else "movement stopped dead")
        check("never crosses the wall", max(ys) <= 512 - 16,
              f"max y={max(ys)} (wall 512, radius 16)")

    secs, errs, log = build("tunnel.tex")
    t = track(log)
    if t:
        xs = [x for x, _, _, _, _ in t]
        check("a move cannot tunnel through a wall", min(xs) >= 16,
              f"min x={min(xs)} (wall at 0, radius 16)")

    secs, errs, log = build("stress.tex")
    t = track(log)
    if t:
        # The built-in map is bounded by x 0..1024 and y 0..512.
        out = [(x, y) for x, y, _, _, _ in t
               if not (16 <= x <= 1008 and 16 <= y <= 496)]
        check("turning against walls never escapes the map", not out,
              f"{len(out)} of {len(t)} frames outside" if out
              else f"{len(t)} turning steps, all inside")

    # ---- M4: the splice primitive ------------------------------------------
    # Sprites occupy a band in the middle of a column that has already been
    # built, so this is the one operation the wall renderer never needed.  If
    # it drops or duplicates a pixel the column-length invariant catches it in
    # aggregate; this pins down the exact contents.
    print("column splice (M4)")
    secs, errs, log = build("splice.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    got = re.search(r"SPLICE:([0-9A-F]*)", log.replace("\n", ""))
    check("splice replaces the right band",
          bool(got) and got.group(1) == "0011AABBCC5566",
          got.group(1) if got else "no output")

    # A closed door is a two-sided line whose opening has collapsed.  It draws
    # as a wall, but if it does not also record its depth, everything behind it
    # passes the sprite depth test and shows straight through.
    def health_px(pdf):
        return sum(1 for f in frames(pdf) for c in f for b in c if b // 16 == 5)

    for name, fixture in (("open", "dooropen.tex"), ("shut", "doorshut.tex")):
        secs, errs, log = build(fixture)
        check(f"door {name}: compiles clean", errs == 0, f"{errs} errors")
    seen, hidden = health_px("dooropen.pdf"), health_px("doorshut.pdf")
    check("sprite is visible through an open portal", seen > 0, f"{seen} px")
    check("sprite is hidden behind a closed portal", hidden == 0,
          f"{hidden} px leaked through" if hidden else "fully occluded")

    # ---- M8: sprite rotation arithmetic -------------------------------------
    print("sprite rotation (M8)")
    secs, errs, log = build("rotmath.tex")
    check("rotmath: compiles clean", errs == 0, f"{errs} errors")
    flat = log.replace("\n", "")
    at = {(int(a), int(b)): int(v)
          for a, b, v in re.findall(r"ATAN (-?\d+) (-?\d+) (\d+)", flat)}
    # 8192 units to a turn, measured anticlockwise from +x.
    want = {(100, 0): 0, (100, 100): 1024, (0, 100): 2048, (-100, 100): 3072,
            (-100, 0): 4096, (-100, -100): 5120, (0, -100): 6144,
            (100, -100): 7168, (1000, 414): 512}
    check("arctangent is exact on the compass points",
          all(at.get(k) == v for k, v in want.items()),
          f"{sum(1 for k, v in want.items() if at.get(k) == v)}/{len(want)}"
          f" exact; 414/1000 is tan(22.5) -> {at.get((1000, 414))}")
    rot = {(int(x), int(y), int(f)): int(r)
           for x, y, f, r in re.findall(r"ROT (-?\d+) (-?\d+) (\d+) (\d+)",
                                        flat)}
    # A thing at the origin facing east.  Rotation 1 is the view of a thing
    # facing you, and the rest run anticlockwise from there; the last two
    # turn the thing instead of the camera.
    wantr = {(100, 0, 0): 1, (100, 100, 0): 2, (0, 100, 0): 3,
             (-100, 100, 0): 4, (-100, 0, 0): 5, (-100, -100, 0): 6,
             (0, -100, 0): 7, (100, -100, 0): 8,
             (100, 0, 2048): 7, (0, 100, 2048): 1}
    bad = {k: (rot.get(k), v) for k, v in wantr.items() if rot.get(k) != v}
    check("rotation 1 faces the viewer and the rest run anticlockwise",
          not bad, f"{len(wantr)} positions" if not bad else f"{bad}")

    # ---- M7: sprite silhouettes ---------------------------------------------
    # \CD is one depth per column and a portal never closes a column, so a
    # part-open door and a raised step both used to hide nothing whatever.
    print("sprite silhouettes (M7)")

    def probe_px(pdf):
        """Ramp-208 pixels per frame -- the tall billboard in the fixtures."""
        return [sum(1 for c in f for b in c if b // 16 == 13)
                for f in frames(pdf)]

    for f in ("sildoor.tex", "silstep.tex", "silstepno.tex"):
        secs, errs, log = build(f)
        check(f"{f[:-4]}: compiles clean", errs == 0, f"{errs} errors")

    hp = probe_px("sildoor.pdf")
    if hp:
        # Shut on frame 0, then 8 units of ceiling a frame: the sprite has to
        # come into view from the bottom up.  The bug drew it whole from the
        # first frame the opening existed at all, so both halves matter --
        # monotone alone would pass a step function.
        check("a rising door reveals a sprite gradually",
              hp[0] == 0 and 0 < hp[1] < max(hp) and hp == sorted(hp),
              f"{hp}")

    step, step_ctl = probe_px("silstep.pdf"), probe_px("silstepno.pdf")
    check("a raised floor hides a sprite behind it",
          step == [0] and step_ctl and step_ctl[0] > 0,
          f"{step[0] if step else '?'} px, "
          f"{step_ctl[0] if step_ctl else '?'} px without the clip")

    print("doors (M5)")
    secs, errs, log = build("dooranim.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    fr = frames("dooranim.pdf")
    hp = [sum(1 for c in f for b in c if b // 16 == 5) for f in fr]
    check("a shut door hides what is behind it", hp and hp[0] == 0,
          f"{hp[0]} px on the first frame" if hp else "no frames")
    check("the ceiling animates open", hp and hp[-1] > 0 and max(hp) > 100,
          f"pickup pixels {hp[0]} -> {hp[-1]}" if hp else "")
    # The door in the fixture above opens as the player approaches, and by the
    # time they reach it the opening has cleared 56 units -- so passing it is
    # correct.  A door left shut must stop them.
    secs, errs, log = build("doorstuck.tex")
    t = track(log)
    if t:
        xs = [x for x, _, _, _, _ in t]
        check("a shut door blocks the player", max(xs) <= 384 - 16,
              f"stopped at x={max(xs)} (door at 384, radius 16)")

    # ---- M6: the use key, and door surfaces ---------------------------------
    # End to end: \dtUse -> \dt@useseg -> \dt@usenear -> \dt@usedoor ->
    # \dtDoorTick -> the P_LineOpening headroom test.  A shut door pins the
    # walk at x=340; using it must be the only thing that gets the player
    # past x=384 and into rooms 1 and 2.
    print("the use key (M6)")
    secs, errs, log = build("usewalk.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    t = track(log)
    secs, errs, ctl = build("usewalkno.tex")
    tc = track(ctl)
    if t and tc:
        xs = [x for x, _, _, _, _ in t]
        seen = {s for _, _, s, _, _ in t}
        seen_c = {s for _, _, s, _, _ in tc}
        check("using a door opens it and lets the player through",
              max(xs) > 384 and seen == {0, 1, 2},
              f"reached x={max(xs)}, sectors {sorted(seen)}")
        # Without this the control would be vacuous: the door has to be shut
        # in the first place, or "got through" proves nothing.
        check("the same walk without the use key never gets through",
              seen_c == {0} and max(x for x, _, _, _, _ in tc) <= 384 - 16,
              f"sectors {sorted(seen_c)}, "
              f"max x={max(x for x, _, _, _, _ in tc)}")
        # \DTUSE holds the key down.  Without the usedown latch in
        # \dt@usedoor the walk reverses its own door every frame and stalls
        # at 340 exactly like the control, so this is what pins the latch.
        stalled = [i for i in range(1, len(xs)) if xs[i] == xs[i - 1]]
        check("a held use key does not oscillate its own door",
              len(stalled) < len(xs) // 2,
              f"{len(stalled)} of {len(xs)} frames stalled")

    # The reported failure was in \DTPLAY mode on the e key specifically, so
    # drive the key loop rather than the scripted walk.
    keys = "w\n" * 6 + "e\n" + "w\n" * 12 + "q\n"
    secs, errs, log = build("useplay.tex", stdin=keys, interactive=True)
    check("e in the key loop compiles clean", errs == 0, f"{errs} errors")
    prompted = [int(s) for s in
                re.findall(r"sec (-?\d+) floor", log.replace("\n", ""))]
    check("e in the key loop opens the door", 1 in prompted,
          f"sectors at the prompt: {sorted(set(prompted))}")

    # Door surfaces.  A door drawn with the front sector's wall ramp and the
    # brick table is invisible as a door; these pin the selection instead of
    # trusting the picture.
    for f in ("doorface.tex", "doorplain.tex", "doortrack.tex"):
        secs, errs, log = build(f)
        check(f"{f[:-4]}: compiles clean", errs == 0, f"{errs} errors")

    def ramp_px(pdf, row):
        return sum(1 for f in frames(pdf) for c in f for b in c
                   if b // 16 == row)

    face, face_ctl = ramp_px("doorface.pdf", 13), ramp_px("doorplain.pdf", 13)
    check("a shut door draws on the door ramp, not the wall's",
          face > 100 and face_ctl == 0,
          f"{face} px on the door, {face_ctl} px in the control")
    track_px = ramp_px("doortrack.pdf", 1)
    check("a door sector's own walls draw as track",
          track_px > 0 and ramp_px("doorface.pdf", 1) == 0,
          f"{track_px} px of track")
    check("the door face is not just a recoloured brick",
          frames("doorface.pdf") != frames("doorplain.pdf"),
          "door and wall spans differ")

    # ---- M13: triggers, tags and the sector machinery -----------------------
    # The use key above proves a door opens when the line in front of the
    # player is pressed.  This proves the other two routes in: a line that
    # fires because it was *crossed*, and a target chosen by tag rather than
    # by the geometry of the line that fired it.
    print("sector specials: walkover triggers and tags (M13)")
    secs, errs, log = build("walkover.tex")
    check("compiles clean", errs == 0, f"{errs} errors")
    t = track(log)
    secs, errs, ctl = build("walkoverno.tex")
    tc = track(ctl)
    if t and tc:
        seen = {s for _, _, s, _, _ in t}
        seen_c = {s for _, _, s, _, _ in tc}
        mx = max(x for x, _, _, _, _ in t)
        mxc = max(x for x, _, _, _, _ in tc)
        check("crossing a line opens the sector its tag names",
              2 in seen and mx > 640,
              f"reached x={mx}, sectors {sorted(seen)}")
        # Without this the check above is vacuous: room 2 has to be sealed in
        # the first place, or "got through" says nothing about the trigger.
        check("the same walk with the special removed never gets through",
              2 not in seen_c and mxc <= 640 - 16,
              f"sectors {sorted(seen_c)}, max x={mxc}")

    print("frame animation (M10)")
    secs, errs, alog = build("animmath.tex")
    check("animmath: compiles clean", errs == 0, f"{errs} errors")
    flat = alog.replace("\n", "")
    seq4 = re.search(r"ANIMAT4 (\w) (\w) (\w) (\w)", flat)
    seq6 = re.search(r"ANIMAT6 (\w) (\w) (\w) (\w)", flat)
    check("the frame picker walks a plain sequence",
          bool(seq4) and seq4.groups() == ("A", "B", "C", "D"),
          " ".join(seq4.groups()) if seq4 else "no line")
    # ABCDCB is a run up and back down, so positions 3,4,5 are D,C,B.  A
    # picker that assumed base+n would give D,E,F here and silently ask for
    # pictures that do not exist.
    check("the frame picker walks a sequence that doubles back",
          bool(seq6) and seq6.groups() == ("A", "D", "C", "B"),
          " ".join(seq6.groups()) if seq6 else "no line")
    idx = {k: v for k, v in re.findall(r"IDX([A-H]) (\d+)", flat)}
    want = {"A": "0", "B": "1", "C": "0", "D": "1", "E": "0",
            "F": "0", "G": "5", "H": "0"}
    check("the animation index is exact", idx == want,
          "8/8" if idx == want
          else f"{sorted(k for k in want if idx.get(k) != want[k])} wrong")
    # Both halves matter.  D differing from A is the per-thing phase, without
    # which every torch in a room flickers in lockstep; F is the truncation --
    # \numexpr would round 5/6 to 1 and show the next frame a tic early.
    check("two things of one type are out of phase",
          idx.get("A") == "0" and idx.get("D") == "1",
          f"thing 0 -> {idx.get('A')}, thing 1 -> {idx.get('D')}")
    check("the modulus truncates rather than rounding",
          idx.get("F") == "0", f"ANIM 5 of 6 tics -> frame {idx.get('F')}")

    print("interactive mode")
    # e is in here deliberately: the crash this file failed to catch was on
    # the use key, and w/d/q alone never reach \dt@usenear.
    secs, errs, log = build("interactive.tex", stdin="w\nd\ne\nq\n",
                            interactive=True)
    check("compiles clean", errs == 0, f"{errs} errors")
    check("ASCII frames reach the terminal", log.count("w/s move") >= 2,
          f"{log.count('w/s move')} prompts")
    # The key loop runs \dtRenderScene in vertical mode, so anything the scene
    # build fails to consume gets typeset instead of assigned -- silently, and
    # with a plausible-looking frame either way.  Compiling clean does not
    # cover it: the leak that prompted this check produced zero errors and one
    # Overfull hbox per column.  The built-in map cannot trigger it on its own
    # (it has no sprites); the IWAD run below is the one that bites.
    check("interactive mode typesets nothing", overfull("interactive.tex") == 0,
          f"{overfull('interactive.tex')} Overfull")

    # ---- M22: the viewport, the pipe, and the tools --------------------------
    # \DTASC decouples the two resolutions: \DTW x \DTH is what the renderer
    # draws and what the PDF keeps, and the terminal gets a box-filtered
    # reduction of it.  Both fixtures render 152x120 and walk the same way, so
    # everything below is about the viewport and nothing is about the walk.
    print("viewport decimation and the raw pipe (M22)")
    keys = "w\nw\nd\nq\n"
    _, errs, boxlog = build("ascbox.tex", stdin=keys, interactive=True)
    _, errs1, _ = build("ascone.tex", stdin=keys, interactive=True)
    check("compiles clean", errs == 0 and errs1 == 0,
          f"{errs} errors at \\DTASC{{2}}, {errs1} at \\DTASC{{1}}")
    check("viewport decimation typesets nothing", overfull("ascbox.tex") == 0,
          f"{overfull('ascbox.tex')} Overfull")
    bx, on = frames("ascbox.pdf"), frames("ascone.pdf")
    # THE feature, in one line: a smaller viewport must not make a smaller
    # picture.  Without this the obvious implementation -- render small, blow
    # the page up -- passes every other check here, because every other check
    # is about what the terminal shows.
    check("a decimated viewport leaves the render alone",
          bool(bx) and bool(on) and len(bx[0]) == 152 and len(bx[0][0]) == 120
          and bx == on,
          f"{len(bx[0]) if bx else 0}x{len(bx[0][0]) if bx else 0} at "
          f"\\DTASC{{2}}, identical to \\DTASC{{1}}: {bx == on}")
    # 152/2 wide and 120/(2*2) tall, and EVERY row of every frame: a count
    # short by one a frame is what the prompt gluing itself to the first row
    # looks like, and it reads as a working viewport.
    got = asciirows(boxlog, 76)
    check("...and the viewport is \\DTW/n by \\DTH/2n, every frame",
          len(got) == 30 * len(bx) and not asciirows(boxlog, 152),
          f"{len(got)} rows of 76 over {len(bx)} frames, want {30 * len(bx)}")

    pal = pdfpal("ascbox.pdf")
    if bx and pal and got:
        # An independent implementation of the filter, from the PDF's own
        # pixels and the PDF's own palette.  This is the composite check's
        # argument applied to the viewport: everything else here judges the
        # filter by whether the picture looks like a room, and a filter that
        # is wrong in any of the ways it can be wrong still does.
        want = viewport(bx[0], pal, 2)
        check("a viewport cell is the mean of the block behind it",
              got[:len(want)] == want,
              f"{sum(1 for a, b in zip(got, want) if a != b)} of {len(want)} "
              f"rows differ from an independently filtered frame")
        # ...and the control, or the check above is satisfied by a filter that
        # never averages anything.  A point sample is the implementation this
        # replaced, and on a real frame it disagrees somewhere.
        pt = viewport(bx[0], pal, 2, point=True)
        check("and a mean, not a sample of one pixel of it", want != pt,
              f"{sum(1 for a, b in zip(want, pt) if a != b)} of {len(want)} "
              f"rows would differ under a point sample")

    # The pipe's whole claim is that a front-end sees the true frame.
    _, errs, plog = build("ascpipe.tex", stdin=keys, interactive=True)
    check("pipe mode compiles clean", errs == 0, f"{errs} errors")
    pf = pipeframes(plog)
    pp = frames("ascpipe.pdf")
    check("the pipe hands over exactly what the PDF gets",
          bool(pf) and pf == pp[:len(pf)],
          f"{len(pf)} blocks, {len(pp)} pages, identical: {pf == pp[:len(pf)]}")
    # The state line is longer than max_print_line and pdfTeX wraps it
    # mid-token.  Whatever comes last is what a reader taking the lines one
    # at a time loses -- silently, reporting a state that parses perfectly.
    # That was `kill' and `of' until M23 put the secrets, the invulnerability
    # timer and `ex' after them, so the test is on the line's actual END
    # rather than on a field name that has since moved to the middle.
    st = pipestates(plog)
    whole = [s for s in st if re.search(r" kill \d+ of \d+ .* ex \d+$", s)]
    check("and a wrapped state line is reassembled whole",
          bool(st) and len(whole) == len(st),
          f"{len(st)} states, {len(whole)} of them complete through the "
          f"last field")

    # ---- M22: the tools ------------------------------------------------------
    print("gif.py and play.py")
    # play.py end to end: record a key stream, replay it, and prove the run
    # came out the same.  This is the only check here that runs the pipe
    # through the front-end that exists to consume it, and the only one that
    # says the engine is deterministic frame for frame rather than merely
    # repeatable in aggregate.  The last two keys do not move the player,
    # which is also what gives the GIF check below a pair of equal frames.
    r = sh(f"{PY} play.py --record chk.dtd --w 76 --h 60 "
           f"--job chkrec", stdin="w\nw\nd\ne\ne\nq\n")
    # First line, not the last 70 characters: tail-slicing this cut mid
    # sentence and reported a bare "TeX error(s)" with no count in front of
    # it, which reads like a failure on a check that passed.
    check("play.py records a demo", r.returncode == 0 and
          os.path.exists("chk.dtd"), firstline(r))
    r = sh(f"{PY} play.py --replay chk.dtd --verify --delay 0 "
           f"--job chkrep")
    check("and replaying it reproduces the run frame for frame",
          r.returncode == 0 and "matched the recording" in r.stdout,
          r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output")
    # The control: the same keys in a different world must NOT match, or
    # "matched" is a statement about the harness rather than the engine.
    # It has to be the world and not the screen -- a 40-wide render walks
    # exactly the same path, because \SCRW reaches \PPLANE and nothing else,
    # so a demo replayed at another resolution matches and proves nothing.
    # Moving the player start moves every state line from frame zero.
    if os.path.exists("chk.dtd"):
        body = open("chk.dtd").read().replace(
            "# ---- ", "# define DTX=200\n# ---- ")
        open("chkbad.dtd", "w").write(body)
        r = sh(f"{PY} play.py --replay chkbad.dtd --verify "
               f"--delay 0 --job chkbad")
        check("...and the same keys from a different start do not",
              "DIVERGED" in r.stdout,
              "starting 100 units east diverges at once"
              if "DIVERGED" in r.stdout else r.stdout.strip()[-70:])

    # The palette goes out once, before the first tick, so whatever the
    # engine SAYS during that tick arrives between it and the first frame --
    # a secret found where the level starts is enough.  play.py used to take
    # every hex-looking fragment of that as more palette, and three stray
    # digits made an odd-length string that bytes.fromhex refused: the
    # front-end died before drawing anything.  The hook here says `abc' on
    # every tick, which is three hex digits and nothing else.
    r = sh(f'{PY} play.py --record chkmsg.dtd --w 76 --h 60 --job chkmsg '
           f'--define "DTHOOK=\\let\\ptwas\\dtPlayerTick'
           f'\\def\\dtPlayerTick{{\\ptwas\\message{{abc}}}}"',
           stdin="w\nw\nq\n")
    check("play.py is not confused by the engine talking before the first "
          "frame", r.returncode == 0 and "3 frames" in r.stdout,
          firstline(r))

    # A WAD path reaches the engine as TeX source, so it has to survive being
    # read as one.  `.\test.wad' is what shell completion on Windows hands
    # over, and it used to go into the fixture verbatim and make \test an
    # undefined control sequence -- whose error prompt then deadlocked
    # against the very stdin play.py was holding, so loading a WAD cleared
    # the screen and never came back.  Imported rather than shelled out to
    # because it is a pure function: no compile, and cheap insurance on a
    # failure whose only symptom is silence.
    if os.path.exists("test.wad"):
        import play
        norm = play.wadpath("." + os.sep + "test.wad", os.getcwd())
        check("play.py rewrites a Windows WAD path into one TeX can read",
              norm == "test.wad", f"-> {norm}")
    r = sh(f"{PY} play.py --wad nosuch.wad")
    check("...and says so at once when the WAD is not there",
          r.returncode != 0 and "no such WAD" in r.stdout + r.stderr,
          firstline(r))

    # gif.py's claim is that it invents nothing: an index goes in and the same
    # index comes out.  So the check is a decode, against a decoder written
    # from the other end of the spec -- see ungif().  Both encodings have to
    # be decoded, and they are two different code paths: a walk is all whole
    # frames, and a run that comes to a stop is what puts the transparency
    # path under test at all.
    for pdf, scale, tag in (("ascbox.pdf", 2, "a walk"),
                            ("chkrec.pdf", 3, "a run that stops")):
        if not os.path.exists(pdf):
            continue
        src = frames(pdf)
        r = sh(f"{PY} gif.py {pdf} gifchk.gif --scale {scale}")
        try:
            gw, gh, got = ungif("gifchk.gif")
        except Exception as e:                              # noqa: BLE001
            got, gw, gh = [], 0, 0
            print(f"       (decode failed: {e})")
        want = [scaled_rows(f, scale) for f in src]
        size = (len(src[0]) * scale, len(src[0][0]) * scale) if src else (0, 0)
        check(f"a GIF decodes back to the render it came from ({tag})",
              r.returncode == 0 and got == want and (gw, gh) == size,
              f"{len(got)} frames at {gw}x{gh}, want {len(want)} at "
              f"{size[0]}x{size[1]}, "
              f"{sum(1 for a, b in zip(got, want) if a != b)} differ")
        held = re.search(r"(\d+) of \d+ frames held", r.stdout)
        if tag != "a walk":
            # Without this the exactness check above only ever sees whole
            # frames and the held-frame encoding is decoded by nobody.
            check("and the held-frame encoding is one of the two decoded",
                  bool(held) and int(held.group(1)) > 0,
                  r.stdout.strip().split(", ", 3)[-1] if r.stdout
                  else "no output")

    # ---- M14: pickups and the player's state ---------------------------------
    # The first thing in this project that changes the map permanently, and
    # the reason the status bar has anything to say.
    print("pickups and player state (M14)")
    _, errs, glog = build("giveunit.tex")
    check("give rules: compiles clean", errs == 0, f"{errs} errors")
    flatg = glog.replace("\r", "").replace("\n", "")
    rows = {m[0]: m[1:] for m in re.findall(
        r"GIVE (\S+) hp=(-?\d+) ar=(-?\d+)/(-?\d+) b=(-?\d+) s=(-?\d+) "
        r"wp=(-?\d+) max=(-?\d+) got=(-?\d+)", flatg)}

    def g(label, field):
        return rows[label][("hp ar cl b s wp max got").split().index(field)] \
            if label in rows else None

    # P_GiveBody returns false at MAXHEALTH, which is what stops a medikit
    # being spent on a player who cannot use it.  Both halves: refused at
    # 100, taken at 50, and the refusal must leave \dtGOT clear or the thing
    # would vanish having healed nothing.
    check("a medikit is refused at full health",
          g("MEDIFULL", "got") == "0" and g("MEDIFULL", "hp") == "100",
          f"health {g('MEDIFULL', 'hp')}, gave {g('MEDIFULL', 'got')}")
    check("and taken when there is room for it",
          g("MEDIHURT", "got") == "1" and g("MEDIHURT", "hp") == "75",
          f"50 -> {g('MEDIHURT', 'hp')}")
    # A found weapon is two clips, not one; a dropped one would be one, and
    # nothing here drops weapons yet.
    check("a found weapon brings two clips and comes up in your hands",
          g("SHOTGUN", "s") == "8" and g("SHOTGUN", "wp") == "2"
          and g("SHOTGUN", "got") == "1",
          f"{g('SHOTGUN', 's')} shells, weapon {g('SHOTGUN', 'wp')}")
    # ...and a second one is worth its ammunition and nothing else.  The
    # third row switches away first, so "did not come up" is visible: without
    # the weaponowned test the pickup would drag the shotgun back into your
    # hands every time you walked over it.
    check("a second copy gives ammunition but does not re-arm you",
          g("SHOTGUNTWO", "s") == "16" and g("SHOTGUNTHREE", "s") == "24"
          and g("SHOTGUNTHREE", "wp") == "1",
          f"shells {g('SHOTGUNTWO', 's')} -> {g('SHOTGUNTHREE', 's')}, "
          f"weapon stayed {g('SHOTGUNTHREE', 'wp')}")
    check("armour of a class you already have is refused",
          g("GREEN", "ar") == "100" and g("GREEN", "cl") == "1"
          and g("GREENTWO", "got") == "0"
          and g("BLUE", "ar") == "200" and g("BLUE", "cl") == "2",
          f"green {g('GREEN', 'ar')}/{g('GREEN', 'cl')}, "
          f"a second gave {g('GREENTWO', 'got')}, "
          f"blue {g('BLUE', 'ar')}/{g('BLUE', 'cl')}")
    # The backpack doubles the maxima ONCE.  Doubling per pickup is the
    # obvious bug and it is invisible on any map with one backpack.
    check("a backpack doubles the maxima once and only once",
          g("PACK", "max") == "400" and g("PACKTWO", "max") == "400"
          and g("PACK", "got") == "1",
          f"200 -> {g('PACK', 'max')} -> {g('PACKTWO', 'max')}")

    _, errs, flog = build("faceunit.tex")
    check("face picker: compiles clean", errs == 0, f"{errs} errors")
    # Keyed on (health, clock) rather than on health alone: 100 is asked four
    # times over, once per glance, and a dict keyed on health alone would
    # quietly keep whichever answer came last.
    faces = {(m[0], m[1]): m[2] for m in re.findall(
        r"FACE (-?\d+) (-?\d+) (\S+)",
        flog.replace("\r", "").replace("\n", ""))}
    want = {"100": "STFST00", "99": "STFST00", "80": "STFST00",
            "79": "STFST10", "50": "STFST20", "20": "STFST30",
            "1": "STFST40", "0": "STFDEAD0", "107": "STFST00"}
    bad = {h: (faces.get((h, "0")), w) for h, w in want.items()
           if faces.get((h, "0")) != w}
    check("the face tracks the pain level", not bad,
          "100/99/80 unhurt, 79 hurt, 1 nearly dead, 0 a corpse"
          if not bad else f"{bad}")
    # ST_calcPainOffset divides by 101 and truncates.  At exactly 80 health
    # (100-80)*5 is 100, which \numexpr would round to 1 -- so a rounding
    # division wounds the face one point early, on one health value in five.
    # This is the \divide rule from INTERNALS section 6 with a witness.
    check("the pain division truncates rather than rounding",
          faces.get(("80", "0")) == "STFST00"
          and faces.get(("79", "0")) == "STFST10",
          f"80 -> {faces.get(('80', '0'))}, 79 -> {faces.get(('79', '0'))}")
    check("the face glances around on Doom's own clock",
          [faces.get(("100", t)) for t in ("0", "17", "34", "51")]
          == ["STFST00", "STFST01", "STFST02", "STFST00"],
          "0/17/34/51 -> ahead, left, right, and back")

    # ---- pickups on the scripted walk ---------------------------------------
    # The end-to-end path: an ordinary scripted walk, collecting through
    # \dtStepFwd with nothing driven by hand.  The render happens before the
    # step, so the walk reaches the stimpack on the fifth step and health
    # changes between the fourth and fifth log lines.
    _, errs, wlogp = build("pickwalk.tex")
    check("pickup walk: compiles clean", errs == 0, f"{errs} errors")
    _, _, wlogn = build("pickwalkno.tex")
    hp, hn = hud(wlogp), hud(wlogn)
    if hp and hn:
        check("walking over a stimpack raises health",
              [h for h, _, _, _, _ in hp] == [50] * 4 + [60] * 4
              and [t for _, _, _, _, t in hp] == [0] * 4 + [1] * 4,
              f"health {[h for h, _, _, _, _ in hp]}")
        # The control: with the scan stubbed the identical walk collects
        # nothing, so the change above is the scan and not the walking.
        check("with the touch scan stubbed the same walk collects nothing",
              [h for h, _, _, _, _ in hn] == [50] * 8
              and [t for _, _, _, _, t in hn] == [0] * 8,
              f"health {hn[0][0]} throughout, {hn[-1][4]} taken")

    errsum = 0
    logs = {}
    for nm in ("pickstand", "pickstandno", "pickfull", "pickhurt",
               "pickbonus", "pickledge"):
        _, e, lg = build(nm + ".tex")
        errsum += e
        logs[nm] = lg
    check("standing pickup fixtures compile clean", errsum == 0,
          f"{errsum} errors")
    H = {n: hud(logs[n]) for n in logs}
    # 208 is the ramp the fixtures force the billboard onto, and one the
    # built-in map never uses, so every pixel of it is the thing itself.
    def px(name):
        return [ramp(f, 13) for f in frames(name + ".pdf")]

    if H["pickstand"] and H["pickstandno"]:
        a, b = px("pickstand"), px("pickstandno")
        check("a thing that is taken stops being drawn",
              all(v == 0 for v in a) and all(v > 0 for v in b),
              f"{sum(a)} px where it stood, {b[0]} px with the scan stubbed")
        check("and the player is the better for it",
              all(h == 60 for h, _, _, _, _ in H["pickstand"])
              and all(h == 50 for h, _, _, _, _ in H["pickstandno"]),
              f"health 60 against the control's 50")
    if H["pickfull"] and H["pickhurt"]:
        f = px("pickfull")
        check("a medikit is refused at full health and left standing",
              all(h == 100 for h, _, _, _, _ in H["pickfull"])
              and all(t == 0 for _, _, _, _, t in H["pickfull"])
              and all(v > 0 for v in f),
              f"health 100 throughout, {f[0]} px still standing")
        check("and the same medikit is taken by a wounded player",
              all(h == 75 for h, _, _, _, _ in H["pickhurt"])
              and all(v == 0 for v in px("pickhurt")),
              f"50 -> {H['pickhurt'][0][0]}, "
              f"{sum(px('pickhurt'))} px left where it stood")
    if H["pickbonus"]:
        # The other half of the table: a bonus is always taken, and it is the
        # only thing here that can carry a player past MAXHEALTH.
        check("a health bonus is taken at full health and goes past 100",
              all(h == 101 for h, _, _, _, _ in H["pickbonus"])
              and H["pickbonus"][0][4] == 1,
              f"100 -> {H['pickbonus'][0][0]}")
    if H["pickledge"]:
        # Same thing, same spot, 60 units up: Doom compares the two objects'
        # height extents as well as their footprints, and without that a
        # stimpack on a ledge is collectable from the floor below it.
        check("a thing out of reach overhead is not collected",
              all(h == 50 for h, _, _, _, _ in H["pickledge"])
              and all(t == 0 for _, _, _, _, t in H["pickledge"]),
              f"health {H['pickledge'][0][0]}, {H['pickledge'][0][4]} taken")

    # ---- M16: ceilings, stairs, lights and the exit ------------------------
    # All on the built-in map, and all through \dtAddLineSpec + \dtFindDoors
    # from \DTHOOK -- the route usewalk.tex already uses to install a door.
    # Its three rooms are sectors 0/1/2 at floors 0/24/-16, ceilings
    # 128/96/144 and lights 224/176/128, and linedef 3 is the portal between
    # rooms 0 and 1, which the scripted walk crosses on its way east.
    print("ceilings, stairs, lights and the exit (M16)")

    def secfix(name, hook, frames=24, extra=""):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n" + extra
            + f"\\def\\DTFRAMES{{{frames}}}\\def\\DTW{{24}}\\def\\DTH{{16}}\n"
            "\\def\\DTHOOK{" + hook + "\n"
            "\\let\\dt@ptwas\\dtPlayerTick\n"
            "\\def\\dtPlayerTick{\\dt@ptwas\\dt@secdump}%\n"
            "\\def\\dt@secdump{\\message{^^JSEC"
            " c1=\\csname dtSc1\\endcsname\\space"
            " f0=\\csname dtSf0\\endcsname\\space"
            " f1=\\csname dtSf1\\endcsname\\space"
            " f2=\\csname dtSf2\\endcsname\\space"
            " l1=\\csname dtSl1\\endcsname\\space"
            " l2=\\csname dtSl2\\endcsname}}%\n"
            "}\n\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        rows = [tuple(int(v) for v in m) for m in re.findall(
            r"SEC c1=(-?\d+) f0=(-?\d+) f1=(-?\d+) f2=(-?\d+) l1=(-?\d+) "
            r"l2=(-?\d+)", lg.replace("\r", "").replace("\n", ""))]
        return e, lg, rows

    # A crusher on room 1, fired by walking over the portal into it.  Room 1's
    # ceiling is 96 and its floor 24, so it must fall to 32 -- Doom's floor
    # plus eight -- and then climb back to 96 and do it again.
    e1, clog, C = secfix(
        "crush", "\\dtAddSectorTag{1}{7}\\dtAddLineTag{3}{7}"
        "\\dtAddLineSpec{3}{6}\\dtFindDoors", frames=30)
    e2, nlog, N = secfix("crushno", "\\relax", frames=30)
    ceil = [r[0] for r in C]
    check("crusher fixtures compile clean", e1 == 0 and e2 == 0,
          f"{e1 + e2} errors")
    check("a crusher comes down to the floor and eight units",
          ceil and min(ceil) == 32,
          f"ceiling {ceil[0]} -> {min(ceil)} (floor 24 + 8)" if ceil else "")
    # ...and back, and down again.  One descent is a ceiling-lower; what makes
    # it a CRUSHER is that it does it twice, so the check is on the turns.
    turns = sum(1 for a, b, c in zip(ceil, ceil[1:], ceil[2:])
                if (b - a) * (c - b) < 0)
    check("and goes back up, and comes down again", turns >= 2,
          f"{turns} changes of direction in {len(ceil)} frames")
    check("with the special removed the ceiling never moves",
          set(r[0] for r in N) == {96},
          f"control ceilings {sorted(set(r[0] for r in N))}")
    # It hurts.  \dt@crushride charges the player only in the sector doing
    # the crushing, so the control here is the same walk under a ceiling that
    # is not moving -- not a different route.
    hp, hpn = [h for h, _, _, _, _ in hud(clog)], \
              [h for h, _, _, _, _ in hud(nlog)]
    drops = [a - b for a, b in zip(hp, hp[1:]) if a != b]
    check("a closing ceiling crushes the player under it",
          bool(drops) and all(d == 10 for d in drops)
          and all(h == 100 for h in hpn),
          f"health {hp[0]} -> {min(hp)} in {len(drops)} crushes of 10, "
          f"control stayed {hpn[0] if hpn else '?'}")
    # Special 57 stops one.  The control is the crusher above, which does not
    # stop -- so this is the pair, not a reading of one run.
    e3, slog, S = secfix(
        "crushstop", "\\dtAddSectorTag{1}{7}\\dtAddLineTag{3}{7}"
        "\\dtAddLineSpec{3}{6}\\dtAddLineTag{10}{7}\\dtAddLineSpec{10}{57}"
        "\\dtFindDoors", frames=30)
    sceil = [r[0] for r in S]
    # linedef 10 is the portal from room 1 into room 2, so the walk starts
    # the crusher on the way in and stops it on the way out.
    check("and a stop line halts it",
          e3 == 0 and len(set(sceil[-6:])) == 1 and len(set(ceil[-6:])) > 1,
          f"stopped at {sceil[-1] if sceil else '?'} and stayed; "
          f"the crusher was still moving through {sorted(set(ceil[-6:]))}")

    # Stairs out of room 0.  Doom accumulates the step height along the
    # chain, so the three rooms end at 8, 16 and 24 rather than all at 8.
    e4, stlog, T = secfix(
        "stairs", "\\dtAddSectorTag{0}{9}\\dtAddLineTag{3}{9}"
        "\\dtAddLineSpec{3}{8}\\dtFindDoors"
        "\\def\\dtStepFwd##1{}\\dtFireLine{3}", frames=24)
    e5, stnlog, TN = secfix("stairsno", "\\def\\dtStepFwd##1{}", frames=24)
    check("stair fixtures compile clean", e4 == 0 and e5 == 0,
          f"{e4 + e5} errors")
    check("a staircase raises each step above the one before",
          T and T[-1][1:4] == (8, 16, 24),
          f"floors {T[-1][1:4] if T else '?'} from 0/24/-16 -- 8 a step, "
          f"accumulated along the chain")
    check("with no trigger the floors stay where the map put them",
          TN and TN[-1][1:4] == (0, 24, -16),
          f"control floors {TN[-1][1:4] if TN else '?'}")
    # Turbo stairs step sixteen, which is the only thing fun 2 changes.
    e6, tblog, TB = secfix(
        "stairsturbo", "\\dtAddSectorTag{0}{9}\\dtAddLineTag{3}{9}"
        "\\dtAddLineSpec{3}{100}\\dtFindDoors"
        "\\def\\dtStepFwd##1{}\\dtFireLine{3}", frames=24)
    check("and a turbo staircase steps sixteen",
          e6 == 0 and TB and TB[-1][1:4] == (16, 32, 48),
          f"floors {TB[-1][1:4] if TB else '?'}")

    # Lights are assignments, so one frame settles them.  Room 2 to full,
    # and room 1 to its brightest neighbour -- which is room 0's 224, read
    # off the map rather than written down here.
    e7, l1log, L1 = secfix(
        "light255", "\\dtAddSectorTag{2}{4}\\dtAddLineTag{3}{4}"
        "\\dtAddLineSpec{3}{13}\\dtFindDoors"
        "\\def\\dtStepFwd##1{}\\dtFireLine{3}", frames=2)
    e8, l2log, L2 = secfix(
        "lightnear", "\\dtAddSectorTag{1}{4}\\dtAddLineTag{3}{4}"
        "\\dtAddLineSpec{3}{12}\\dtFindDoors"
        "\\def\\dtStepFwd##1{}\\dtFireLine{3}", frames=2)
    check("light fixtures compile clean", e7 == 0 and e8 == 0,
          f"{e7 + e8} errors")
    check("a light special sets the level it names",
          L1 and L1[-1][5] == 255 and L1[-1][4] == 176,
          f"room 2 128 -> {L1[-1][5] if L1 else '?'}, room 1 left at "
          f"{L1[-1][4] if L1 else '?'}")
    check("and 'to the brightest neighbour' reads the neighbour",
          L2 and L2[-1][4] == 224,
          f"room 1 176 -> {L2[-1][4] if L2 else '?'}, which is room 0's own "
          f"224 rather than a number written into the engine")

    # The exit ends the run, and the control is the same walk without it.
    e9, xlog, _ = secfix("exit", "\\dtAddLineSpec{3}{52}\\dtFindDoors",
                         frames=24)
    e10, xnlog, _ = secfix("exitno", "\\relax", frames=24)
    nx, nxn = len(track(xlog)), len(track(xnlog))
    got = re.search(r"exit reached on frame (\d+)",
                    xlog.replace("\r", "").replace("\n", ""))
    check("crossing the exit line ends the level",
          e9 == 0 and e10 == 0 and nx < nxn and bool(got),
          f"{nx} frames rendered against the control's {nxn}; "
          f"exit on frame {got.group(1) if got else '-'}")

    # ---- M18: projectiles ---------------------------------------------------
    # On the built-in map, for M15's reason and one more of its own: a
    # missile's whole life is geometry -- it flies until something stops it --
    # and three rooms whose floors and ceilings this file names are the only
    # place a check can say WHICH thing stopped it.  Rooms 0/1/2 are at floors
    # 0/24/-16 and ceilings 128/96/144, and the portal between 0 and 1 is at
    # x=384; the east wall is at 1024 and the player starts at (100,256)
    # facing east.
    print("projectiles (M18)")

    MISDUMP = (
        "\\let\\dt@ptwas\\dtPlayerTick\n"
        "\\def\\dtPlayerTick{\\dt@ptwas\\dt@misdump}%\n"
        "\\def\\dt@misdump{\\message{^^JMIS"
        " n=\\the\\NTHING\\space booms=\\the\\dtBOOMS\\space"
        " s=\\ifnum\\dtMISBASE<0 -1\\else"
        "\\csname dtJs\\the\\dtMISBASE\\endcsname\\fi\\space"
        " x=\\ifnum\\dtMISBASE<0 0\\else"
        "\\csname dtHx\\the\\dtMISBASE\\endcsname\\fi\\space"
        " z=\\ifnum\\dtMISBASE<0 0\\else"
        "\\csname dtHz\\the\\dtMISBASE\\endcsname\\fi\\space"
        " mh=\\ifcsname dtOh0\\endcsname"
        "\\csname dtOh0\\endcsname\\else-1\\fi}}%\n")

    def misfix(name, hook, nf=20, pre="", w=24, h=16):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n" + pre
            + f"\\def\\DTFRAMES{{{nf}}}\\def\\DTW{{{w}}}\\def\\DTH{{{h}}}\n"
            "\\def\\DTHOOK{" + hook + "\n" + MISDUMP + "}\n"
            "\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        rows = [tuple(int(v) for v in m) for m in re.findall(
            r"MIS n=(\d+) booms=(\d+) s=(-?\d+) x=(-?\d+) z=(-?\d+) "
            r"mh=(-?\d+)", lg.replace("\r", "").replace("\n", ""))]
        return e, lg, rows

    # The rocket launcher, owned and loaded, and a player who does not walk --
    # so nothing but the missile can move and nothing but the missile can
    # take health off.
    GUN = ("\\dtWEAP=4 \\expandafter\\gdef\\csname dtUW4\\endcsname{1}%\n"
           "\\expandafter\\gdef\\csname dtUA3\\endcsname{99}%\n")
    STAND = "\\def\\dtStepFwd##1{}%\n"

    e1, flog, FLY = misfix("misfly", GUN + STAND + "\\dtFire", nf=22)
    e2, nlog, NFLY = misfix("misflyno", GUN + STAND + "\\dtFire", nf=22,
                            pre="\\def\\DTNOMIS{1}\n")
    check("projectile fixtures compile clean", e1 == 0 and e2 == 0,
          f"{e1 + e2} errors")
    # Doom's rocket is 20 units a tic and a frame here is two and a half of
    # those, so 50 -- and the step is asserted rather than merely "it moved",
    # because the ratio to the player's own 40 is the whole of what makes a
    # rocket read as fast.
    air = [x for _, _, s, x, _, _ in FLY if s == 0]
    steps = {b - a for a, b in zip(air, air[1:])}
    check("a rocket leaves the player and crosses the room",
          len(air) > 10 and steps == {50},
          f"x {air[0] if air else '?'} -> {air[-1] if air else '?'} in steps "
          f"of {sorted(steps)}")
    # ...and the control is that NOTHING is thrown, not that it went nowhere.
    check("and with \\DTNOMIS nothing is thrown at all",
          bool(NFLY) and all(n == 0 and b == 0 for n, b, _, _, _, _ in NFLY),
          f"{NFLY[-1][0] if NFLY else '?'} things, "
          f"{NFLY[-1][1] if NFLY else '?'} explosions")
    # A missile that left the map would keep counting up for ever; what
    # bounds it is the east wall, and the explosion is how it says so.
    end = FLY[-1] if FLY else None
    check("and stops at the wall rather than leaving the map",
          bool(end) and end[1] == 1 and end[2] == 1 and 900 < end[3] < 1024,
          f"exploded at x={end[3] if end else '?'} (east wall 1024), "
          f"{end[1] if end else '?'} explosions")

    # The one rule a missile has that a walker does not: it is stopped by the
    # opening at the height it arrives at, and it does not climb.  Three
    # spawns differing ONLY in z, across the portal at x=384 into a room
    # whose floor is 24 and whose ceiling is 96.  Any one of these alone
    # would also be satisfied by a missile that never crossed anything.
    def zfix(name, z):
        return misfix(name, STAND
                      + "\\dtSpawnMissile{9005}{120}{256}{%d}"
                        "{1000}{256}{%d}{-1}%%\n" % (z, z), nf=14)

    e3, _, ZMID = zfix("misz40", 40)
    e4, _, ZLOW = zfix("misz10", 10)
    e5, _, ZHIGH = zfix("misz90", 90)
    check("z fixtures compile clean", e3 == 0 and e4 == 0 and e5 == 0,
          f"{e3 + e4 + e5} errors")
    check("a missile flies over a step it is above",
          bool(ZMID) and ZMID[-1][3] > 384,
          f"z=40 reached x={ZMID[-1][3] if ZMID else '?'} past the portal "
          f"at 384 into a room whose floor is 24")
    check("and is stopped by one it is below",
          bool(ZLOW) and ZLOW[-1][3] < 384 and ZLOW[-1][2] == 1,
          f"z=10 stopped at x={ZLOW[-1][3] if ZLOW else '?'}")
    check("and by a ceiling it would not fit under",
          bool(ZHIGH) and ZHIGH[-1][3] < 384 and ZHIGH[-1][2] == 1,
          f"z=90 stopped at x={ZHIGH[-1][3] if ZHIGH else '?'}; the opening "
          f"tops out at 96 and the missile is 8 tall")

    # A rocket into a zombieman: the direct hit kills it and the blast
    # reaches back to the player.  The control is the same shot with
    # \DTNOMIS, where the launcher is inert -- so it separates "the missile
    # did it" from "firing did it".
    MON = "\\dtAddThing{200}{256}{3004}{0}\\dtSpawnMonsters%\n"
    e6, blog, BOOM = misfix("misboom", MON + GUN + STAND + "\\dtFire", nf=8)
    e7, bnlog, _ = misfix("misboomno", MON + GUN + STAND + "\\dtFire", nf=8,
                          pre="\\def\\DTNOMIS{1}\n")
    kb = [int(v) for v in re.findall(
        r"kill (\d+) of", blog.replace("\r", "").replace("\n", ""))]
    kbn = [int(v) for v in re.findall(
        r"kill (\d+) of", bnlog.replace("\r", "").replace("\n", ""))]
    hb = [x for x, _, _, _, _ in hud(blog)]
    hbn = [x for x, _, _, _, _ in hud(bnlog)]
    check("a rocket kills what it hits", e6 == 0 and e7 == 0
          and kb and kb[-1] == 1 and kbn and kbn[-1] == 0,
          f"{kb[-1] if kb else '?'} killed against the control's "
          f"{kbn[-1] if kbn else '?'}")
    check("and its blast reaches back to the player",
          hb and hb[-1] < 100 and hbn and all(v == 100 for v in hbn),
          f"health {hb[0] if hb else '?'} -> {hb[-1] if hb else '?'}, "
          f"control stayed {hbn[-1] if hbn else '?'}")
    # P_RadiusAttack falls off linearly, so the same rocket fired at a
    # monster forty units further away has to cost the player LESS -- and
    # both have to cost something, or this is a check on nothing.
    e8, flog2, _ = misfix(
        "misfar", "\\dtAddThing{240}{256}{3004}{0}\\dtSpawnMonsters%\n"
        + GUN + STAND + "\\dtFire", nf=8)
    hf = [x for x, _, _, _, _ in hud(flog2)]
    near = 100 - (hb[-1] if hb else 100)
    far = 100 - (hf[-1] if hf else 100)
    check("and the blast falls off with distance",
          e8 == 0 and near > far > 0,
          f"{near} points at the near monster against {far} at one forty "
          f"units further")
    # The DIRECT hit, which the two checks above do not watch: a rocket's
    # blast is 128 points and kills a zombieman on its own, so deleting
    # \dtHurtThing from \dt@misboom left every rocket reading green
    # (mutation `missile-does-no-damage').  The plasma bolt carries no splash
    # at all, so what kills the monster here can only be the impact -- and
    # the player standing a hundred units away has to come through untouched,
    # which is the same fixture saying the blast really is absent.
    e15, plog, _ = misfix(
        "misplasma", MON
        + "\\dtWEAP=5 \\expandafter\\gdef\\csname dtUW5\\endcsname{1}%\n"
          "\\expandafter\\gdef\\csname dtUA2\\endcsname{99}%\n"
        + STAND, nf=10, pre="\\def\\DTFIRE{1}\\def\\DTNOWAIT{1}\n")
    kp = [int(v) for v in re.findall(
        r"kill (\d+) of", plog.replace("\r", "").replace("\n", ""))]
    hp2 = [x for x, _, _, _, _ in hud(plog)]
    check("a bolt with no blast still kills what it hits",
          e15 == 0 and kp and kp[-1] == 1 and hp2 and all(v == 100
                                                          for v in hp2),
          f"{kp[-1] if kp else '?'} killed by impact alone, and the player "
          f"a hundred units away stayed on {hp2[-1] if hp2 else '?'}")

    # The monster half.  An imp 460 units away is far outside its own melee
    # range, so under \DTNOMIS -- M15's world -- it can close but never reach.
    IMP = "\\dtAddThing{560}{256}{3001}{24}\\dtSpawnMonsters%\n"
    e9, ilog, IMPR = misfix("misimp", IMP + STAND, nf=40)
    e10, inlog, IMPN = misfix("misimpno", IMP + STAND, nf=40,
                              pre="\\def\\DTNOMIS{1}\n")
    hi = [x for x, _, _, _, _ in hud(ilog)]
    hin = [x for x, _, _, _, _ in hud(inlog)]
    check("imp fixtures compile clean", e9 == 0 and e10 == 0,
          f"{e9 + e10} errors")
    check("an imp throws a fireball and it reaches the player",
          hi and hi[-1] < 100 and IMPR and IMPR[-1][1] > 0,
          f"health {hi[0] if hi else '?'} -> {hi[-1] if hi else '?'}, "
          f"{IMPR[-1][1] if IMPR else '?'} explosions")
    check("and with \\DTNOMIS the same imp cannot reach at all",
          hin and all(v == 100 for v in hin)
          and IMPN and all(b == 0 for _, b, _, _, _, _ in IMPN),
          f"control health {hin[-1] if hin else '?'}, "
          f"{IMPN[-1][1] if IMPN else '?'} explosions -- it closes and "
          f"punches, which is what M15 left")
    # The z half, and it is the one that was wrong.  A fireball is aimed at
    # the player's FEET, so its z falls the whole way; \numexpr rounds, and a
    # z velocity split per substep rather than taken over the frame descends
    # a third too fast and lands in the floor a stride short -- with the
    # player untouched and every other number in this section still green.
    # An explosion against the floor is clamped to it, so a nonzero z at the
    # impact is exactly "it arrived at the player".
    hit = next((r for r in IMPR if r[2] == 1), None)
    zs = [z for _, _, s, _, z, _ in IMPR if s == 0]
    check("and it arrives at the player rather than in the floor short of "
          "them", bool(hit) and hit[4] > 0 and len(set(zs)) > 5,
          f"exploded at z={hit[4] if hit else '?'} after falling through "
          f"{len(set(zs))} heights from {zs[0] if zs else '?'}")
    # ...and it does not go off in the face of the imp that threw it.
    mh = [m for _, _, _, _, _, m in IMPR]
    check("and a missile does not explode on whatever threw it",
          mh and all(v == 60 for v in mh),
          f"the imp kept {mh[-1] if mh else '?'} of its 60 health")

    # Slots are recycled, so sustained fire is bounded.  Ninety frames of it:
    # \NTHING must stop at \dtMAXMIS past the map's own count, and more
    # missiles must have exploded than there are slots -- one without the
    # other is either a leak or a cap that jammed shut.
    # \DTNOWAIT, here and in the two fixtures below it: M21 gave every weapon
    # Doom's refire period, and these three are counting shots per FRAME
    # because that is what \DTFIRE meant when they were written.  What the
    # cooldown itself does is checked in M21's own section.
    e11, rlog, RECY = misfix("misrecy", GUN + STAND, nf=90,
                             pre="\\def\\DTFIRE{1}\\def\\DTNOWAIT{1}\n")
    ns = [n for n, _, _, _, _, _ in RECY]
    bs = [b for _, b, _, _, _, _ in RECY]
    check("missile slots are recycled and bounded",
          e11 == 0 and ns and max(ns) == 24 and bs and bs[-1] > 24,
          f"{max(ns) if ns else '?'} slots for {bs[-1] if bs else '?'} "
          f"explosions over {len(RECY)} frames")
    # Doom spends one rocket a shot and forty cells a BFG shot, and the pair
    # is what says the cost is read per weapon rather than assumed to be one.
    # A hundred cells is deliberately not a multiple of forty: the run has to
    # stop with twenty left, which is the only reading that tells a weapon
    # whose SHOT costs forty from one that merely draws down faster.
    ra = [a for _, _, a, _, _ in hud(rlog)]
    e12, glog, _ = misfix(
        "misbfg", "\\dtWEAP=6 \\expandafter\\gdef\\csname dtUW6\\endcsname{1}%\n"
        "\\expandafter\\gdef\\csname dtUA2\\endcsname{100}%\n" + STAND,
        nf=8, pre="\\def\\DTFIRE{1}\\def\\DTNOWAIT{1}\n")
    ga = [a for _, _, a, _, _ in hud(glog)]
    rd = {a - b for a, b in zip(ra, ra[1:])} - {0}
    gd = {a - b for a, b in zip(ga, ga[1:])} - {0}
    check("a rocket costs one and a BFG shot forty",
          e12 == 0 and rd == {1} and gd == {40},
          f"rockets fell by {sorted(rd)}, cells by {sorted(gd)}")
    check("and a BFG with thirty-nine cells left does not fire",
          bool(ga) and ga[-1] == 20,
          f"100 cells ran to {ga[-1] if ga else '?'} over {len(ga)} frames "
          f"of held trigger, which is two shots and a refusal")

    # Finally, that any of it is DRAWN.  The control is \DTNOMIS on the same
    # fixture, where the player stands still and nothing else on this map
    # moves -- so a pixel that differs can only be the missile.
    e13, _, _ = misfix("misdraw", GUN + STAND + "\\dtFire", nf=6, w=64, h=40)
    e14, _, _ = misfix("misdrawno", GUN + STAND + "\\dtFire", nf=6, w=64,
                       h=40, pre="\\def\\DTNOMIS{1}\n")
    fa, fb = frames("misdraw.pdf"), frames("misdrawno.pdf")
    px = 0
    if fa and fb and len(fa) == len(fb):
        px = max(sum(1 for ca, cb in zip(a, b)
                     for u, v in zip(ca, cb) if u != v)
                 for a, b in zip(fa, fb))
    check("a missile in the air is drawn",
          e13 == 0 and e14 == 0 and len(fa) == len(fb) and px > 0,
          f"{px} px differ from the same walk with nothing thrown, "
          f"over {len(fa)} frames")

    # ---- M20: locked doors, infighting and sound ----------------------------
    # All on the built-in map, and all three want the same thing from it: a
    # room the player cannot reach, or a monster the player cannot see, with
    # exactly one way for that to change.
    print("locked doors, infighting and sound (M20)")

    # A blue door on the portal into room 1.  The shut ceiling is written
    # before the mark, as usewalk.tex does, because that is what records the
    # shut height; special 26 is Doom's DR Blue.
    def lockfix(name, spec=26, give="\\relax", pre="", nf=30):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n" + pre
            + "\\def\\DTUSE{1}\n"
            + f"\\def\\DTFRAMES{{{nf}}}\\def\\DTW{{24}}\\def\\DTH{{16}}\n"
            "\\def\\DTHOOK{%\n"
            "\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
            f"\\dtAddLineSpec{{3}}{{{spec}}}\\dtFindDoors\n" + give
            + "\\let\\dt@ptwas\\dtPlayerTick\n"
            "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JLOCK "
            "n=\\the\\dtLOCKN}}%\n}\n\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        return e, lg

    e1, l1 = lockfix("lockno")
    e2, l2 = lockfix("lockkey", give="\\dtGiveKey{0}")
    e3, l3 = lockfix("lockoff", pre="\\def\\DTNOLOCK{1}\n")
    check("locked door fixtures compile clean", e1 + e2 + e3 == 0,
          f"{e1 + e2 + e3} errors")
    s1 = {s for _, _, s, _, _ in track(l1)}
    s2 = {s for _, _, s, _, _ in track(l2)}
    s3 = {s for _, _, s, _, _ in track(l3)}
    ref = [int(v) for v in re.findall(
        r"LOCK n=(\d+)", l1.replace("\r", "").replace("\n", ""))]
    check("a locked door refuses a player without its key",
          s1 == {0} and bool(ref) and ref[-1] > 0,
          f"stayed in sector(s) {sorted(s1)} through {ref[-1] if ref else 0} "
          f"refusals")
    check("and opens for one who has it", s2 > {0},
          f"the blue key reaches sector(s) {sorted(s2)}")
    # The off switch, and the whole of what it is for: every fixture written
    # before M20 that used one of these six specials meant "a door".
    check("and \\DTNOLOCK opens it regardless", s3 == s2,
          f"sector(s) {sorted(s3)} against the key's {sorted(s2)}")
    # A refusal must not SPEND the line.  Special 32 is D1 -- once for the
    # whole level -- so a refusal that ran \dt@fireact would leave the door
    # dead and the key useless.  The key arrives ten frames in, and the walk
    # has to get through after it and not before.
    e4, l4 = lockfix(
        "lockspent", spec=32,
        give="\\let\\dt@ktwas\\dtPlayerTick\n"
             "\\def\\dtPlayerTick{\\dt@ktwas"
             "\\ifnum\\dtANIM=10 \\dtGiveKey{0}\\fi}%\n", nf=40)
    seq = [s for _, _, s, _, _ in track(l4)]
    early = set(seq[:10])
    check("and a refusal does not spend a once-only line",
          e4 == 0 and early == {0} and set(seq) > {0},
          f"sector 0 for the first ten frames, then {sorted(set(seq))} once "
          f"the key arrived -- so the D1 line was still armed")

    # Infighting.  An imp at 500 throws at the player at 100, and a zombieman
    # at 300 is standing in the way: Doom's P_DamageMobj repoints the
    # victim's target at whatever hurt it, and that is the whole mechanism.
    # \DTNOMIS is the control, and it is a tight one -- same two monsters,
    # same positions, same sight lines, no fireball to start it.
    def fightfix(name, pre=""):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n" + pre
            + "\\def\\DTFRAMES{60}\\def\\DTW{24}\\def\\DTH{16}\n"
            "\\def\\DTHOOK{%\n"
            "\\dtAddThing{300}{256}{3004}{0}%\n"
            "\\dtAddThing{500}{256}{3001}{24}%\n"
            "\\dtSpawnMonsters\n\\def\\dtStepFwd##1{}%\n"
            "\\let\\dt@ptwas\\dtPlayerTick\n"
            "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JFIGHT"
            " zh=\\csname dtOh0\\endcsname\\space"
            " zt=\\csname dtOt0\\endcsname\\space"
            " ih=\\csname dtOh1\\endcsname\\space"
            " it=\\csname dtOt1\\endcsname}}%\n}\n\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        return e, [tuple(int(v) for v in m) for m in re.findall(
            r"FIGHT zh=(-?\d+) zt=(-?\d+) ih=(-?\d+) it=(-?\d+)",
            lg.replace("\r", "").replace("\n", ""))], lg

    e5, F, flog = fightfix("infight")
    e6, FN, fnlog = fightfix("infightno", pre="\\def\\DTNOMIS{1}\n")
    check("infighting fixtures compile clean", e5 + e6 == 0,
          f"{e5 + e6} errors")
    check("a fireball that catches another monster turns it on the thrower",
          bool(F) and F[-1][1] == 1 and F[-1][0] < 20,
          f"the zombieman took {20 - F[-1][0] if F else '?'} off its 20 and "
          f"now targets thing {F[-1][1] if F else '?'} -- the imp")
    check("and the imp fights back", bool(F) and F[-1][2] < 60
          and F[-1][3] == 0,
          f"the imp is down to {F[-1][2] if F else '?'} of 60 and targets "
          f"thing {F[-1][3] if F else '?'}")
    # The control has to be tight in BOTH directions: nothing hurt, and
    # nobody's target moved off the player.
    check("and with nothing thrown neither of them notices the other",
          bool(FN) and FN[-1] == (20, -1, 60, -1),
          f"control ended {FN[-1] if FN else '?'} against "
          f"{F[-1] if F else '?'} -- (zh, zt, ih, it), -1 being the player")
    # PIT_CheckThing's species rule, which nothing above can see because the
    # two monsters in it are of different types -- `missiles-hit-their-own-
    # kind' reddened nothing until this was written.  The same fixture with
    # the zombieman replaced by a second IMP: the fireball must pass straight
    # through it, so neither imp is ever hurt and neither turns on the other.
    open("infightsame.tex", "w").write(
        "\\catcode`\\@=11\n"
        "\\def\\DTFRAMES{60}\\def\\DTW{24}\\def\\DTH{16}\n"
        "\\def\\DTHOOK{%\n"
        "\\dtAddThing{300}{256}{3001}{0}%\n"
        "\\dtAddThing{500}{256}{3001}{24}%\n"
        "\\dtSpawnMonsters\n\\def\\dtStepFwd##1{}%\n"
        "\\let\\dt@ptwas\\dtPlayerTick\n"
        "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JSAME"
        " a=\\csname dtOh0\\endcsname\\space"
        " b=\\csname dtOh1\\endcsname\\space"
        " booms=\\the\\dtBOOMS}}%\n}\n\\input doom.tex\n")
    _, e10, slog = build("infightsame.tex")
    SM = [tuple(int(v) for v in m) for m in re.findall(
        r"SAME a=(-?\d+) b=(-?\d+) booms=(\d+)",
        slog.replace("\r", "").replace("\n", ""))]
    check("a missile passes through a thing of its shooter's own kind",
          e10 == 0 and bool(SM) and SM[-1][2] > 0
          and SM[-1][0] == 60 and SM[-1][1] == 60,
          f"{SM[-1][2] if SM else '?'} fireballs thrown past a second imp "
          f"and both still on {SM[-1][0] if SM else '?'}/"
          f"{SM[-1][1] if SM else '?'} of 60 -- without the rule the first "
          f"one thrown starts a brawl in every room of imps in the game")

    # Sound.  \dtSight is stubbed to fail for every pair, so NOTHING in these
    # three runs can wake by sight and a monster that wakes can only have
    # heard something.  That is a tighter control than any geometry the
    # built-in map can offer: its three rooms are a straight corridor, and
    # sound differs from sight by going round corners it has not got.
    def sndfix(name, fire=True, shut=False):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n"
            + ("\\def\\DTFIRE{1}\n" if fire else "")
            + "\\def\\DTFRAMES{20}\\def\\DTW{24}\\def\\DTH{16}\n"
            "\\def\\DTHOOK{%\n"
            "\\dtAddThing{700}{256}{3004}{-16}\\dtSpawnMonsters\n"
            + ("\\expandafter\\xdef\\csname dtSc1\\endcsname{24}%\n"
               if shut else "")
            + "\\def\\dtSight##1##2##3##4{\\dtSEE=0}%\n"
            "\\def\\dtStepFwd##1{}%\n"
            "\\let\\dt@ptwas\\dtPlayerTick\n"
            "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JSND "
            "st=\\csname dtOs0\\endcsname\\space noise=\\the\\dtNOISE}}%\n"
            "}\n\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        rows = [tuple(int(v) for v in m) for m in re.findall(
            r"SND st=(\d+) noise=(\d+)",
            lg.replace("\r", "").replace("\n", ""))]
        return e, rows

    e7, S1 = sndfix("noise")
    e8, S2 = sndfix("noiseno", fire=False)
    e9, S3 = sndfix("noisedoor", shut=True)
    check("sound fixtures compile clean", e7 + e8 + e9 == 0,
          f"{e7 + e8 + e9} errors")
    check("a gunshot wakes a monster that cannot see the player",
          bool(S1) and S1[-1] == (1, 1),
          f"two rooms away, sight stubbed dead, it ended in state "
          f"{S1[-1][0] if S1 else '?'} after {S1[-1][1] if S1 else '?'} "
          f"noise wakings")
    check("and silence leaves it asleep",
          bool(S2) and S2[-1] == (0, 0),
          f"the same walk without the trigger held ends in state "
          f"{S2[-1][0] if S2 else '?'}")
    # The half that makes it P_RecursiveSound rather than "wake everything":
    # the flood crosses a two-sided line only while it still has a gap.
    check("and a shut door stops it", bool(S3) and S3[-1] == (0, 0),
          f"with room 1's ceiling closed onto its floor the shot reaches "
          f"nobody: state {S3[-1][0] if S3 else '?'}, "
          f"{S3[-1][1] if S3 else '?'} woken")

    # ---- M21: the weapon state machine, spread and z-sight ------------------
    print("weapons and the z half of sight (M21)")

    def gunfix(name, hook="\\relax", nf=40, pre="", stand=True):
        open(name + ".tex", "w").write(
            "\\catcode`\\@=11\n" + pre
            + f"\\def\\DTFRAMES{{{nf}}}\\def\\DTW{{24}}\\def\\DTH{{16}}\n"
            "\\def\\DTHOOK{" + hook + "\n"
            + ("\\def\\dtStepFwd##1{}%\n" if stand else "")
            + "\\let\\dt@ptwas\\dtPlayerTick\n"
            "\\def\\dtPlayerTick{\\dt@ptwas\\message{^^JWSM"
            " shots=\\the\\dtSHOTS\\space hits=\\the\\dtHITS\\space"
            " a=\\ifcsname dtOh0\\endcsname\\csname dtOh0\\endcsname"
            "\\else-1\\fi\\space"
            " b=\\ifcsname dtOh1\\endcsname\\csname dtOh1\\endcsname"
            "\\else-1\\fi}}%\n}\n\\input doom.tex\n")
        _, e, lg = build(name + ".tex")
        return e, [tuple(int(v) for v in m) for m in re.findall(
            r"WSM shots=(\d+) hits=(\d+) a=(-?\d+) b=(-?\d+)",
            lg.replace("\r", "").replace("\n", ""))]

    # A zombieman to shoot at, so the trigger has a reason to be held.
    TGT = "\\dtAddThing{400}{256}{3004}{24}\\dtSpawnMonsters\n"
    SG = ("\\dtWEAP=2 \\expandafter\\gdef\\csname dtUA1\\endcsname{50}%\n")
    e1, W1 = gunfix("wpistol", TGT, pre="\\def\\DTFIRE{1}\n")
    e2, W2 = gunfix("wshot", TGT + SG, pre="\\def\\DTFIRE{1}\n")
    e3, W3 = gunfix("wnowait", TGT,
                    pre="\\def\\DTFIRE{1}\\def\\DTNOWAIT{1}\n")
    check("weapon fixtures compile clean", e1 + e2 + e3 == 0,
          f"{e1 + e2 + e3} errors")
    # Doom's periods are 14 tics for the pistol and 30 for the shotgun, and
    # \dtMISRATE turns those into 6 frames and 12.  Forty frames of held
    # trigger is therefore seven pistol shots and four shotgun shots -- and
    # the point is the RATIO between them, which is what "one shot a frame"
    # could not express at all.
    ps = W1[-1][0] if W1 else 0
    ss = W2[-1][0] if W2 else 0
    ns = W3[-1][0] if W3 else 0
    check("a held trigger fires at the weapon's own rate",
          ps == 7 and ss == 4,
          f"{ps} pistol shots and {ss} shotgun in 40 frames -- Doom's 14 and "
          f"30 tics at this engine's five-halves of a tic to the frame")
    check("and \\DTNOWAIT puts it back to one shot a frame",
          ns == 40,
          f"{ns} shots in 40 frames, which is what \\DTFIRE meant from M15 "
          f"to M20")

    # The spread, and the one reading that says it is real: a shotgun blast
    # lands on TWO different things.  Every pellet used to be handed the one
    # thing the single aim scan picked, so this could not have happened
    # however wide the numbers were -- M15 recorded exactly that as the gap.
    # Both monsters are 16 units off the facing, well inside the 20-unit
    # radius the aim scan accepts, so the nearest-wins rule gives every
    # unspread pellet to the first of them.
    #
    # Their health is raised to 500 apiece, and that is the whole difference
    # between this check and one that passes for the wrong reason. At 20
    # health the first of them DIES part way through the blast, stops being a
    # target, and the remaining pellets go to the second -- so both end up
    # hurt with no spread whatever, and `pellets-share-one-aim' reddened
    # nothing at all. A target that cannot die inside one blast is the only
    # way the second one being hit means what it says.
    TWO = ("\\dtAddThing{500}{240}{3004}{0}\\dtAddThing{500}{272}{3004}{0}%\n"
           "\\dtSpawnMonsters\n"
           "\\expandafter\\gdef\\csname dtOh0\\endcsname{500}%\n"
           "\\expandafter\\gdef\\csname dtOh1\\endcsname{500}%\n")
    e4, S1 = gunfix("wspread", TWO + SG + "\\dtFire\n", nf=1)
    check("spread fixtures compile clean", e4 == 0, f"{e4} errors")
    check("a shotgun blast lands on more than one thing",
          bool(S1) and S1[-1][2] < 500 and S1[-1][3] < 500,
          f"one blast left them on {S1[-1][2] if S1 else '?'} and "
          f"{S1[-1][3] if S1 else '?'} of 500 apiece")
    # ...and Doom's own rule about WHEN it spreads.  This one needs a target
    # the unspread ray CANNOT reach: 29 units off the facing, against an aim
    # scan that accepts 20, so an accurate shot misses it outright and only a
    # scattered one can land.  Placed in front of the aim's reach and read as
    # a miss, because "one thing was hit" cannot tell accurate from spread --
    # a single pellet hits one thing either way, and `no-shot-is-ever-
    # accurate' reddened nothing while the check was written that way.
    # Two targets, 22 units off the facing on EITHER SIDE, against an aim
    # that reaches 20.  An accurate ray misses both by exactly two units and
    # cannot do otherwise; a spread one leans one way or the other and
    # reaches whichever it leaned toward.
    #
    # Both halves of that were learned from the mutation.  One target 29
    # units off saw nothing, because the spread had four degrees to find and
    # this shot's die rolled less than one.  One target 22 units off on the
    # LEFT saw nothing either, because the roll leaned right -- a single
    # deterministic draw has a sign as well as a size, and a check that
    # depends on the sign is a coin toss written down as an assertion.
    OFF = ("\\dtAddThing{500}{234}{3004}{0}\\dtAddThing{500}{278}{3004}{0}%\n"
           "\\dtSpawnMonsters\n"
           "\\expandafter\\gdef\\csname dtOh0\\endcsname{500}%\n"
           "\\expandafter\\gdef\\csname dtOh1\\endcsname{500}%\n")
    e5, S2 = gunfix("wacc", OFF + "\\dtFire\n", nf=1)
    check("and the first shot of a pistol burst is not spread at all",
          e5 == 0 and bool(S2) and S2[-1][1] == 0
          and S2[-1][2] == 500 and S2[-1][3] == 500,
          f"one target 22 units either side of the facing, against an aim "
          f"that reaches 20: {S2[-1][1] if S2 else '?'} hits, and they kept "
          f"{S2[-1][2] if S2 else '?'} and {S2[-1][3] if S2 else '?'} of 500")

    # The z half of P_CheckSight.  Room 1's floor is lifted to 60, so the
    # opening between rooms 0 and 1 runs 60..96 and a sight line from a
    # monster's eye in room 2 (floor -16, so eye 24) to the player's body in
    # room 0 (floor 0, so 28) passes a long way underneath it.  Nothing about
    # the two-dimensional test can see that, which is why M15 left it out and
    # why it needs geometry built on purpose -- MAP01 has none.
    # gunfix's own dump reports health, so the monster's STATE is added on
    # top of it -- "asleep" is the whole reading here and health cannot say
    # it.
    ZF = ("\\expandafter\\xdef\\csname dtSf1\\endcsname{60}%\n"
          "\\dtAddThing{700}{256}{3004}{-16}\\dtSpawnMonsters\n"
          "\\let\\dt@zwas\\dtPlayerTick\n"
          "\\def\\dtPlayerTick{\\dt@zwas\\message{^^JMON0 "
          "s=\\csname dtOs0\\endcsname}}%\n")

    def zstate(name):
        lg = open(name + ".log", encoding="utf-8", errors="replace").read()
        m = re.findall(r"MON0 s=(\d+)", lg.replace("\r", "").replace("\n", ""))
        return int(m[-1]) if m else -1

    e6, _ = gunfix("zsight", ZF, nf=30)
    e7, _ = gunfix("zsightno", ZF, nf=30, pre="\\def\\DTNOZ{1}\n")
    za, zb = zstate("zsight"), zstate("zsightno")
    check("z-sight fixtures compile clean", e6 + e7 == 0,
          f"{e6 + e7} errors")
    check("a monster cannot see over a parapet it should not",
          za == 0 and zb > 0,
          f"asleep in state {za} with the height test on, and awake in "
          f"state {zb} with \\DTNOZ -- the same map, the same monster")

    # ---- M23: the last of the roadmap, on the built-in map --------------------
    m23_checks()

    # ---- a real IWAD, if one happens to be here ------------------------------
    iwad = next((w for w in ("doom2.wad", "doom.wad", "doom1.wad")
                 if os.path.exists(w)), None)
    if iwad:
        mapname = "MAP01" if iwad == "doom2.wad" else "E1M1"
        print(f"real IWAD ({iwad}, {mapname})")
        head = (f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
                "\\def\\DTFRAMES{8}\n")
        open("iwad.tex", "w").write(head + "\\input doom.tex\n")
        # The same walk with the sprite pass switched off.  Two checks below
        # are about how *procedurally shaded* surfaces behave, and once real
        # artwork is in the frame it is no longer true that every pixel came
        # from a ramp base plus a shade -- a sprite pixel is whatever colour
        # the artist used.  Running them against geometry alone keeps them
        # saying what they were written to say.
        # The HUD is real artwork too, so it goes off for the same reason.
        # \DTNOTEX is the third of the same argument, and the one that made
        # this fixture necessary rather than merely tidy: once walls carry
        # composited TEXTURE1 artwork, "every pixel is a ramp base plus a
        # shade" stops being true of the geometry as well.  Keeping the walls
        # procedural here is what lets the ramp and void checks below go on
        # meaning what they were written to mean.  The textured path gets its
        # own checks further down.
        open("iwadwall.tex", "w").write(
            head + "\\def\\DTNOTEX{1}\n"
            "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}}\n"
            "\\input doom.tex\n")
        secs, errs, log = build("iwad.tex")
        check("compiles clean", errs == 0, f"{errs} errors, {secs:.2f}s")
        fr = frames("iwad.pdf")
        # Column length WITH sprites is the strongest check on the resampler:
        # every post splices back exactly the rows its run emitted, or this
        # goes red in aggregate.
        h = len(fr[0][0]) if fr else 0
        bad = [1 for f in fr for c in f if len(c) != h]
        check("IWAD: every column exactly H bytes, sprites on", not bad,
              f"{len(fr[0])}x{h}, {len(bad)} bad" if bad else f"{len(fr[0])}x{h}")

        secs, errs, wlog = build("iwadwall.tex")
        check("geometry alone: compiles clean", errs == 0, f"{errs} errors")
        frw = geometry_checks("IWAD geometry", "iwadwall.pdf")

        t = track(log)
        secs_seen = {s for _, _, s, _, _ in t}
        flrs = sorted({f for _, _, _, f, _ in t})
        # MAP01 starts on a ledge and the scripted walk heads north up a
        # staircase, so a working point-locator sees several distinct sectors
        # at several distinct floor heights.
        check("walks through several sectors", len(secs_seen) >= 3,
              f"{len(secs_seen)} sectors, floors {flrs}")

        # Every procedurally shaded surface must be based on a palette row
        # that really is a ramp in *this WAD's* palette, or distance shading
        # inverts partway down.
        pal = playpal(iwad)
        if pal and frw:
            ok = ramp_rows(pal)
            used = {b // 16 for f in frw for c in f for b in c}
            check("shading stays inside real palette ramps", used <= ok,
                  f"rows used {sorted(used)}, "
                  f"unusable {sorted(used - ok)}" if used - ok
                  else f"{len(used)} rows, all genuine ramps")
        n = re.search(r"\[wad\] (\d+) things placed", log.replace("\n", ""))
        check("THINGS loaded and placed on their sector floors",
              bool(n) and int(n.group(1)) > 0,
              f"{n.group(1)} things" if n else "no things line")

        # M10: does animation reach the screen?  The player is held still, so
        # the geometry, the depth test and the projection all produce the same
        # pixels every frame and the only thing left that can change one is
        # the frame letter.  The control matters as much as the test: a sprite
        # pass that had stopped drawing entirely would also be "identical
        # every frame", so the pair is what pins it.
        #
        # \DTNOMON on BOTH halves, and that is what keeps the pair meaning
        # what it meant: with monsters live, "the world animates" would be
        # satisfied by a zombieman walking about whether the frame clock ran
        # or not, and the frozen control would not be frozen at all.  The
        # subject here is \dtAnimTick, so everything else must hold still.
        stand = ("\\def\\DTHOOK{\\def\\dtStepFwd##1{}%s}\n"
                 "\\def\\DTFRAMES{24}\\def\\DTNOMON{1}\n")
        head24 = f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
        open("animon.tex", "w").write(
            head24 + stand % "" + "\\input doom.tex\n")
        open("animoff.tex", "w").write(
            head24 + stand % "\\def\\dtAnimTick{}" + "\\input doom.tex\n")
        secs, errs, _ = build("animon.tex")
        check("animation: compiles clean", errs == 0, f"{errs} errors")
        secs, errs, _ = build("animoff.tex")
        check("animation control: compiles clean", errs == 0, f"{errs} errors")
        fon, foff = frames("animon.pdf"), frames("animoff.pdf")
        non = len({tuple(f) for f in fon})
        noff = len({tuple(f) for f in foff})
        check("standing still, the world still animates", non > 1,
              f"{non} distinct frames in {len(fon)}")
        check("with the tick stubbed the same walk is frozen", noff == 1,
              f"{noff} distinct frames in {len(foff)}")
        # Animation must not cost the resampler its invariant: every post
        # still splices back exactly the rows its run emitted, on every frame
        # of the cycle rather than just frame A.
        h = len(fon[0][0]) if fon else 0
        bad = [1 for f in fon for c in f if len(c) != h]
        check("every column exactly H bytes, animating", not bad,
              f"{len(bad)} bad" if bad else f"{len(fon[0])}x{h}")

        # doom.tex is supposed to run unmodified under pdflatex and produce
        # byte-identical output.  It silently stopped doing so: LaTeX's
        # register allocator starts where plain TeX's finishes, so a block of
        # \countdef'd scratch registers picked by hand at 300..399 landed on
        # top of \newcount allocations -- \VIEWH and \dtHDA became the same
        # \count352 and the HUD overwrote the view height every frame.  The
        # player walked the same path with the camera sinking through the
        # floor, which no other check here looks at.  Needs a WAD: the banks
        # that collide are the sprite, HUD and COLORMAP ones.
        open("latexcmp.tex", "w").write(
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\\def\\DTFRAMES{{4}}\n"
            "\\input doom.tex\n\\end{document}\n")
        open("plaincmp.tex", "w").write(
            f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\\def\\DTFRAMES{{4}}\n"
            "\\input doom.tex\n")
        t0 = time.time()
        r = sh("pdflatex -interaction=nonstopmode latexcmp.tex")
        lerr = len(re.findall(r"^!", open("latexcmp.log", errors="replace")
                              .read(), re.M))
        secs, perr, _ = build("plaincmp.tex")
        check("pdflatex: compiles clean", lerr == 0, f"{lerr} errors")
        try:
            fl, fp = frames("latexcmp.pdf"), frames("plaincmp.pdf")
            same = fl == fp
            nd = sum(1 for a, b in zip(fl, fp)
                     for ca, cb in zip(a, b) for x, y in zip(ca, cb) if x != y)
            check("pdflatex output is pixel-identical to pdftex",
                  same and perr == 0 and len(fl) == 4,
                  f"{len(fl)} frames identical" if same
                  else f"{nd} px differ")
        except OSError:
            check("pdflatex output is pixel-identical to pdftex", False,
                  "no pdf")

        # Interactive mode with real sprites: the key loop is the only place
        # the scene build runs in vertical mode, and the sprite pass is the
        # only part of it that was ever caught emitting anything.  A macro
        # name collision between \dt@thingrot's rotation digit and play.tex's
        # transpose walker put a whole column into the page per frame here --
        # 0 errors, a frame that still looked right, and nothing else in this
        # file saw it.  Needs a WAD (no sprites, no bug) and needs to be
        # interactive (\dtRenderFrame boxes the scene, so the batch path is
        # immune either way).
        open("iwadplay.tex", "w").write(
            f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
            "\\def\\DTPLAY{1}\n"
            "\\def\\DTW{76}\\def\\DTH{60}\\def\\DTPIX{6}\n"
            "\\input doom.tex\n")
        secs, errs, plog = build("iwadplay.tex", stdin="w\nd\ne\nq\n",
                                 interactive=True)
        check("interactive IWAD: compiles clean", errs == 0, f"{errs} errors")
        check("interactive IWAD: sprite pass typesets nothing",
              overfull("iwadplay.tex") == 0,
              f"{overfull('iwadplay.tex')} Overfull")
        check("interactive IWAD: ASCII frames reach the terminal",
              plog.count("w/s move") >= 2, f"{plog.count('w/s move')} prompts")

        # ---- M11: real wall textures ----------------------------------------
        print("wall textures (M11)")
        flatlog = log.replace("\n", "")
        got = re.search(r"\[wad\] textures: (\d+) composited of (\d+) named",
                        flatlog)
        # Every texture a sidedef names must resolve.  This is the check that
        # catches case: MAP01's sidedefs spell `doortrak' and `support2' in
        # lower case while TEXTURE1 spells both in upper, so a byte-exact
        # name comparison silently loses exactly those two and reports
        # "37 composited of 39".  Nothing else in the render says so -- the
        # two surfaces just fall back to the procedural bricks and look
        # entirely plausible.
        check("every texture a sidedef names is composited",
              bool(got) and got.group(1) == got.group(2) and int(got.group(1)) > 0,
              f"{got.group(1)} of {got.group(2)}" if got else "no textures line")

        # The composite itself, against a second implementation.  Everything
        # else about textures is judged by how the picture looks, and a
        # composite that is wrong in any of the ways it can be wrong -- a
        # patch at the wrong origin, a PNAMES index off by one, a post
        # decoded one byte out -- still renders a perfectly plausible wall.
        # Three textures, chosen for what each one exercises: a single-patch
        # tile, a five-patch composite, and the one texture in MAP01 whose
        # patches leave holes for the hole filler to close.  The spread is
        # the point and was confirmed by breaking the compositor: dropping
        # the patch y origin leaves STONE7 byte-identical, because it is one
        # patch at (0,0) and cannot see that bug at all.  BIGDOOR1 catches it.
        probes = [("STONE7", 7), ("BIGDOOR1", 40), ("BRNSMAL1", 33)]
        open("iwadrow.tex", "w").write(
            head + "\\def\\DTHOOK{" + "".join(
                f"\\dtTexRow{{{nm}}}{{{v}}}" for nm, v in probes)
            + "\\def\\dtStepFwd##1{}}\n\\def\\DTFRAMES{1}\n\\input doom.tex\n")
        _, errs, rowlog = build("iwadrow.tex")
        rowflat = rowlog.replace("\r", "").replace("\n", "")
        for nm, v in probes:
            m = re.search(r"\[texrow\] %s %d (\d+) (\d+) ([0-9A-F]+)"
                          % (nm, v), rowflat)
            ref = composite(iwad, nm)
            if not m or not ref:
                check(f"composite matches an independent one: {nm}", False,
                      "no [texrow] line" if not m else "not in TEXTURE1")
                continue
            w, h = int(m.group(1)), int(m.group(2))
            row = m.group(3)
            rw, rh, tex = ref
            want = "".join("%02X" % tex[(x, v)] for x in range(rw))
            check(f"composite matches an independent one: {nm}",
                  (w, h) == (rw, rh) and row == want,
                  f"{w}x{h} row {v} agrees over {w} columns"
                  if (w, h) == (rw, rh) and row == want
                  else f"{w}x{h} vs {rw}x{rh}, "
                       f"{sum(1 for a, b in zip(row, want) if a != b)} chars differ")

        # The textured geometry, with sprites and HUD off so that every pixel
        # in frame is a wall, a floor or a ceiling.
        # \DTNOFLAT because every check below this line was written about
        # WALLS.  Once floors and ceilings carry real flats they reach palette
        # rows outside the ramps too, and "the textured render reaches rows a
        # ramp cannot" stops being a statement about the wall path -- it would
        # pass with \dt@usetex deleted entirely.  The flats get their own
        # fixture in M12.
        open("iwadtex.tex", "w").write(
            head + "\\def\\DTNOFLAT{1}\n"
            "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}}\n"
            "\\input doom.tex\n")
        secs, errs, _ = build("iwadtex.tex")
        check("textured geometry: compiles clean", errs == 0, f"{errs} errors")
        frt = frames("iwadtex.pdf")
        # The wrap arithmetic is new on this path -- v now wraps at the
        # texture's own height rather than a fixed 64, and vstep is reduced
        # against that same modulus -- and a span that emitted the wrong
        # number of rows would show up here and nowhere else.
        h = len(frt[0][0]) if frt else 0
        bad = [1 for f in frt for c in f if len(c) != h]
        check("every column exactly H bytes, textures on", frt and not bad,
              f"{len(frt[0])}x{h}, {len(bad)} bad" if bad else f"{len(frt[0])}x{h}")

        # Real artwork against the procedural control.  A procedurally shaded
        # wall is a ramp base plus 0..13, so it can only ever land on rows
        # that are genuine ramps; composited artwork is whatever colour the
        # artist used and has no such obligation.  Both halves matter: the
        # control is what proves this is about the walls rather than about
        # the floors, which are still ramps on both paths.  A colour count
        # would NOT do here -- eleven ramps of fourteen shades is already
        # over a hundred colours, so the two paths are within a factor of
        # two of each other on that measure and it separates nothing.
        if frt and frw:
            pal = playpal(iwad)
            ok = ramp_rows(pal) if pal else set(range(16))
            ctex = {b for f in frt for c in f for b in c}
            cpro = {b for f in frw for c in f for b in c}
            rtex, rpro = {b // 16 for b in ctex}, {b // 16 for b in cpro}
            check("walls draw real artwork, not a shade ramp",
                  bool(rtex - ok) and not (rpro - ok),
                  f"textured reaches rows {sorted(rtex - ok)} outside the "
                  f"ramps; procedural stays inside {len(rpro)} ramp rows")
            # ...and it has to reach the screen.  A texture path that
            # composited perfectly and then drew nothing would pass every
            # check above this one.
            check("turning textures off changes the picture",
                  [tuple(f) for f in frt] != [tuple(f) for f in frw],
                  "textured and procedural renders differ")

        # Doom shades a wall by sector light AND distance, through COLORMAP.
        # If the distance term were dropped the whole map would draw at one
        # level per sector, so counting the levels a single texture reaches
        # is a direct read of whether the light table is being indexed.
        if frt:
            shades = {b for f in frt for c in f for b in c}
            check("wall shading spans many colormap levels", len(shades) >= 40,
                  f"{len(shades)} distinct colours in geometry alone")

        # A sidedef's row offset slides its texture vertically.  This one
        # forces the value rather than trusting the map, because MAP01's 38
        # sidedefs that carry a row offset all happen to lie off the scripted
        # walk: deleting the term from \dtSegDraw changes not one pixel of
        # any other fixture here, so without this the whole \dtVOFF term is
        # unguarded and could be removed without a single check going red.
        # Forcing it is also what distinguished "unexercised" from "broken"
        # in the first place -- stubbing it proved nothing either way.
        open("iwadyoff.tex", "w").write(
            head + "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}%\n"
            "\\tmpi=0 \\dtWhile{\\tmpi<\\NSIDE}{%\n"
            "\\expandafter\\xdef\\csname dtYy\\the\\tmpi\\endcsname{32}%\n"
            "\\advance\\tmpi by1\\relax }}\n\\input doom.tex\n")
        _, errs, _ = build("iwadyoff.tex")
        fry = frames("iwadyoff.pdf")
        moved = (frt and fry
                 and [tuple(f) for f in fry] != [tuple(f) for f in frt])
        check("a sidedef row offset slides the texture",
              errs == 0 and moved,
              f"{errs} errors" if errs else
              ("32-unit offset moves the walls" if moved else "no change"))

        # Pegging picks which end of a surface a texture is nailed to, and
        # the default for an upper is the BACK sector's ceiling -- which is
        # what makes a door slab travel up with the ceiling rather than
        # standing still while the gap opens.  Worth pinning: before this
        # milestone the renderer had DONTPEGTOP hardwired on for every wall
        # in every map, and nothing noticed.
        #
        # The flag is CLEARED here rather than set, and the direction is not
        # arbitrary.  Setting it everywhere changes nothing at all, because
        # every upper texture visible on this walk already carries it, and
        # where an upper is zero-height the two formulas differ by exactly
        # the texture height -- which wraps to zero.  Clearing it moves 7640
        # pixels.  A check that fires in only one direction still guards the
        # branch; one written in the other direction guards nothing and
        # looks identical.
        open("iwadpeg.tex", "w").write(
            head + "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}%\n"
            "\\tmpi=0 \\dtWhile{\\tmpi<\\NLINE}{%\n"
            "\\expandafter\\xdef\\csname dtLG\\the\\tmpi\\endcsname{0}%\n"
            "\\advance\\tmpi by1\\relax }}\n\\input doom.tex\n")
        _, errs, _ = build("iwadpeg.tex")
        frp = frames("iwadpeg.pdf")
        pegged = (frt and frp
                  and [tuple(f) for f in frp] != [tuple(f) for f in frt])
        check("the pegging flag chooses where a texture starts",
              errs == 0 and pegged,
              f"{errs} errors" if errs else
              ("clearing DONTPEGTOP moves the uppers" if pegged
               else "no change"))

        # ---- M8: real sprite graphics --------------------------------------
        # ---- M12: real flats and the sky ------------------------------------
        print("real flats and sky (M12)")
        got = re.search(r"\[wad\] flats: (\d+) read of (\d+) named", flatlog)
        check("every flat a sector names is read",
              bool(got) and got.group(1) == got.group(2) and int(got.group(1)) > 0,
              f"{got.group(1)} of {got.group(2)}" if got else "no flats line")

        # The 4096 bytes, against a second implementation.  A flat that is
        # transposed, off by a row, or read from the wrong lump renders a
        # perfectly plausible floor -- this is the only thing that looks at
        # the data rather than at what the data looks like.
        #
        # The probes must be ASYMMETRIC, and that is asserted rather than
        # assumed: dtFL is indexed [row][column] because Doom's R_DrawSpan
        # reads source[(y&63)*64 + (x&63)], and swapping the pair mirrors
        # every floor in the map about its diagonal -- which on a symmetric
        # tile is not visible and would not be caught here either.
        fprobes = [("FLOOR0_1", 0), ("RROCK09", 37)]
        open("iwadfrow.tex", "w").write(
            head + "\\def\\DTHOOK{" + "".join(
                f"\\dtFlatRow{{{nm}}}{{{v}}}" for nm, v in fprobes)
            + "\\def\\dtStepFwd##1{}}\n\\def\\DTFRAMES{1}\n\\input doom.tex\n")
        _, errs, frowlog = build("iwadfrow.tex")
        frowflat = frowlog.replace("\r", "").replace("\n", "")
        for nm, v in fprobes:
            ref = read_flat(iwad, nm)
            m = re.search(r"\[flatrow\] %s %d ((?:\{[0-9A-F]{2}\})+)" % (nm, v),
                          frowflat)
            if not ref or not asymmetric(ref):
                check(f"flat matches an independently read one: {nm}", False,
                      "not in F_START..F_END" if not ref
                      else "probe is symmetric and could not see a transpose")
                continue
            if not m:
                check(f"flat matches an independently read one: {nm}", False,
                      "no [flatrow] line")
                continue
            row = "".join(re.findall(r"\{([0-9A-F]{2})\}", m.group(1)))
            want = "".join("%02X" % ref[64 * v + x] for x in range(64))
            check(f"flat matches an independently read one: {nm}",
                  row == want,
                  f"row {v} agrees over 64 columns" if row == want
                  else f"{sum(1 for a, b in zip(row, want) if a != b)} "
                       "chars differ")

        # Geometry with flats on: walls, floors and ceilings, nothing else.
        open("iwadflat.tex", "w").write(
            head + "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}}\n"
            "\\input doom.tex\n")
        secs, errs, _ = build("iwadflat.tex")
        check("flat geometry: compiles clean", errs == 0, f"{errs} errors")
        frf = frames("iwadflat.pdf")
        geometry_checks("flats", "iwadflat.pdf")

        # A procedural plane is a ramp base plus 0..13, so it can only ever
        # land on a row that really is one of the eleven usable ramps.  Real
        # artwork is whatever colour the artist used and has no such
        # obligation.
        #
        # The walls have to come OFF for this, which is what \dt@txok forced
        # false does -- every wall falls back to the procedural table without
        # touching the flats.  Written against the ordinary geometry fixture
        # it reads the walls instead: their artwork supplies rows outside the
        # ramps all by itself, and the check stays green with the flat path
        # disabled entirely.  Confirmed by doing exactly that.
        fpal = playpal(iwad)
        frn = frames("iwadtex.pdf")              # same walk, planes procedural
        planeonly = ("\\catcode`\\@=11\n"
                     "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}%\n"
                     "\\def\\dt@txok##1{\\dt@realtexfalse}}\n")
        open("iwadplane.tex", "w").write(head + planeonly + "\\input doom.tex\n")
        _, e1, _ = build("iwadplane.tex")
        open("iwadplanec.tex", "w").write(
            head + "\\def\\DTNOFLAT{1}\n" + planeonly + "\\input doom.tex\n")
        _, e2, _ = build("iwadplanec.tex")
        frp, frpc = frames("iwadplane.pdf"), frames("iwadplanec.pdf")
        check("plane fixtures compile clean", e1 + e2 == 0, f"{e1 + e2} errors")
        if frp and frpc and fpal:
            rows = ramp_rows(fpal)
            def used(fs):
                return {b // 16 for f in fs for c in f for b in c}
            outside, ctl = used(frp) - rows, used(frpc) - rows
            check("planes draw real artwork, not a shade ramp",
                  bool(outside) and not ctl,
                  f"flats reach rows {sorted(outside)[:6]} outside the ramps; "
                  f"procedural planes stay inside {len(rows)} ramp rows"
                  if outside and not ctl
                  else f"flats outside={sorted(outside)[:4]} "
                       f"control outside={sorted(ctl)[:4]}")
        if frf and frn:
            differ = [tuple(f) for f in frf] != [tuple(f) for f in frn]
            check("turning flats off changes the picture", differ,
                  "flat and procedural renders differ" if differ
                  else "flats on and flats off are the same picture")

        # Doom shades a plane by sector light AND distance, through the same
        # COLORMAP the walls use -- planezlight rather than scalelight, but
        # the same table.
        #
        # Forced, not counted.  "The plane pass reaches 40+ colours" sounds
        # like a statement about distance shading and is not one: 30 flats
        # across 59 sector light levels reach 105 with the depth term
        # deleted, so that check stayed green with the distance term set to
        # a constant.  Zeroing \dt@plit removes the falloff and nothing else,
        # and the picture has to notice.
        open("iwadflit.tex", "w").write(
            head + "\\catcode`\\@=11\n"
            "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}%\n"
            "\\def\\dt@plit{0}}\n\\input doom.tex\n")
        _, e3, _ = build("iwadflit.tex")
        frfl = frames("iwadflit.pdf")
        lit = (frfl and e3 == 0
               and [tuple(f) for f in frfl] != [tuple(f) for f in frf])
        check("planes shade with distance, not sector light alone", bool(lit),
              "removing the depth falloff moves the planes" if lit
              else "the planes did not notice")

        # Is the flat pinned to the WORLD or to the screen?  Nothing above can
        # tell: a floor sampled at a fixed origin still slides along with the
        # geometry around it and still looks exactly like a floor.
        #
        # So FORCE the term to a camera-independent constant -- the bias
        # alone, i.e. a camera at the origin -- and require the picture to
        # move.  Adding an offset to it instead would only prove the term is
        # read at all: an engine that had dropped \PX entirely would still
        # shift when something was added to it, and would still pass.
        # One fixture per axis, so neither term can go missing behind the
        # other.
        # \catcode`\@=11 or this fixture does not stub what it names: with @
        # at catcode 12 \def\dt@pfwas defines \dt with the parameter text
        # @pfwas.  Here that errors; the dangerous case is the one in
        # INTERNALS section 8, where it compiles and changes nothing.
        BIAS = 268435456
        for axis in ("x", "y"):
            job = f"iwadf{axis}"
            open(job + ".tex", "w").write(
                head + "\\catcode`\\@=11\n"
                "\\def\\DTHOOK{\\def\\dtDrawThings{}\\def\\dtDrawHUD{}%\n"
                "\\let\\dt@pfwas\\dtPlaneFrame\n"
                "\\def\\dtPlaneFrame{\\dt@pfwas\n"
                f"\\def\\dt@p{axis}q{{{BIAS}}}}}}}\n"
                "\\input doom.tex\n")
            _, errs, _ = build(job + ".tex")
            fro = frames(job + ".pdf")
            moved = (fro and errs == 0
                     and [tuple(f) for f in fro] != [tuple(f) for f in frf])
            check(f"the camera {axis} feeds the flat sampling", bool(moved),
                  "pinning the sample origin moves the planes" if moved
                  else "the planes did not notice")

        # ---- the sky --------------------------------------------------------
        # F_SKY1 is a marker, not a flat: ten of MAP01's 59 sectors name it,
        # and drawing its 4096 bytes gives a ceiling of red rock stretching to
        # infinity at the horizon.  Doom draws a strip of a wall texture
        # picked by view angle instead.
        #
        # Which pixels are sky is settled by a control render with \SkyRun
        # stubbed to a marker colour, so the checks below never have to guess.
        sky = composite(iwad, "SKY1")
        SKYMARK = 254

        def sky_run(job, x, y, ang, stub=False):
            body = ("\\def\\dtDrawThings{}\\def\\dtDrawHUD{}"
                    + ("\\def\\SkyRun##1##2{\\Rep{\\numexpr(##2)-(##1)\\relax}"
                       "{\\hx{%d}}}" % SKYMARK if stub else ""))
            open(job + ".tex", "w").write(
                f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
                f"\\def\\DTFRAMES{{1}}\\def\\DTX{{{x}}}\\def\\DTY{{{y}}}"
                f"\\def\\DTANG{{{ang}}}\n"
                "\\catcode`\\@=11\n"
                f"\\def\\DTHOOK{{{body}}}\n\\input doom.tex\n")
            _, e, _ = build(job + ".tex")
            fr = frames(job + ".pdf")
            return (fr[0] if fr else None), e

        # A spot inside MAP01's outdoor sector, where the sky is most of the
        # upper half of the view.
        SX, SY = -644, 781

        def texdiv(a, b):                 # \numexpr rounds, half away from zero
            q, r = abs(a) // abs(b), abs(a) % abs(b)
            if 2 * r >= abs(b):
                q += 1
            return q if (a < 0) == (b < 0) else -q

        base, e0 = sky_run("iwadsky", SX, SY, 2048)
        mask, e1 = sky_run("iwadskym", SX, SY, 2048, stub=True)
        turn, e2 = sky_run("iwadskyt", SX, SY, 2148)
        move, e3 = sky_run("iwadskyw", SX + 48, SY, 2048)
        maskw, e4 = sky_run("iwadskywm", SX + 48, SY, 2048, stub=True)
        maskt, e5 = sky_run("iwadskytm", SX, SY, 2148, stub=True)
        errs = e0 + e1 + e2 + e3 + e4 + e5
        check("sky fixtures compile clean", errs == 0, f"{errs} errors")

        if base and mask and sky:
            skw, skh, stex = sky
            H = len(base[0])
            half = H // 2
            # dtSV<y>: Doom pins texture row 100 to the horizon and steps one
            # texture row per screen row of a 200-row view.
            sv = [min(max(100 + texdiv((y - half) * 200, H), 0), skh)
                  for y in range(H)]
            npix = sum(1 for c in mask for b in c if b == SKYMARK)
            check("the sky is drawn at all", npix > 500, f"{npix} sky pixels")

            # Every sky column must be a vertical strip of SKY1 -- the same
            # texture column at every row, unshaded.  Recovering WHICH column
            # by search rather than recomputing the angle keeps this
            # independent of the engine's arctangent table.
            def strips(frame, msk):
                out = {}
                for x in range(len(frame)):
                    ys = [y for y in range(H) if msk[x][y] == SKYMARK]
                    if len(ys) < 6:
                        continue
                    hit = [u for u in range(skw)
                           if all(stex[(u, sv[y])] == frame[x][y] for y in ys)]
                    out[x] = hit
                return out

            got = strips(base, mask)
            solid = [x for x, h in got.items() if h]
            check("every sky column is a vertical strip of SKY1",
                  got and len(solid) == len(got),
                  f"{len(solid)} of {len(got)} columns matched a SKY1 column")

            # Doom's sky neither scrolls with the player nor darkens with
            # distance: it is the one surface with no perspective at all, and
            # the texture column depends on the facing alone.  So compare the
            # BYTES, on the pixels both renders agree are sky.  Recovering a
            # SKY1 column and comparing that is not the same test: a column of
            # sky can match several columns of the texture, and picking one of
            # them made this fire on 4 columns of 118 with nothing wrong.
            def both(m1, m2):
                return [(x, y) for x in range(len(m1)) for y in range(H)
                        if m1[x][y] == SKYMARK and m2[x][y] == SKYMARK]

            walked = both(mask, maskw)
            kept = sum(1 for x, y in walked if base[x][y] == move[x][y])
            check("walking does not move the sky",
                  walked and kept == len(walked),
                  f"{kept} of {len(walked)} sky pixels unchanged by a 48-unit "
                  "walk")
            if turn and maskt:
                turned = both(mask, maskt)
                diff = sum(1 for x, y in turned if base[x][y] != turn[x][y])
                check("turning moves the sky",
                      turned and diff > 0.5 * len(turned),
                      f"{diff} of {len(turned)} sky pixels changed on a "
                      "100-unit turn")

        print("sprite graphics (M8)")
        flat = log.replace("\n", "")
        got = re.search(r"\[wad\] sprites: (\d+) pictures for (\d+) things",
                        flat)
        # More pictures than thing types is the whole point: a monster needs
        # eight rotations, a pickup one.  Equal counts would mean the loader
        # found only rotation 0 and every monster faces the same way.
        check("sprite pictures load from the WAD",
              bool(got) and int(got.group(1)) > int(got.group(2)),
              f"{got.group(1)} pictures for {got.group(2)} things"
              if got else "no sprite line")
        check("COLORMAP loads", "COLORMAP: 32 light levels" in flat)

        if fr and frw and len(fr) == len(frw):
            drawn = [(i, x, y) for i in range(len(fr))
                     for x in range(len(fr[i]))
                     for y in range(len(fr[i][x]))
                     if fr[i][x][y] != frw[i][x][y]]
            vals = {fr[i][x][y] for i, x, y in drawn}
            # A billboard is one ramp row shaded 0..13, so it cannot produce
            # colours from more than one row however many of them it draws.
            # Real artwork does, and that is the difference this pins.
            check("sprites draw real artwork, not billboards",
                  len(drawn) > 500 and len(vals) > 30
                  and len({v // 16 for v in vals}) >= 4,
                  f"{len(drawn)} px, {len(vals)} colours, "
                  f"{len({v // 16 for v in vals})} palette rows")

            # Transparency: a post chain leaves the column's own pixels
            # showing between posts.  One band per column could not.
            holes = 0
            for i in range(len(fr)):
                for x in range(len(fr[i])):
                    ys = [y for y in range(len(fr[i][x]))
                          if fr[i][x][y] != frw[i][x][y]]
                    if ys and len(ys) < ys[-1] - ys[0] + 1:
                        holes += 1
            check("transparency shows through between posts", holes > 0,
                  f"{holes} columns with a gap in the sprite")

        # ---- M19: masked middle textures ------------------------------------
        # The camera is stood square in front of one of the map's own masked
        # middles rather than pointed at MAP01 by hand, so this stays a
        # statement about Doom's data.  A wall square to the view has one
        # depth for every column, which is what makes the expected row a
        # number rather than an interpolation.
        print("masked middle textures (M19)")
        MW, MH, MD = 120, 80, 116
        mm = [m for m in masked_middles(iwad, mapname)
              if composite(iwad, m["tex"])]
        if not mm:
            check("the map has a masked middle to draw", False,
                  "no two-sided linedef names an axis-aligned middle texture")
        else:
            # Most transparent first, then longest.  Not a nicety: MAP01
            # names two middles on two-sided lines and one of them,
            # BRNSMAL1, has no holes at all -- it is a solid wall hung in a
            # doorway.  Picking it would leave the transparency check
            # asserting that an opaque texture is see-through, which is a
            # check that can only ever fail or lie.
            def holes(m):
                r = composite(iwad, m["tex"], fill=False)
                return 0 if not r else r[0] * r[1] - len(r[2])
            mm.sort(key=lambda m: (-holes(m),
                                   -(abs(m["a"][0] - m["b"][0])
                                     + abs(m["a"][1] - m["b"][1]))))
            M = mm[0]
            MHOLES = holes(M)
            (ax, ay), (bx, by) = M["a"], M["b"]
            # the front side is to the RIGHT of a->b, which is the direction
            # turned a quarter turn clockwise
            dx, dy = bx - ax, by - ay
            n = max(abs(dx), abs(dy))
            px, py = dy // n, -dx // n
            camx, camy = (ax + bx) // 2 + MD * px, (ay + by) // 2 + MD * py
            # ...and it looks back along that normal.  8192 to a full turn.
            ang = {(0, -1): 2048, (0, 1): 6144,
                   (-1, 0): 0, (1, 0): 4096}[(px, py)]
            tw, th, _ = composite(iwad, M["tex"])
            openb = max(M["front"][0], M["back"][0])
            opent = min(M["front"][1], M["back"][1])
            wtop = (openb + th if M["pegbottom"] else opent) + M["rowoff"]
            mhead = (f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
                     f"\\def\\DTFRAMES{{1}}\\def\\DTW{{{MW}}}"
                     f"\\def\\DTH{{{MH}}}\n"
                     f"\\def\\DTX{{{camx}}}\\def\\DTY{{{camy}}}"
                     f"\\def\\DTANG{{{ang}}}\n\\def\\DTNOMON{{1}}\n")
            open("iwadmask.tex", "w").write(mhead + "\\input doom.tex\n")
            open("iwadmaskno.tex", "w").write(
                "\\def\\DTNOMASK{1}\n" + mhead + "\\input doom.tex\n")
            _, em1, mlog = build("iwadmask.tex")
            _, em2, _ = build("iwadmaskno.tex")
            check("masked middle fixtures compile clean", em1 == 0 and em2 == 0,
                  f"{em1 + em2} errors, standing {MD} units off "
                  f"{M['tex']} on linedef {M['line']}")
            # The eye comes out of the run rather than being computed as
            # floor + \VIEWH here: the camera has to LAND in the front sector
            # for that to be the same number, and asking is both shorter and
            # true on a map this was not written against.
            eye = re.search(r"start: sector -?\d+, floor -?\d+, eye (-?\d+)",
                            mlog.replace("\r", "").replace("\n", ""))
            camz = int(eye.group(1)) if eye else M["front"][0] + 41
            # \dtScale, then \dt@maskcolC's projection, with \numexpr's
            # rounding at every division -- the same arithmetic, so a
            # disagreement is the engine's and not the model's.
            scale = texdiv((MW // 2) * 65536 * 65536, MD * 65536)
            wrow = (MH // 2) - texdiv(texdiv((wtop - camz) * 65536 * scale,
                                             65536), 65536)
            geometry_checks("masked middles", "iwadmask.pdf")
            fm, fmn = frames("iwadmask.pdf"), frames("iwadmaskno.pdf")
            # The window is the columns THIS line covers, less one at each
            # end.  Not the middle third: MAP01's railing turns a corner, and
            # a third of the screen catches the next segment of it, which is
            # nearer and projects higher -- enough to move the top edge by a
            # row and to bridge two runs of bars into one.  The extent is the
            # line's own half-length projected at the distance the camera was
            # put at, which is a number this file chose and knows.
            half = (n // 2) * (MW // 2) // MD
            x0, x1 = MW // 2 - half + 1, MW // 2 + half
            grid = []
            if fm and fmn and len(fm) == len(fmn):
                grid = [[fm[0][x][y] != fmn[0][x][y] for y in range(MH)]
                        for x in range(x0, x1)]
            changed = [(x, y) for x, col in enumerate(grid)
                       for y, v in enumerate(col) if v]
            check("a masked middle is drawn where the wall pass left a hole",
                  bool(changed),
                  f"{len(changed)} px differ from the same view with "
                  f"\\DTNOMASK, which composites it opaque and draws nothing")
            # The top edge is read off the map: opening bottom plus the
            # texture's own height when the line is unpegged from the bottom,
            # the opening's top when it is not, plus the sidedef's row
            # offset.  Getting the pegging backwards moves it by the whole
            # height of the texture.
            top = min(y for _, y in changed) if changed else -1
            check("and its top edge is where the map's own numbers put it",
                  abs(top - wrow) <= 1,
                  f"row {top} against {wrow} computed from opening "
                  f"{openb}..{opent}, {M['tex']} {tw}x{th}, "
                  f"{'bottom' if M['pegbottom'] else 'top'}-pegged, "
                  f"row offset {M['rowoff']}")
            # ...and it is a GRATE, not a wall.  An opaque middle texture
            # would change one contiguous run of columns; bars change several,
            # with the room behind showing through untouched between them.
            # This is the whole feature in one number.
            brow = (MH // 2) - texdiv(texdiv((wtop - th - camz) * 65536 * scale,
                                             65536), 65536)
            mid = (wrow + brow) // 2
            runs = 0
            if 0 <= mid < MH and grid:
                prev = False
                for col in grid:
                    if col[mid] and not prev:
                        runs += 1
                    prev = col[mid]
            check("and the room behind shows through between the bars",
                  MHOLES > 0 and runs >= 3,
                  f"{runs} separate runs of changed columns across row {mid}; "
                  f"an opaque middle would be one. {M['tex']} leaves "
                  f"{MHOLES} of its texels uncovered"
                  if MHOLES else
                  f"{M['tex']} has no holes -- no masked middle on this map "
                  f"can say anything about transparency")
            # The loader's half: exactly the textures a sidedef names as a
            # middle on a two-sided line are kept, counted by check.py's own
            # walk of the map rather than read back off the message.
            named = {m["tex"] for m in
                     masked_middles(iwad, mapname, axis_only=False)}
            got = re.search(r"(\d+) kept as masked middles",
                            mlog.replace("\r", "").replace("\n", ""))
            check("the loader keeps exactly the masked middles the map names",
                  bool(got) and int(got.group(1)) == len(named),
                  f"{got.group(1) if got else '0'} kept against "
                  f"{len(named)} named by a sidedef as a middle on a "
                  f"two-sided line: {', '.join(sorted(named))}")
            # M23: posts BUILT from the composite, where the ones above came
            # ready-made out of a single patch.  The commercial maps name no
            # texture that is both several patches and full of holes, so the
            # builder is handed the textures on this map instead -- whose
            # right answer is already on the page -- and has to give the same
            # picture back.  Then EVERY column is read out and held against
            # check.py's own composite, hole for hole.
            hole_tex = composite(iwad, M["tex"], fill=False)[2]
            open("iwadmaskre.tex", "w").write(
                "\\def\\DTHOOK{"
                + "".join(f"\\dtMaskRebuild{{{t}}}" for t in sorted(named))
                + "".join(f"\\dtMaskCol{{{M['tex']}}}{{{c}}}"
                          for c in range(tw))
                + "}\n" + mhead + "\\input doom.tex\n")
            _, em3, rlog = build("iwadmaskre.tex")
            fre = frames("iwadmaskre.pdf")
            ndiff = (sum(1 for a, b in zip(fre[0], fm[0])
                         for u, v in zip(a, b) if u != v)
                     if fre and fm else -1)
            check("posts built from the composite draw the picture the "
                  "patch's own did", em3 == 0 and ndiff == 0,
                  f"{', '.join(sorted(named))} rebuilt; {ndiff} px differ")
            rflat = rlog.replace("\r", "").replace("\n", "")
            wrong, nposts = [], 0
            for c in range(tw):
                gotc = re.search(r"\[maskcol\] %s %d ((?:\{[^{}]*\})+)"
                                 % (M["tex"], c), rflat)
                want = expected_posts(hole_tex, th, c)
                nposts += want.count("{") // 3 - 1
                if not gotc or gotc.group(1) != want:
                    wrong.append(c)
            check("and every hole in every column is where the WAD says it "
                  "is", not wrong and nposts > tw // 2,
                  f"{tw} columns of {M['tex']}, {nposts} posts between "
                  f"them, {len(wrong)} columns wrong")

        # ---- M9: the HUD ---------------------------------------------------
        print("weapon and status bar (M9)")
        check("status bar loads", "[wad] STBAR:" in flat,
              re.search(r"\[wad\] STBAR: [^\[\s]+", flat).group(0)[6:]
              if "[wad] STBAR:" in flat else "not found")
        # The control for the HUD has to differ from the full render in the
        # HUD and nothing else.  It used to be the geometry-only fixture,
        # which was already loose -- that one also has the sprites off -- and
        # became wrong outright once \DTNOTEX went into it, because then
        # every textured wall pixel counted as "the HUD moved".
        open("iwadnohud.tex", "w").write(
            head + "\\def\\DTHOOK{\\def\\dtDrawHUD{}}\n\\input doom.tex\n")
        secs, errs, _ = build("iwadnohud.tex")
        check("HUD control: compiles clean", errs == 0, f"{errs} errors")
        frn = frames("iwadnohud.pdf")
        if fr and frn:
            H, W = len(fr[0][0]), len(fr[0])
            bar = H - H * 32 // 200
            # Screen space: the bar does not move when the player does, and
            # the geometry underneath it does.  Both halves matter -- a bar
            # that failed to draw would also be "identical" if the player
            # happened to stand still, and this walk does not.
            static = all(fr[0][x][y] == fr[i][x][y]
                         for i in range(1, len(fr))
                         for x in range(W) for y in range(bar, H))
            moving = any(frn[0][x][y] != frn[i][x][y]
                         for i in range(1, len(frn))
                         for x in range(W) for y in range(bar, H))
            check("the status bar is drawn in screen space and does not move",
                  static and moving,
                  f"rows {bar}..{H} fixed over {len(fr)} frames, "
                  f"{'moving' if moving else 'also fixed'} underneath")
            # And the whole HUD stays below the horizon: nothing it draws may
            # reach the top of the view.  With an exact control every
            # differing pixel IS the HUD, so this needs no mask.
            top = min((y for i in range(len(fr)) for x in range(W)
                       for y in range(H) if fr[i][x][y] != frn[i][x][y]),
                      default=H)
            check("the HUD stays in the lower part of the view", top > H // 3,
                  f"highest HUD row {top} of {H}")
        # ---- M13 on real data: lifts, switches and tags -------------------
        # MAP01 carries one of nearly every activation class: a switch lift
        # (special 62, tag 5), a switch floor-lower naming FOUR sectors at
        # once (102, tag 3), switch doors (103) and a walkover door (2).
        # The numbers below were derived from the WAD independently, by
        # walking its LINEDEFS and SECTORS in Python, before any of this was
        # written -- so they are a statement about Doom's data, not a
        # transcript of what the engine happened to print.
        if iwad == "doom2.wad":
            print("lifts, switches and tags on a real map (M13)")
            # SW1BRCOM and SW2BRCOM as \dtUpHex spells them: eight bytes,
            # uppercased, two hex characters each.  Asking the engine for the
            # index of each BY NAME is what turns "the texture changed" into
            # "the texture changed to the pressed face" -- a swap that landed
            # on any other index would pass the first and fail this.
            open("tagunit.tex", "w").write(
                "\\newcount\\fixi \\newcount\\fixj\n"
                "\\def\\DTWAD{doom2.wad}\\def\\DTMAP{MAP01}\n"
                "\\def\\DTFRAMES{0}\\def\\DTW{24}\\def\\DTH{16}\n"
                "\\def\\DTHOOK{%\n"
                "\\message{^^JTAGS line126=\\dtLineTag{126}"
                " sec21=\\dtSecTag{21} line84=\\dtLineTag{84}}%\n"
                # fire the tag-3 floor-lower switch and run the movers down
                "\\dtFireLine{84}%\n"
                "\\fixi=0 \\dtWhile{\\fixi<20}{\\dtPlatTick"
                "\\advance\\fixi by1\\relax}%\n"
                "\\message{^^JTAG3 46=\\csname dtSf46\\endcsname\\space"
                " 47=\\csname dtSf47\\endcsname\\space"
                " 50=\\csname dtSf50\\endcsname\\space"
                " 51=\\csname dtSf51\\endcsname}%\n"
                # sector 21 is a marked mover carrying a DIFFERENT tag, which
                # is what tells "matched the tag" from "started everything"
                "\\message{^^JOTHERTAG 21=\\csname dtSf21\\endcsname}%\n"
                # put one of them back and fire the spent line again
                "\\expandafter\\xdef\\csname dtSf46\\endcsname{112}%\n"
                "\\expandafter\\xdef\\csname dtES46\\endcsname{0}%\n"
                "\\dtFireLine{84}%\n"
                "\\fixi=0 \\dtWhile{\\fixi<3}{\\dtPlatTick"
                "\\advance\\fixi by1\\relax}%\n"
                "\\message{^^JREFIRE 46=\\csname dtSf46\\endcsname}%\n"
                # the switch face on the lift's own switch line
                "\\fixj=\\csname dtLR126\\endcsname\\relax\n"
                "\\message{^^JSWBEFORE \\csname dtYl\\the\\fixj"
                "\\endcsname}%\n"
                "\\dtFireLine{126}%\n"
                "\\message{^^JSWAFTER \\csname dtYl\\the\\fixj"
                "\\endcsname}%\n"
                "\\message{^^JSWNAMES sw1=\\csname"
                " dtXi5357314252434F4D\\endcsname\\space sw2=\\csname"
                " dtXi5357324252434F4D\\endcsname}%\n"
                "\\message{^^JSWWIDTH \\csname dtXw\\csname"
                " dtXi5357324252434F4D\\endcsname\\endcsname}}\n"
                "\\input doom.tex\n")
            secs, errs, ulog = build("tagunit.tex")
            check("tagunit: compiles clean", errs == 0, f"{errs} errors")
            flat = ulog.replace("\r", "").replace("\n", "")

            def field(pat):
                m = re.search(pat, flat)
                return m.groups() if m else None

            g = field(r"TAGS line126=(-?\d+) sec21=(-?\d+) line84=(-?\d+)")
            check("a linedef tag and a sector tag are read from the map",
                  g == ("5", "5", "3"), f"got {g}")
            g = field(r"TAG3 46=(-?\d+) 47=(-?\d+) 50=(-?\d+) 51=(-?\d+)")
            check("a tagged trigger moves every sector carrying that tag",
                  g == ("48", "48", "48", "48"),
                  f"four sectors tagged 3, floors {g} (112 before, 48 is the "
                  f"highest neighbouring floor)")
            g = field(r"OTHERTAG 21=(-?\d+)")
            check("and moves no sector carrying a different one",
                  g == ("152",), f"sector 21 is tagged 5, floor {g}")
            g = field(r"REFIRE 46=(-?\d+)")
            check("a once-only line does not fire a second time",
                  g == ("112",),
                  f"floor put back to 112 and the line pressed again -> {g}")
            b = field(r"SWBEFORE (-?\d+)")
            a_ = field(r"SWAFTER (-?\d+)")
            nm = field(r"SWNAMES sw1=(-?\d+) sw2=(-?\d+)")
            w = field(r"SWWIDTH (-?\d+)")
            if b and a_ and nm:
                check("pressing a switch swaps its face to the pressed one",
                      b[0] == nm[0] and a_[0] == nm[1] and nm[0] != nm[1],
                      f"sidedef texture {b[0]} -> {a_[0]}; "
                      f"SW1BRCOM={nm[0]}, SW2BRCOM={nm[1]}")
            # A pressed face that never composited would fall back to the
            # procedural bricks, which reads as the wall being replaced
            # rather than pressed -- and would still pass the check above.
            if w:
                check("the pressed face is a real composited texture",
                      int(w[0]) > 0, f"SW2BRCOM width {w[0]}")

            # End to end: stand the player on the lift, press nothing else,
            # and let the floor come out from under them.  Everything here is
            # read straight from the frame log -- the sector the engine
            # thinks they are in, the floor it reports, and the eye height.
            for nm2, hook in (("liftride", "\\dtFireLine{126}"),
                              ("liftno", "")):
                open(nm2 + ".tex", "w").write(
                    "\\def\\DTWAD{doom2.wad}\\def\\DTMAP{MAP01}\n"
                    "\\def\\DTX{124}\\def\\DTY{664}\\def\\DTANG{0}\n"
                    "\\def\\DTFRAMES{20}\\def\\DTW{24}\\def\\DTH{16}\n"
                    "\\def\\DTHOOK{\\def\\dtStepFwd##1{}" + hook + "}\n"
                    "\\input doom.tex\n")
            secs, errs, llog = build("liftride.tex")
            check("liftride: compiles clean", errs == 0, f"{errs} errors")
            secs, errs, nlog = build("liftno.tex")
            tl, tn = track(llog), track(nlog)
            if tl and tn:
                fl = [f for _, _, _, f, _ in tl]
                ey = [e for _, _, _, _, e in tl]
                fn = [f for _, _, _, f, _ in tn]
                check("a lift lowers the floor under a player who is standing "
                      "still", fl[0] == 144 and min(fl) == 40
                      and all(b <= a for a, b in zip(fl, fl[1:])),
                      f"floor {fl[0]} -> {min(fl)} over {len(fl)} frames")
                # The control matters: the player is not moving, so a floor
                # that changed for any other reason would look the same.
                check("with the switch unpressed the floor never moves",
                      set(fn) == {152}, f"floors {sorted(set(fn))}")
                # \dtFollowFloor is reached only from \dtTryMove, so before
                # \dt@platride this stayed at its starting 193 for the whole
                # descent -- the floor visibly dropping away underneath a
                # camera hanging in the air, with no error and no other check
                # able to see it.
                check("and the eye rides it down", ey[-1] == fl[-1] + VIEWHEIGHT
                      and ey[0] < 193,
                      f"eye {ey[0]} -> {ey[-1]}, floor {fl[-1]} + {VIEWHEIGHT}")

            # ---- M14 on real data: pickups and the bar ------------------
            # These positions are MAP01's own.  The medikit room is the
            # octagon bounded by x 896..1088, and the three medikits sit in a
            # row at x 928, 992 and 1056 on y 1104, all on floor 40 -- read
            # out of the WAD's THINGS and LINEDEFS in Python before any of
            # this was written.
            print("pickups on a real map (M14)")
            # \DTNOMON: the rule under test is which medikits a wounded
            # player can use, and an imp shooting the same player's health up
            # and down while they walk decides it instead.
            open("pickmap.tex", "w").write(
                "\\def\\DTWAD{doom2.wad}\\def\\DTMAP{MAP01}\n"
                "\\def\\DTNOMON{1}\n"
                "\\def\\DTX{912}\\def\\DTY{1104}\\def\\DTANG{0}\n"
                "\\def\\DTFRAMES{6}\\def\\DTW{40}\\def\\DTH{24}\n"
                "\\def\\DTHOOK{\\dtHEALTH=50 }\n"
                "\\input doom.tex\n")
            _, errs, mlog = build("pickmap.tex")
            check("pickmap: compiles clean", errs == 0, f"{errs} errors")
            hm = hud(mlog)
            if hm:
                # Two taken, the third refused because the second filled the
                # player up.  Three identical medikits on a floor nobody laid
                # out for this test, and the rule decides which two go.
                check("a walk down MAP01's medikits takes what it can use",
                      [h for h, _, _, _, _ in hm] == [75, 100, 100, 100,
                                                      100, 100],
                      f"health {[h for h, _, _, _, _ in hm]}")
                check("and leaves the one it cannot",
                      [t for _, _, _, _, t in hm] == [1, 2, 2, 2, 2, 2],
                      f"taken {[t for _, _, _, _, t in hm]} of three")
            # The shotgun at (320,368) with its shells beside it at (384,368).
            # Picking it up must change three things at once: the weapon, the
            # ammunition it brings, and which ammunition the bar is reading.
            #
            # \DTNOMON, because this is a check about the item table and there
            # are three imps in that room.  Without it they close on the
            # player, stand in the doorway the walk goes through, and the
            # check reports a pickup rule that was never exercised.
            open("pickgun.tex", "w").write(
                "\\def\\DTWAD{doom2.wad}\\def\\DTMAP{MAP01}\n"
                "\\def\\DTNOMON{1}\n"
                "\\def\\DTX{280}\\def\\DTY{368}\\def\\DTANG{0}\n"
                "\\def\\DTFRAMES{3}\\def\\DTW{40}\\def\\DTH{24}\n"
                "\\input doom.tex\n")
            _, errs, glog2 = build("pickgun.tex")
            hg = hud(glog2)
            if hg:
                check("picking up the shotgun arms it and switches the count",
                      [w for _, _, _, w, _ in hg] == [2, 2, 2]
                      and [a for _, _, a, _, _ in hg] == [8, 12, 12],
                      f"weapons {[w for _, _, _, w, _ in hg]}, and the bar "
                      f"reads 50 -> {[a for _, _, a, _, _ in hg]}")

            # ---- M15: enemies and combat --------------------------------
            # The first thing in this engine that acts on its own, so the
            # controls carry more weight than usual: everything below has a
            # \DTNOMON twin or a stubbed half, because "the health went down"
            # is also what a bug in the pickup scan looks like.
            print("enemies and combat (M15)")

            # Line of sight, against a second implementation.  check.py walks
            # the map's LINEDEFS; the engine walks its SEGS.  The segs
            # partition the linedefs exactly, so the two have to agree on
            # every pair, and a disagreement is a real bug in one of them.
            #
            # The probes are the map's own: the player start to each monster
            # it carries.  That is a set nobody chose to make the check pass,
            # and it is asserted below to contain both answers.
            see = sight_model(iwad, mapname)
            T = map_lumps(iwad, mapname)["THINGS"]
            things = [struct.unpack("<hhhhh", T[10 * i:10 * i + 10])
                      for i in range(len(T) // 10)]
            start = next(((x, y) for x, y, _, t, _ in things if t == 1), None)
            MONS = (3004, 9, 65, 84, 3001, 3002, 58, 3005, 3003, 69, 66, 3006)
            probes = [(x, y) for x, y, _, t, fl in things
                      if t in MONS and not fl & 16]
            want = [see(start[0], start[1], px, py) for px, py in probes]
            body = "".join("\\S{%d}{%d}{%d}{%d}" % (start[0], start[1], px, py)
                           for px, py in probes)
            open("sightunit.tex", "w").write(
                "\\catcode`\\@=11\n"
                f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
                "\\def\\DTFRAMES{0}\\def\\DTW{16}\\def\\DTH{16}\n"
                "\\def\\DTHOOK{%\n"
                "\\def\\S##1##2##3##4{\\dtSight{##1}{##2}{##3}{##4}%\n"
                "\\message{^^JSEE ##3 ##4 \\the\\dtSEE}}%\n"
                + body + "}\n\\input doom.tex\n")
            _, errs, slog = build("sightunit.tex")
            check("sight unit: compiles clean", errs == 0, f"{errs} errors")
            got = [v == "1" for v in re.findall(
                r"SEE -?\d+ -?\d+ (\d)", slog.replace("\r", "").replace("\n", ""))]
            # A probe set that is all one answer would make the comparison
            # below meaningless, so that is asserted rather than assumed --
            # the same argument \dtFlatRow's asymmetry check makes.
            check("the sight probes are not all one answer",
                  any(want) and not all(want),
                  f"{sum(want)} of {len(want)} monsters visible from the "
                  f"player start")
            bad = [i for i in range(min(len(got), len(want)))
                   if got[i] != want[i]]
            check("line of sight agrees with an independent one",
                  len(got) == len(want) and not bad,
                  f"{len(got)} probes, all agree" if not bad
                  else f"{len(bad)} of {len(got)} disagree, first at "
                       f"{probes[bad[0]]}")

            # Every frame of every state has to be a picture the loader
            # actually found, or a zombieman turns into a coloured billboard
            # on whichever frame is missing -- quietly, since the fallback is
            # what every thing without artwork already uses.  Four more
            # sequences per monster is most of a doubling.
            g = re.search(r"\[wad\] sprites: (\d+) pictures for (\d+) things",
                          log.replace("\n", ""))
            check("the states a monster can be in are all loaded",
                  bool(g) and int(g.group(1)) > 120,
                  f"{g.group(1)} pictures for {g.group(2)} things -- walking, "
                  f"shooting, flinching and dying, at eight rotations"
                  if g else "no sprite line")

        # ---- M15: enemies and combat ------------------------------------
        # On the BUILT-IN map, deliberately.  Every mechanism below is about
        # geometry, and the built-in map's geometry is three rooms whose
        # walls this file already names: on MAP01 a walk that stops could be
        # stopped by the monster or by the corridor, and telling those apart
        # is exactly what a fixture is for.  It also costs a second and a
        # half a compile rather than a hundred.
        #
        # \dtSpawnMonsters is guarded on dtOs, so a fixture can add a thing
        # from \DTHOOK and spawn it there -- \DTHOOK runs after the engine's
        # own call.  The control is then the same thing in the same place
        # with no state machine on it, which is a tighter control than
        # \DTNOMON: identical geometry, identical sprite, no monster.
        print("enemies and combat (M15)")
        MONHOOK = ("\\def\\DTHOOK{%%\n"
                   "\\dtAddThing{%d}{256}{3004}{24}%%\n"
                   "%s%s"
                   "\\let\\dt@ptwas\\dtPlayerTick\n"
                   "\\def\\dtPlayerTick{\\dt@ptwas\\dt@mondump}%%\n"
                   "\\def\\dt@mondump{\\fixi=0\n"
                   "\\dtWhile{\\fixi<\\NTHING}{%%\n"
                   "\\ifcsname dtOs\\the\\fixi\\endcsname\n"
                   "\\message{^^JMON \\the\\fixi\\space"
                   " s=\\csname dtOs\\the\\fixi\\endcsname\\space"
                   " h=\\csname dtOh\\the\\fixi\\endcsname\\space"
                   " at \\csname dtHx\\the\\fixi\\endcsname,"
                   "\\csname dtHy\\the\\fixi\\endcsname}%%\n"
                   "\\fi\\advance\\fixi by1\\relax}}%%\n"
                   "%s}\n")

        def monfix(name, at=500, spawn=True, extra="", frames=16, pre="",
                   also=None):
            # `also' is a second monster, added BEFORE the spawn call so that
            # it gets a state machine too.
            more = ("\\dtAddThing{%d}{%d}{3004}{24}%%\n" % also) if also else ""
            open(name + ".tex", "w").write(
                "\\catcode`\\@=11\n\\newcount\\fixi\n"
                + pre
                + f"\\def\\DTFRAMES{{{frames}}}\\def\\DTW{{24}}"
                "\\def\\DTH{16}\n"
                + MONHOOK % (at, more,
                             "\\dtSpawnMonsters\n" if spawn else "", extra)
                + "\\input doom.tex\n")
            _, e, lg = build(name + ".tex")
            return e, lg

        def mondump(lg):
            """{index: [(state, health, x, y), ...]} over the frames."""
            out = {}
            for i, s, h, x, y in re.findall(
                    r"MON (\d+) s=(\d+) h=(-?\d+) at (-?\d+),(-?\d+)",
                    lg.replace("\r", "").replace("\n", "")):
                out.setdefault(int(i), []).append(
                    (int(s), int(h), int(x), int(y)))
            return out

        STAND = "\\def\\dtStepFwd##1{}"
        # A zombieman 300 units east of a player who never moves.  Nothing
        # else on this map can move it or take health off, so every number
        # below belongs to the monster.
        e1, wlog = monfix("monwake", at=400, extra=STAND, frames=44)
        e2, nlog = monfix("monwakeno", at=400, spawn=False, extra=STAND,
                          frames=44)
        check("monster fixtures compile clean", e1 == 0 and e2 == 0,
              f"{e1 + e2} errors")
        M, Mn = mondump(wlog), mondump(nlog)
        hp = [h for h, _, _, _, _ in hud(wlog)]
        hpn = [h for h, _, _, _, _ in hud(nlog)]
        woke = [i for i, r in M.items() if any(s > 0 for s, _, _, _ in r)]
        check("a monster wakes when it can see the player", bool(woke),
              f"{len(woke)} of {len(M)} left the spawn loop")
        moved = [i for i, r in M.items() if r[-1][2] != r[0][2]]
        check("and closes on them", bool(moved)
              and all(M[i][-1][2] < M[i][0][2] for i in moved),
              f"walked from x={M[moved[0]][0][2]} to {M[moved[0]][-1][2]}"
              if moved else "nothing moved")
        check("and shoots", hp and hp[-1] < 100,
              f"health {hp[0]} -> {hp[-1]} over {len(hp)} frames"
              if hp else "no frames")
        # A_PosAttack is ((P_Random%5)+1)*3, so every amount a zombieman can
        # take off is a multiple of three between 3 and 15.  A damage path
        # that had lost the multiplier, the +1 or the modulus would still
        # lower health, and only this says which.
        drops = [a - b for a, b in zip(hp, hp[1:]) if a != b]
        check("in Doom's own steps",
              bool(drops) and all(d % 3 == 0 and 3 <= d <= 15 for d in drops),
              f"hits of {drops}, all ((rand%5)+1)*3")
        # The control is the same thing in the same place, unspawned: the
        # sprite is still drawn and the map is otherwise identical, so this
        # separates "the monster did it" from "something did it".
        check("and none of that happens to a thing with no state machine",
              not Mn and hpn and all(h == 100 for h in hpn),
              f"health {hpn[0] if hpn else '?'} throughout, "
              f"{len(Mn)} things ticking")

        # Shooting back.  \DTFIRE holds the trigger down, so the pistol
        # empties into the zombieman standing in front of it.
        #
        # In the PREAMBLE, not in \DTHOOK: \dtFireStep is settled from
        # \DTFIRE before the hook runs, exactly as \dtUseStep is settled from
        # \DTUSE.  Setting it in the hook compiles, changes nothing, and
        # reports a player who fired forty times without spending a bullet.
        #
        # A second zombieman at (200,400) is the decoy, and it is what gives
        # the aim's WIDTH a check.  It is NEARER along the facing than the
        # target and 144 units off it, so a scan that had stopped measuring
        # how far a thing is from the ray would pick the decoy and kill it
        # instead -- while a decoy placed beside the target, at the same
        # distance, would leave the same bug invisible.
        e3, klog = monfix("monkill", at=300, extra=STAND, frames=40,
                          pre="\\def\\DTFIRE{1}\\def\\DTNOWAIT{1}\n",
                          also=(200, 400))
        flatk = klog.replace("\r", "").replace("\n", "")
        kills = [int(v) for v in re.findall(r"kill (\d+) of", flatk)]
        ammo = [a for _, _, a, _, _ in hud(klog)]
        K = mondump(klog)
        check("firing kills a monster and counts it",
              e3 == 0 and kills and kills[-1] == 1,
              f"{kills[-1] if kills else '?'} killed, "
              f"first at frame {kills.index(1) + 1 if 1 in kills else '-'}")
        # Doom spends a bullet whether or not the shot hits.  A count that
        # only fell on a hit would look identical on this fixture until the
        # monster died, which is why the whole run is compared.
        check("and every shot costs a bullet",
              ammo == list(range(49, 49 - len(ammo), -1)),
              f"50 -> {ammo[-1] if ammo else '?'} over {len(ammo)} frames")
        dead = [i for i, r in K.items() if r[-1][0] == 5]
        check("a dead monster ends as a corpse", bool(dead)
              and all(K[i][-1][1] == 0 for i in dead),
              f"{len(dead)} reached state 5 at 0 health")
        # The decoy: nearer along the facing, 144 units off it, and it must
        # come through untouched.  Thing 0 is the target and thing 1 the
        # decoy, in the order \dtAddThing placed them.
        check("a shot misses what is not in front of it",
              1 in K and K[1][-1][1] == 20 and 0 in K and K[0][-1][1] == 0,
              f"the decoy kept {K[1][-1][1] if 1 in K else '?'} of its 20 "
              f"health; the target ended on "
              f"{K[0][-1][1] if 0 in K else '?'}")
        # ...and stops being a target, which is what keeps the kill count
        # from running away while the trigger is still held.
        check("and is not killed twice", kills and kills[-1] == len(dead),
              f"{kills[-1] if kills else '?'} kills, {len(dead)} corpses, "
              f"{len(ammo)} shots fired")

        # Solid.  The scripted walk runs east from x=100 along y=256 and the
        # east wall is at 1024; a zombieman at 500 has to stop it well short
        # of that, and the control is the same walk with the same thing in it
        # and no state machine.
        e4, blog = monfix("monblock", at=500, frames=16)
        e5, bnlog = monfix("monblockno", at=500, spawn=False, frames=16)
        xs = [x for x, _, _, _, _ in track(blog)]
        xsn = [x for x, _, _, _, _ in track(bnlog)]
        check("a live monster blocks the player",
              e4 == 0 and e5 == 0 and xs and xsn and max(xs) < max(xsn) - 200,
              f"walked to x={max(xs) if xs else '?'} against the control's "
              f"{max(xsn) if xsn else '?'} (east wall 1024)")

        # Dying.  Health is set low enough that the zombieman finishes the
        # job, and then two things have to follow: the player stops walking,
        # and the view drops to Doom's death height of six above the floor.
        e6, dlog = monfix("mondead", at=400,
                          extra="\\dtHEALTH=8 ", frames=44)
        td = track(dlog)
        hd = [h for h, _, _, _, _ in hud(dlog)]
        if td and hd:
            died = [i for i, h in enumerate(hd) if h == 0]
            xs2 = [x for x, _, _, _, _ in td]
            eyes = [e for _, _, _, _, e in td]
            flrs = [f for _, _, _, f, _ in td]
            check("a monster can kill the player", e6 == 0 and bool(died),
                  f"health {hd[0]} -> {hd[-1]}, dead from frame "
                  f"{died[0] + 1 if died else '-'}")
            if died:
                i = died[0]
                check("a dead player stops walking",
                      len(set(xs2[i:])) == 1,
                      f"x pinned at {xs2[-1]} for {len(xs2) - i} frames")
                # P_DeathThink drops viewheight to 6.  It eases there at the
                # rate \dtFollowFloor already uses, so the last frame is what
                # gets asserted rather than the first.
                check("and the view drops to the floor",
                      eyes[-1] == flrs[-1] + 6,
                      f"eye {eyes[i]} -> {eyes[-1]}, floor {flrs[-1]} + 6")

        # ---- M15: damage, armour and the rest of the face ------------------
        # Arithmetic, so no WAD is needed and no pixel is counted: what a hit
        # costs and which face answers it are both questions with names.
        open("hurtunit.tex", "w").write(
            "\\catcode`\\@=11\n"
            "\\def\\DTFRAMES{0}\\def\\DTW{16}\\def\\DTH{16}\n"
            "\\def\\DTHOOK{%\n"
            "\\def\\D##1##2##3##4{\\dtHEALTH=##1 \\dtARMOR=##2 \\dtARMCL=##3 \n"
            "\\dtDamagePlayer{##4}{\\numexpr\\dtPSX+100\\relax}{\\dtPSY}%\n"
            "\\message{^^JHURT ##1 ##2 ##3 ##4 -> hp \\the\\dtHEALTH\\space"
            " ar \\the\\dtARMOR\\space cl \\the\\dtARMCL}}%\n"
            # no armour: the whole hit lands
            "\\D{100}{0}{0}{20}%\n"
            # green absorbs a third, blue a half
            "\\D{100}{100}{1}{30}%\n"
            "\\D{100}{100}{2}{30}%\n"
            # armour that runs out stops being armour
            "\\D{100}{2}{1}{30}%\n"
            # health floors at zero rather than going negative
            "\\D{10}{0}{0}{50}%\n"
            "\\def\\F##1##2##3##4{\\dtHEALTH=##1 \\dtHURT=##2 \\dtHURTC=##3 \n"
            "\\dtATTL=##4 \\dt@facepick\n"
            "\\message{^^JFACE2 ##1 ##2 ##3 ##4 \\dtFaceKey}}%\n"
            # unhurt; a small hit from the left, then from the right; a big
            # one, which is an ouch, at two pain levels; and dead, which
            # outranks a wound that landed on the same frame.
            "\\F{100}{0}{0}{0}%\n"
            "\\F{100}{5}{9}{1}%\n"
            "\\F{100}{5}{9}{0}%\n"
            "\\F{100}{25}{9}{1}%\n"
            "\\F{40}{25}{9}{1}%\n"
            "\\F{0}{25}{9}{1}%\n"
            "\\dtHURTC=0 \\dtGRINC=5 \\dtHEALTH=100 \\dt@facepick\n"
            "\\message{^^JFACE2 grin 0 0 0 \\dtFaceKey}%\n"
            "\\dtGRINC=0 \\dtFIREC=5 \\dt@facepick\n"
            "\\message{^^JFACE2 rage 0 0 0 \\dtFaceKey}}\n"
            "\\input doom.tex\n")
        _, errs, hlog = build("hurtunit.tex")
        check("damage unit: compiles clean", errs == 0, f"{errs} errors")
        flath = hlog.replace("\r", "").replace("\n", "")
        H2 = {m[:4]: m[4:] for m in re.findall(
            r"HURT (-?\d+) (-?\d+) (-?\d+) (-?\d+) -> hp (-?\d+) ar (-?\d+) "
            r"cl (-?\d+)", flath)}
        # P_DamageMobj: green armour takes a third of the hit, blue a half,
        # and what the armour absorbs the player does not.
        check("armour absorbs its share of a hit",
              H2.get(("100", "0", "0", "20")) == ("80", "0", "0")
              and H2.get(("100", "100", "1", "30")) == ("80", "90", "1")
              and H2.get(("100", "100", "2", "30")) == ("85", "85", "2"),
              f"no armour 100->{H2.get(('100','0','0','20'), ('?',))[0]}, "
              f"green ->{H2.get(('100','100','1','30'), ('?',))[0]}, "
              f"blue ->{H2.get(('100','100','2','30'), ('?',))[0]}")
        # Armour that cannot cover its share absorbs what it has and stops
        # being armour at all -- the class goes, not just the points.
        check("armour that runs out stops being armour",
              H2.get(("100", "2", "1", "30")) == ("72", "0", "0"),
              f"2 points of green against 30 -> "
              f"{H2.get(('100','2','1','30'), ('?','?','?'))}")
        check("health floors at zero rather than going negative",
              H2.get(("10", "0", "0", "50")) == ("0", "0", "0"),
              f"10 health against 50 -> "
              f"{H2.get(('10','0','0','50'), ('?',))[0]}")
        F2 = {m[0]: m[4] for m in re.findall(
            r"FACE2 (\S+) (\S+) (\S+) (\S+) (\S+)", flath)}
        # ST_updateFaceWidget is a priority list and the order is the point:
        # a dead player does not grin and a player just shot does not glance.
        faces = re.findall(r"FACE2 \S+ \S+ \S+ \S+ (\S+)", flath)
        check("the face turns toward whatever hit it",
              faces[1:3] == ["STFTL00", "STFTR00"],
              f"left/right -> {faces[1:3] if len(faces) > 2 else faces}")
        # 40 health is pain level 2: ST_calcPainOffset truncates (100-40)*5
        # over 101, which is 2 and not the 2.97 a rounding division gives.
        check("a big hit is an ouch, at the right pain level",
              faces[3:5] == ["STFOUCH0", "STFOUCH2"],
              f"25 damage at 100 and at 40 health -> "
              f"{faces[3:5] if len(faces) > 4 else faces}")
        check("and death outranks all of it",
              len(faces) > 5 and faces[5] == "STFDEAD0",
              f"dead with a fresh 25-point wound -> "
              f"{faces[5] if len(faces) > 5 else '?'}")
        check("a new weapon grins and a held trigger rampages",
              faces[6:8] == ["STFEVL0", "STFKILL0"],
              f"{faces[6:8] if len(faces) > 7 else faces}")

        # ---- M14: the bar reads the player's state ----------------------
        # 320x200 so that Doom's status bar coordinates ARE screen columns:
        # \dtHDA and \dtHDB are both 65536 there, which is what lets these
        # name the columns Doom names.  The player is held still and one
        # frame is rendered, so nothing but the state under test can move a
        # pixel.
        print("the status bar reads the player (M14)")
        # From iwad.tex's own load, not from whichever log `flat` last held.
        # 53: ten digits, a percent, the bar, the corpse, fifteen glancing
        # faces, and the twenty-five M15 added -- five pain levels of
        # {turn left, turn right, ouch, evil grin, rampage}.  The floor is
        # tight on purpose: \dt@hudpat draws nothing at all when a picture is
        # missing, so a face that failed to load is a face that silently
        # vanishes the moment the player is hurt.
        g = re.search(r"\[wad\] hud: (\d+) pictures", log.replace("\n", ""))
        check("the bar's digits and faces load",
              bool(g) and int(g.group(1)) >= 53,
              f"{g.group(1)} pictures (10 digits, a percent, the bar, the "
              f"corpse, and 5 pain levels x 8 faces)" if g else "no hud line")

        hudhead = (f"\\catcode`\\@=11\n"
                   f"\\def\\DTWAD{{{iwad}}}\\def\\DTMAP{{{mapname}}}\n"
                   "\\def\\DTFRAMES{1}\\def\\DTW{320}\\def\\DTH{200}"
                   "\\def\\DTPIX{2}\n")

        def hudfix(name, hook):
            open(name + ".tex", "w").write(
                hudhead + "\\def\\DTHOOK{\\def\\dtStepFwd##1{}" + hook + "}\n"
                "\\input doom.tex\n")

        hudfix("hudoff", "\\def\\dt@hudstate{}")
        hudfix("hudbase", "")
        # The fist, deliberately: it draws no ammunition count, so the only
        # digits anywhere near the health field are the health field's own.
        # Without that a layout mutation can march the AMMO digits into the
        # health box and the alignment check reads those instead (§11).
        hudfix("hud11", "\\dtWEAP=0 \\dtHEALTH=11 ")
        hudfix("hud111", "\\dtWEAP=0 \\dtHEALTH=111 ")
        hudfix("hud20", "\\dtHEALTH=20 ")
        hudfix("hud107", "\\dtHEALTH=107 ")
        hudfix("hudsg", "\\dtWEAP=2 ")
        hudfix("hudfist", "\\dtWEAP=0 ")
        # ---- M17: the arms panel, the keys and the ammunition boxes ------
        # Each of the three is a pair: the widget's subject set, and the same
        # frame with it clear.  \dtWEAP is deliberately NOT the subject of
        # the arms panel -- what it reports is dtUW, i.e. what you are
        # carrying, and holding a weapon you do not own is exactly the
        # confusion a check should be able to tell apart.
        hudfix("hudarms", "\\expandafter\\gdef\\csname dtUW2\\endcsname{1}"
                          "\\expandafter\\gdef\\csname dtUW4\\endcsname{1}")
        hudfix("hudkey", "\\expandafter\\gdef\\csname dtUK1\\endcsname{1}")
        # A backpack doubles the maxima, which is the only thing on the whole
        # bar that ever moves the right-hand column of the ammunition boxes.
        hudfix("hudpack", "\\dtGOT=0 \\def\\dt@iva{0}\\def\\dt@ivb{0}"
                          "\\dt@gpack")
        herr = 0
        for nm in ("hudoff", "hudbase", "hud11", "hud111", "hud20",
                   "hud107", "hudsg", "hudfist",
                   "hudarms", "hudkey", "hudpack"):
            _, e, _ = build(nm + ".tex")
            herr += e
        check("status bar fixtures compile clean", herr == 0, f"{herr} errors")
        F = {nm: frames(nm + ".pdf")[0] for nm in
             ("hudoff", "hudbase", "hud11", "hud111", "hud20", "hud107",
              "hudsg", "hudfist", "hudarms", "hudkey", "hudpack")
             if frames(nm + ".pdf")}
        # Doom's own boxes: ammo right edge 44, health 90, armour 221, all
        # three at y 171; the face at 143,168 plus its (-5,-2) offsets.
        #
        # HEALTH is 48..90 and both edges are load-bearing, which two
        # mutations had to teach me (§11).  90 excludes STTPRCNT, whose left
        # edge sits exactly there -- with 91 the percent sign alone satisfied
        # "the digits are right-aligned" while the digits were off marching
        # rightward out of the box.  48 is where three digits at Doom's pitch
        # begin, and excludes the AMMO number: at a pitch narrower than the
        # glyphs the ammo digits overrun their own field as far as 47, so a
        # wider box reports a layout change that belongs to a different
        # widget.
        AMMO, HEALTH = (2, 45, 170, 188), (48, 90, 170, 188)
        ARMOUR, FACE = (177, 222, 170, 188), (148, 173, 170, 199)
        VIEW = (0, 320, 0, 168)
        # M17's three, and none of them overlaps the four above -- STARMS is
        # 40 wide from 104 and the face box starts at 148, the keys sit at
        # 239 against an armour box ending at 222, and the small numbers are
        # right-aligned on 288 and 314.  Those are the WAD's own patch
        # widths, checked rather than hoped.
        ARMS, KEYS = (104, 148, 168, 190), (236, 250, 168, 198)
        AMMOBOX, MAXBOX = (272, 290, 170, 198), (298, 316, 170, 198)
        if len(F) == 11:
            n = {k: boxdiff(F["hudbase"], F["hudoff"], *b)[0] for k, b in
                 (("ammo", AMMO), ("health", HEALTH), ("armour", ARMOUR),
                  ("face", FACE))}
            # Four separate widgets, one control.  With an exact control
            # every differing pixel IS the widget, so this needs no mask.
            check("the bar shows ammo, health, armour and a face",
                  all(v > 0 for v in n.values()), f"{n}")
            # Right-aligned, and on the pitch of the digit ZERO.  STTNUM1 is
            # three pixels narrower than the rest, so an implementation that
            # advanced by each digit's own width would still draw 11 and 111
            # -- but the gap between where they start would be 11, not 14.
            # The expected gap is read from the WAD rather than assumed.
            w0 = struct.unpack("<h", lump(iwad, "STTNUM0")[:2])[0]
            c11 = boxdiff(F["hud11"], F["hudoff"], *HEALTH)
            c111 = boxdiff(F["hud111"], F["hudoff"], *HEALTH)
            check("health digits are right-aligned on Doom's column",
                  c11[2] == c111[2] and c11[0] > 0,
                  f"11 and 111 both end at column {c11[2]}")
            check("and laid out on the width of the digit zero",
                  c11[1] - c111[1] == w0,
                  f"one more digit starts {c11[1] - c111[1]} columns left, "
                  f"STTNUM0 is {w0} wide")
            # The face is the one widget that is not a number, so it gets its
            # own pair: a change across a pain threshold must move it, and a
            # change that does not cross one must not.  107 also proves the
            # clamp, since without it the pain index would go negative.
            fd = boxdiff(F["hud20"], F["hudbase"], *FACE)[0]
            fs = boxdiff(F["hud107"], F["hudbase"], *FACE)[0]
            check("the face changes with the damage taken", fd > 0,
                  f"{fd} px of the face differ at 20 health")
            check("and not with a change inside one pain level", fs == 0,
                  f"107 and 100 draw the same face, {fs} px differ")
            # The ammo widget reads the weapon in hand, not a fixed slot.
            ad = boxdiff(F["hudsg"], F["hudbase"], *AMMO)[0]
            vd = boxdiff(F["hudsg"], F["hudbase"], *VIEW)[0]
            af = boxdiff(F["hudfist"], F["hudoff"], *AMMO)[0]
            check("the ammo count reads the weapon in hand",
                  ad > 0, f"{ad} px differ between 50 bullets and 0 shells")
            check("and the weapon in your hands is the one you are holding",
                  vd > 0, f"{vd} px of the view differ with the shotgun up")
            # Doom points the widget at a sentinel its draw routine refuses,
            # rather than drawing a zero, for the two weapons that use none.
            check("the fist shows no ammunition at all", af == 0,
                  f"{af} px drawn where the count would be")

            # ---- M17: the rest of the bar ---------------------------
            # The arms panel is drawn either way -- STARMS is a background --
            # so "it appears" says nothing.  What has to change is the SIX
            # numbers on it, between a player carrying the shotgun and the
            # rocket launcher and one carrying neither.
            ab = boxdiff(F["hudbase"], F["hudoff"], *ARMS)[0]
            aw = boxdiff(F["hudarms"], F["hudbase"], *ARMS)[0]
            check("the arms panel is on the bar", ab > 0,
                  f"{ab} px of panel and numbers")
            check("and lights the weapons you are carrying", aw > 0,
                  f"{aw} px differ with the shotgun and rocket launcher owned")
            # ...and it is dtUW it reads, not \dtWEAP.  hudsg HOLDS weapon 2
            # without owning it, so an arms panel keyed on the wrong one
            # would light up here.
            asg = boxdiff(F["hudsg"], F["hudbase"], *ARMS)[0]
            check("and reads what you own, not what is in your hands",
                  asg == 0,
                  f"holding the shotgun without owning it moves {asg} px of "
                  f"the panel")
            # Keys: one held draws an icon, none held draws nothing.
            kd = boxdiff(F["hudkey"], F["hudbase"], *KEYS)[0]
            kb = boxdiff(F["hudbase"], F["hudoff"], *KEYS)[0]
            check("a key you are holding shows on the bar", kd > 0,
                  f"{kd} px of key icon")
            check("and one you are not does not", kb == 0,
                  f"{kb} px drawn with no keys held")
            # The ammunition boxes.  A backpack doubles the MAXIMA and gives
            # a clip of each, so it has to move both columns -- and the pair
            # is what says the right-hand one is reading dtUM at all.
            pa = boxdiff(F["hudpack"], F["hudbase"], *AMMOBOX)[0]
            pm = boxdiff(F["hudpack"], F["hudbase"], *MAXBOX)[0]
            bb = boxdiff(F["hudbase"], F["hudoff"], *AMMOBOX)[0]
            check("the ammunition boxes are on the bar", bb > 0,
                  f"{bb} px in the held column")
            check("and a backpack moves the maxima beside them",
                  pa > 0 and pm > 0,
                  f"{pa} px of held and {pm} px of maximum differ after a "
                  f"backpack")

        # ---- M23, the part that needs real artwork ---------------------------
        # iwad.pdf is re-read rather than trusted from a variable set two
        # thousand lines up: its first frame is the control for the two view
        # checks, and that has to be the plain start of the map.
        base = frames("iwad.pdf")
        m23_iwad(iwad, mapname, base[0] if base else None)

    else:
        print("real IWAD -- none present, skipped")

    if not quick:
        print("benchmark")
        # By DIFFERENCE, never by dividing a whole run -- INTERNALS §9 spends
        # three paragraphs on this and the harness used to do exactly what it
        # forbids.  A run is load + N frames, and the load is not small: even
        # here, with no WAD to parse, it is ~0.4 s against ~1.5 s of frames, so
        # dividing reported ~185 ms for a frame that costs ~148.  On a machine
        # where \pdffiledump is slow the same mistake reports 4 s/frame and
        # hides a 1.6x regression completely.
        for name, n in (("bench0.tex", 0), ("bench.tex", 10)):
            open(name, "w").write(
                f"\\def\\DTW{{320}}\\def\\DTH{{200}}\\def\\DTPIX{{2}}"
                f"\\def\\DTFRAMES{{{n}}}\n\\input doom.tex\n")
        # Best of three each, interleaved: this box has been seen to run 1.6x
        # slower than it had an hour earlier, and interleaving is what keeps
        # the two halves of the subtraction in the same weather.
        best = {"bench0.tex": None, "bench.tex": None}
        errs = 0
        for _ in range(3):
            for name in ("bench0.tex", "bench.tex"):
                secs, e, _ = build(name)
                errs += e
                if best[name] is None or secs < best[name]:
                    best[name] = secs
        load, whole = best["bench0.tex"], best["bench.tex"]
        per = (whole - load) / 10 * 1000
        check("320x200 compiles clean", errs == 0, f"{errs} errors")
        # A load that outweighs the frames means the subtraction is carrying
        # the measurement, which is precisely when dividing would have lied.
        check("the benchmark measures frames, not the load", per > 0,
              f"{per:.0f} ms/frame ({whole:.2f}s - {load:.2f}s load) / 10; "
              f"dividing the whole run would say {whole / 10 * 1000:.0f}")

    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)} check(s): {', '.join(FAIL)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
