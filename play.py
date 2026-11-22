#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""Drive doom.tex's interactive mode: real keys in, a colour picture out.

doom.tex can already be played on its own -- \\read16 blocks for a line and
the engine draws ASCII to the terminal.  What it cannot do is read a key
without Enter behind it, or put a colour on a pixel, or remember what you
did.  None of that belongs in the engine: a TeX macro has no way to ask what
the terminal is capable of.  So \\DTPIPE hands the raw framebuffer over
instead of drawing it, and this file decides how to show it.

    python3 play.py --wad doom2.wad --map MAP01
    python3 play.py --wad doom2.wad --w 304 --h 240      # a 304x240 recording
    python3 play.py --record run.dtd                     # ...and keep the keys
    python3 play.py --replay run.dtd                     # play them back
    python3 play.py --replay run.dtd --verify            # ...and prove it
    python3 play.py --colors braille                     # no colour, 4x the dots

Rendering is half-block by default: one terminal cell carries TWO pixels, an
upper U+2580 with the top pixel as its foreground and the bottom as its
background, so a given terminal shows twice the vertical resolution one
character per pixel can and shows it in colour.  `--colors braille' trades
the colour for another factor of four, putting 2x4 dithered dots in a cell.

Either way the frame is box-filtered into the grid in RGB -- averaging
palette *indices* would be meaningless, since index 200 is not "between" 100
and 250 in any sense the eye agrees with.

Nothing here is required to play: the engine's own ASCII path is still
there, and \\DTASC{n} is what gives it a viewport smaller than the render.
This is the front-end for when you have a terminal worth using.

Pure standard library, like everything else in this project.
"""
import argparse
import os
import re
import subprocess
import sys
import time

HEXLINE = re.compile(r"[0-9A-Fa-f]+")
STATEF = re.compile(r"(-?\d+)")

# The engine's own keys: move, turn, strafe, use, fire, quit, and Doom's
# number keys for the weapon.  Anything else it does not recognise is a frame
# in which the player does nothing, which is a perfectly good "wait".
ENGINE_KEYS = "wsadzcefq1234567"
WAIT_KEY = "."

BLOCK = "▀"          # UPPER HALF BLOCK
RESET = "\x1b[0m"
# Sixteen densities, brightest first -- the same ramp play.tex uses, so the
# mono renderer here and the engine's ASCII path agree about what "bright"
# looks like.
DENSITY = "@@NNWMoc*+=-:.. "

# How many pixels a character cell carries, by mode.  A cell is about twice
# as tall as it is wide, so every one of these divides it into square pixels
# and `fit' needs no per-mode fudge.
CELL = {"truecolor": (1, 2), "256": (1, 2), "mono": (1, 2), "braille": (2, 4)}

# U+2800 plus a bitmask.  The numbering is historical rather than sensible:
# the first three rows go down the left column then down the right, and the
# fourth row was bolted on afterwards -- hence 0x40 and 0x80 out of order.
DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

# A 4x4 ordered dither.  Braille is one bit a dot, and a plain 50% threshold
# on a game this dark gives two large black regions with a hard edge between
# them.  Dithering trades that edge for texture, which is what makes eight
# dots to a cell read as shading rather than as a stencil -- and being
# ordered rather than error-diffused, it does not smear when the camera
# moves, which on a moving picture matters more than the extra accuracy.
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


# Words of main memory to ask pdfTeX for beyond the five million it starts
# with.  Doom II's first few maps fit in the five; MAP09 composites 87 wall
# textures against MAP01's 40 and does not, and it dies part way through the
# load with "TeX capacity exceeded".
EXTRA_MEM = 30000000


def pdftex_command():
    """(argv, environment) for a pdftex with room for a large map.

    It has to be extra_mem_TOP.  TeX keeps token lists -- which is what every
    texel and every sprite column here is -- at the top of its memory and
    boxes at the bottom, and extra_mem_bot adds room only for boxes: a load
    that overflows with five million words overflows identically with
    forty-five, and the log then reports the unused forty as "used".

    Web2C reads the number from the environment, so setting the variable is
    enough on TeX Live.  MiKTeX wants it on the command line instead -- and
    TeX Live would refuse that option as unknown, hence the look at
    `--version' first.  A pdftex that cannot be run at all is reported by
    the caller, which is about to try for real.
    """
    env = dict(os.environ)
    env.setdefault("extra_mem_top", str(EXTRA_MEM))
    argv = ["pdftex"]
    try:
        about = subprocess.run(["pdftex", "--version"], capture_output=True,
                               text=True, errors="replace", timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        about = ""
    if "MiKTeX" in about:
        argv.append(f"--extra-mem-top={EXTRA_MEM}")
    return argv, env


# ---------------------------------------------------------------- terminal in
class Keyboard:
    """Single keypresses, no Enter.  A context manager because POSIX raw mode
    has to be put back however we leave."""

    def __init__(self, enabled=True):
        self.enabled = enabled and sys.stdin.isatty()
        self.fd = None
        self.saved = None

    def __enter__(self):
        if self.enabled and os.name != "nt":
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def key(self):
        """One key, mapped to what the engine understands.  None means quit."""
        raw = self._raw()
        if raw is None:
            return None
        return {
            "UP": "w", "DOWN": "s", "LEFT": "a", "RIGHT": "d",
            " ": "e", "\r": "e", "\n": "e",
            "\x1b": "q", "\x03": "q", "\x04": "q",
        }.get(raw, raw.lower())

    def _raw(self):
        if not self.enabled:
            return sys.stdin.readline()[:1] or None
        if os.name == "nt":
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return {"H": "UP", "P": "DOWN", "K": "LEFT",
                        "M": "RIGHT"}.get(msvcrt.getwch(), WAIT_KEY)
            return ch
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        # An escape ALONE is quit; an escape followed by [A..D is an arrow.
        # Reading the two bytes unconditionally would hang on the former, so
        # this peeks with a zero timeout.
        import select
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return "\x1b"
        if sys.stdin.read(1) != "[":
            return WAIT_KEY
        return {"A": "UP", "B": "DOWN", "D": "LEFT",
                "C": "RIGHT"}.get(sys.stdin.read(1), WAIT_KEY)


def vt_enable():
    """Windows consoles need to be told they speak ANSI.  Harmless elsewhere."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        for handle in (-11,):                      # STD_OUTPUT_HANDLE
            h = k.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)
        return True
    except Exception:
        return False


# -------------------------------------------------------------------- engine
class Engine:
    """A pdftex subprocess in interactive mode, talking the \\DTPIPE protocol.

    The protocol is four markers on the terminal stream: [DTPAL] once,
    then [DTS] state / [DTF] w h / hex / [DTE] per frame.  Two things about
    it are not obvious and both are load bearing.

    pdfTeX hard-wraps the terminal at max_print_line, mid-token, without
    inserting or dropping a character -- so a section is reassembled by
    deleting the newlines and never by reading a line at a time.  This is
    check.py's track() rule and the state line is long enough to need it.

    And this cannot deadlock on a full pipe buffer, which is the usual way
    a scheme like this dies: TeX flushes the terminal immediately before
    every blocking read, so by the time it is waiting for a key everything
    it wrote has left.  The prompt it writes -- `\\dt@key=' with no newline
    -- is what the NEXT line arrives glued to, which is why every marker is
    searched for anywhere in a line rather than at its start.
    """

    def __init__(self, job, args):
        self.job = job
        self.tex = job + ".tex"
        with open(self.tex, "w") as f:
            f.write(fixture(args))
        # Not -interaction=nonstopmode: under it \read16 gets EOF instead of
        # blocking and the key loop spins to its end in silence.
        argv, env = pdftex_command()
        try:
            self.proc = subprocess.Popen(
                argv + [self.tex], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, env=env)
        except FileNotFoundError:
            sys.exit("pdftex is not on PATH -- this drives the real engine, "
                     "it does not reimplement it")
        self.pal = None
        self.preamble = []
        # Called with each line the engine prints before the first frame.
        # play() sets it so that a slow load has something to show, and
        # clears it once frames start -- pdfTeX's own page markers share
        # this stream and would land in the middle of the picture.
        self.trace = None

    MARKS = ("[DTPAL]", "[DTS]", "[DTF]", "[DTE]")

    def frame(self):
        """The next (width, height, columns, state), or None at the end."""
        sec, buf, state, w, h, hexbuf = None, [], "", 0, 0, []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.rstrip("\r\n")
            if line.startswith("! "):
                return self._bail(line)
            mark = next((m for m in self.MARKS if m in line), None)
            if mark is None:
                if sec == "[DTF]":
                    s = line.strip()
                    # Only a line that is hex and nothing else: pdfTeX's own
                    # page markers -- [1], and the map file on page one --
                    # share this stream and contain digits.
                    if s and HEXLINE.fullmatch(s):
                        hexbuf.append(s)
                elif sec == "[DTPAL]":
                    # The palette is written once, before the first tick --
                    # so anything the engine SAYS during that tick arrives
                    # here, between the palette and the first [DTS]: a secret
                    # found on the spot the level starts, a warning, a
                    # fixture's own message.  The palette is the lines that
                    # are hex and nothing else, the same rule the frame data
                    # is read by; everything else is the engine talking.
                    s = line.strip()
                    if s and HEXLINE.fullmatch(s):
                        buf.append(s)
                    elif line:
                        self.preamble.append(line)
                        if self.trace:
                            self.trace(line)
                elif sec is not None:
                    buf.append(line)
                elif line:
                    self.preamble.append(line)
                    if self.trace:
                        self.trace(line)
                continue
            # A marker closes whatever section was open.  Joining with no
            # separator is what undoes max_print_line's wrap.
            if sec == "[DTPAL]":
                self.pal = unpal("".join(buf))
            elif sec == "[DTS]":
                state = "".join(buf)
            tail = line.split(mark, 1)[1]
            if mark == "[DTE]":
                return self._finish(w, h, hexbuf, state)
            if mark == "[DTF]":
                w, h = (int(v) for v in STATEF.findall(tail)[:2])
            sec, buf = mark, [tail]

    def _bail(self, line):
        r"""Give up on a TeX error rather than wait out its prompt.

        Not running under -interaction=nonstopmode is what lets \read16
        block for a key, and the price is that an error stops at `?' --
        written with no newline, so a reader waiting for a line is waiting
        on a prompt that is itself waiting on us.  Deadlock, and a silent
        one: the screen is already cleared and nothing more arrives.

        `x' is what a person would type there.  TeX closes the log and
        quits, the next read returns nothing, and errors() picks the
        failure up from the log, where it is legible.
        """
        # Answer first and read afterwards.  Going looking for the rest of
        # the error before answering is the same deadlock in miniature: the
        # prompt carries no newline, so a reader waiting for one waits on
        # it forever whenever the context runs shorter than expected.
        # Answered, TeX prints what is left and quits, and the read below
        # ends on the end of file that gives -- which is why it needs no
        # rule for recognising a prompt, only a bound in case TeX was
        # already in batchmode and there was no prompt at all.
        try:
            self.send("x")
        except Exception:
            pass
        sys.stderr.write("\r\n" + line + "\r\n")
        for _ in range(12):
            more = self.proc.stdout.readline()
            if not more:
                break
            sys.stderr.write(more.rstrip("\r\n") + "\r\n")
        sys.stderr.flush()
        return None

    def _finish(self, w, h, hexbuf, state):
        raw = bytes.fromhex("".join(hexbuf))
        if len(raw) != w * h:
            raise ValueError(f"frame is {len(raw)} bytes, expected {w * h}")
        cols = [raw[x * h:(x + 1) * h] for x in range(w)]
        return w, h, cols, state.strip()

    def send(self, key):
        self.proc.stdin.write(key + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.send("q")
        except Exception:
            pass
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=60)
        except Exception:
            self.proc.kill()

    def errors(self):
        try:
            log = open(self.job + ".log", errors="replace").read()
        except OSError:
            return -1
        # From the log, never from stdout: an error prompt under normal
        # interaction mode eats a line of stdin, and if that line is a key
        # TeX switches to batchmode and says nothing more on the terminal.
        return len(re.findall(r"^!", log, re.M))


def fixture(args):
    """The .tex the engine is actually run from.  Everything that can change
    what the run does has to be in here, because a demo replays by rebuilding
    this file and nothing else."""
    out = ["%% written by play.py -- regenerated on every run\n"]
    out.append("\\def\\DTPLAY{1}\\def\\DTPIPE{1}\n")
    out.append(f"\\def\\DTW{{{args.w}}}\\def\\DTH{{{args.h}}}"
               f"\\def\\DTPIX{{{args.pix}}}\n")
    if args.wad:
        out.append(f"\\def\\DTWAD{{{args.wad}}}\\def\\DTMAP{{{args.map}}}\n")
    for d in args.define or []:
        name, _, val = d.partition("=")
        out.append(f"\\def\\{name}{{{val or '1'}}}\n")
    out.append("\\input doom.tex\n")
    return "".join(out)


# Characters that mean something to TeX before they mean anything to the
# filesystem.  A backslash is the one that actually happens, because it is
# what shell completion on Windows hands you.
TEXBAD = r"\{}%#$&~^_ "


def wadpath(arg, base):
    r"""A WAD path the engine can be handed.

    \pdffiledump expands its filename as TeX tokens, so `.\doom2.wad' --
    which is exactly what tab completion in PowerShell produces -- makes
    \doom an undefined control sequence at load time.  Under the normal
    interaction mode this run needs, that stops at TeX's `?' prompt on a
    cleared screen and waits forever for a stdin we are holding: the
    failure reads as "loading a WAD hangs" and shows nothing at all.

    pdfTeX takes `/' as the separator on Windows too, so the fix is a
    rewrite.  Anything left that TeX would still eat is refused here,
    where saying so costs nothing, rather than in the middle of a load.
    """
    full = os.path.abspath(arg)
    if not os.path.isfile(full):
        sys.exit(f"no such WAD: {arg}")
    # Relative to the engine, since that is the directory it runs in, and a
    # short path is also one with fewer chances to contain a space.
    rel = os.path.relpath(full, base)
    out = (full if rel.startswith("..") else rel).replace(os.sep, "/")
    bad = sorted({c for c in out if c in TEXBAD})
    if bad:
        sys.exit(f"WAD path {out!r} contains {' '.join(repr(c) for c in bad)}, "
                 f"which TeX reads as markup -- put the WAD beside doom.tex, "
                 f"or somewhere without those in the path")
    return out


def unpal(hexstr):
    """768 hex bytes -> 256 (r, g, b).  The engine sends its own palette
    because it is the only thing that knows which one it is using: the
    built-in ramps until a WAD replaces them with PLAYPAL."""
    # Exactly 256 entries, and only whole bytes: the buffer is the palette
    # followed by whatever else looked like hex before the first frame, and
    # an odd digit on the end of it is not a reason to lose the picture.
    digits = "".join(HEXLINE.findall(hexstr))[:1536]
    raw = bytes.fromhex(digits[:len(digits) // 2 * 2])
    return [tuple(raw[3 * i:3 * i + 3]) for i in range(len(raw) // 3)]


# ------------------------------------------------------------------ rendering
def resample(cols, w, h, tw, th, pal):
    """Box-filter a w x h frame of palette indices into a tw x th RGB grid.

    In RGB, not in index space.  Doom's palette is not a gradient -- it is
    fourteen unrelated ramps -- so the mean of two indices names a colour
    with no relationship to either, and a downscaled frame comes out as
    confetti.  This is the same reason play.tex's ASCII table indexes by
    luma rather than by the low nibble.
    """
    xs = [(tx * w // tw, max(tx * w // tw + 1, (tx + 1) * w // tw))
          for tx in range(tw)]
    ys = [(ty * h // th, max(ty * h // th + 1, (ty + 1) * h // th))
          for ty in range(th)]
    out = []
    for y0, y1 in ys:
        row = []
        for x0, x1 in xs:
            r = g = b = 0
            n = (x1 - x0) * (y1 - y0)
            for x in range(x0, x1):
                col = cols[x]
                for y in range(y0, y1):
                    pr, pg, pb = pal[col[y]]
                    r += pr
                    g += pg
                    b += pb
            row.append((r // n, g // n, b // n))
        out.append(row)
    return out


def cube256(r, g, b):
    """xterm-256: the 6x6x6 colour cube, or the grey ramp when it is closer."""
    if abs(r - g) < 12 and abs(g - b) < 12:
        grey = 232 + min(23, (r * 24) // 256)
        return grey
    return 16 + 36 * (r * 6 // 256) + 6 * (g * 6 // 256) + (b * 6 // 256)


def luma(p):
    return (2 * p[0] + 5 * p[1] + p[2]) // 8


def paint_braille(grid):
    """Eight dots to a character cell, 2 across by 4 down.

    The highest resolution a terminal can reach in plain text: four times the
    pixels of a half block and eight times one character per pixel, with no
    colour at all and nothing but U+28xx on the wire.  One bit a dot, so it
    is dithered rather than thresholded.
    """
    out = []
    for cy in range(0, len(grid) - 3, 4):
        for cx in range(0, len(grid[0]) - 1, 2):
            bits = 0
            for dy in range(4):
                row = grid[cy + dy]
                for dx in range(2):
                    # Indexed by ABSOLUTE pixel position, not by position
                    # within the cell.  A cell is only two dots wide, so
                    # indexing locally would use the same two columns of the
                    # matrix everywhere and dither into vertical stripes --
                    # which is a pattern the eye finds immediately and reads
                    # as a rendering artefact rather than as shading.
                    if luma(row[cx + dx]) > BAYER[(cy + dy) % 4][(cx + dx) % 4] * 16 + 8:
                        bits |= DOTS[dy][dx]
            out.append(chr(0x2800 + bits))
        out.append("\r\n")
    return "".join(out)


def paint(grid, mode):
    """A half-block picture: two pixel rows to a terminal row.

    Colour codes are emitted only where they change.  On a 152x120 grid that
    is the difference between ~600 KB a frame and ~40 KB, which on a slow
    terminal is the difference between a game and a slideshow.
    """
    if mode == "braille":
        return paint_braille(grid)
    out = []
    for ty in range(0, len(grid) - 1, 2):
        top, bot = grid[ty], grid[ty + 1]
        fg = bg = None
        for x in range(len(top)):
            t, b = top[x], bot[x]
            if mode == "mono":
                # Both rows, not just the top one: a density character
                # cannot show two pixels, but it can be the mean of them,
                # and throwing the lower row away is what the engine's own
                # ASCII path stopped doing.
                out.append(DENSITY[15 - min(15, (luma(t) + luma(b)) // 32)])
                continue
            if t != fg:
                out.append(f"\x1b[38;2;{t[0]};{t[1]};{t[2]}m" if mode == "truecolor"
                           else f"\x1b[38;5;{cube256(*t)}m")
                fg = t
            if b != bg:
                out.append(f"\x1b[48;2;{b[0]};{b[1]};{b[2]}m" if mode == "truecolor"
                           else f"\x1b[48;5;{cube256(*b)}m")
                bg = b
            out.append(BLOCK)
        out.append(("" if mode == "mono" else RESET) + "\r\n")
    return "".join(out)


def fit(w, h, cols, rows, aspect, mode):
    """The largest pixel grid inside the terminal that keeps the frame's shape.

    Every mode divides a character cell into square pixels -- a cell is about
    twice as tall as it is wide, so 1x2 for a half block and 2x4 for Braille
    both come out square -- which is what lets this be one calculation rather
    than one per mode.  `aspect' is how much wider than tall a FRAMEBUFFER
    pixel is meant to be: 1.2 is Doom's own, a 320x200 render shown at 4:3.
    """
    cw, ch = CELL[mode]
    tw = max(2 * cw, cols * cw)
    th = max(2 * ch, (rows - 1) * ch)
    scale = min(tw / (w * aspect), th / h)
    # Trimmed to whole cells: a partial row of Braille has nothing to draw
    # its bottom dots from.
    return (max(cw, int(w * aspect * scale) // cw * cw),
            max(ch, int(h * scale) // ch * ch))


def hudline(state, extra=""):
    f = dict(zip(state.split()[0::2], state.split()[1::2]))
    # Invulnerability is the one power worth a word here: it is the only one
    # that changes what the player should DO, and the engine's own gold-eyed
    # face is on a status bar this viewport may be too small to read.
    god = "  GOD" if f.get("iv", "0") not in ("0", "?") else ""
    return (f"  hp {f.get('hp','?'):>3}  ar {f.get('ar','?'):>3}  "
            f"am {f.get('am','?'):>3}  wp {f.get('wp','?')}  "
            f"kills {f.get('kill','?')}/{f.get('of','?')}  "
            f"secrets {f.get('sc','?')}/{f.get('st','?')}{god}  "
            f"({f.get('x','?')},{f.get('y','?')}) sec {f.get('sec','?')}"
            f"   {extra}")


# ---------------------------------------------------------------------- demos
DEMO_MAGIC = "# doomtex demo v1"


def demo_write(path, args, rows):
    with open(path, "w") as f:
        f.write(DEMO_MAGIC + "\n")
        for k in ("wad", "map", "w", "h", "pix"):
            f.write(f"# {k} {getattr(args, k)}\n")
        for d in args.define or []:
            f.write(f"# define {d}\n")
        f.write("# ---- key<TAB>state, one line per frame\n")
        for key, state in rows:
            f.write(f"{key}\t{state}\n")


def demo_read(path):
    head, rows = {"define": []}, []
    with open(path) as f:
        if f.readline().strip() != DEMO_MAGIC:
            raise ValueError(f"{path}: not a doomtex demo")
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# ----") or not line:
                continue
            if line.startswith("# "):
                k, _, v = line[2:].partition(" ")
                if k == "define":
                    head["define"].append(v)
                else:
                    head[k] = v
                continue
            key, _, state = line.partition("\t")
            rows.append((key, state))
    return head, rows


# ----------------------------------------------------------------------- main
def play(args):
    eng = Engine(args.job, args)
    recorded = []
    demo = None
    if args.replay:
        _, demo = demo_read(args.replay)
    live = not args.verify and sys.stdout.isatty()
    # A console that will not speak ANSI gets the density ramp rather than a
    # screenful of escape sequences.  The old form of this line computed the
    # fallback and then ignored it.
    mode = args.colors
    if mode in ("truecolor", "256") and not vt_enable():
        mode = "mono"
    diverged = []
    n = 0

    # The screen is cleared when the first frame arrives, not before: a
    # WAD load is a minute or more of silence on a slow \pdffiledump, and
    # clearing first is what turns that wait into a blank screen with no
    # way to tell it from a hang.  The engine says what it is up to --
    # [wad] and [doom.tex] lines -- so let those through until then.
    if sys.stderr.isatty():
        def trace(line):
            sys.stderr.write(line.strip() + "\r\n")
            sys.stderr.flush()
        eng.trace = trace
    try:
        with Keyboard(enabled=demo is None) as kb:
            while True:
                got = eng.frame()
                if got is None:
                    break
                w, h, cols, state = got
                if n == 0:
                    eng.trace = None
                    if live:
                        sys.stdout.write("\x1b[2J\x1b[?25l")
                if demo is not None and n < len(demo):
                    want = demo[n][1]
                    if want and want != state:
                        diverged.append((n, want, state))
                if live:
                    tc = os.get_terminal_size()
                    tw, th = fit(w, h, tc.columns, tc.lines, args.aspect, mode)
                    grid = resample(cols, w, h, tw, th, eng.pal)
                    sys.stdout.write("\x1b[H" + paint(grid, mode)
                                     + hudline(state, f"frame {n}") + "\x1b[K")
                    sys.stdout.flush()

                if demo is not None:
                    if n >= len(demo):
                        break
                    key = demo[n][0]
                    if args.delay:
                        time.sleep(args.delay)
                else:
                    key = kb.key()
                    if key is None:
                        break
                recorded.append((key, state))
                n += 1
                if key == "q":
                    break
                eng.send(key if key in ENGINE_KEYS else WAIT_KEY)
    finally:
        if live:
            sys.stdout.write("\x1b[?25h" + RESET + "\r\n")
        eng.close()

    errs = eng.errors()
    # The engine's own last word on the level -- kills, items, secrets, and
    # whether it ended at the exit.  It is printed after the final frame, so
    # it is in the log rather than in anything frame() returned.
    try:
        log = open(args.job + ".log", errors="replace").read()
        tally = re.findall(r"\[doom\.tex\] tally: ([^\[]*)",
                           log.replace("\r", "").replace("\n", ""))
        if tally:
            print(tally[-1].strip())
    except OSError:
        pass
    print(f"{n} frames, {errs} TeX error(s); recording in {args.job}.pdf")
    if args.record:
        demo_write(args.record, args, recorded)
        print(f"demo written to {args.record} ({len(recorded)} keys)")
    if demo is not None:
        if diverged:
            print(f"REPLAY DIVERGED at {len(diverged)} frame(s); first:")
            i, want, got = diverged[0]
            print(f"  frame {i}\n  want {want}\n  got  {got}")
            return 1
        print(f"replay matched the recording on all {min(n, len(demo))} frames")
    return 1 if errs else 0


def main():
    ap = argparse.ArgumentParser(
        description="play doom.tex interactively, in colour",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="keys: w/s move, a/d turn, z/c strafe, e use, f fire, 1-7 "
               "weapon, q quit; arrows work too, space is use, Esc quits.")
    ap.add_argument("--wad", help="an IWAD or PWAD; omitted means the "
                                  "built-in three-room map")
    ap.add_argument("--map", default="MAP01")
    ap.add_argument("--w", type=int, default=152,
                    help="render width -- this is the PDF's resolution, not "
                         "the terminal's (default 152)")
    ap.add_argument("--h", type=int, default=120, help="render height")
    ap.add_argument("--pix", type=int, default=4, help="PDF points per pixel")
    ap.add_argument("--job", default="dtplay", help="job name; the recording "
                                                    "lands in <job>.pdf")
    ap.add_argument("--define", action="append", metavar="NAME[=VAL]",
                    help="a raw \\def, e.g. --define DTNOMON --define DTX=100")
    ap.add_argument("--colors", choices=("truecolor", "256", "mono",
                                         "braille"),
                    default="truecolor",
                    help="truecolor and 256 draw half blocks in colour; mono "
                         "is the density ramp; braille is 2x4 dots a cell, "
                         "the highest resolution plain text reaches")
    ap.add_argument("--aspect", type=float, default=1.0,
                    help="framebuffer pixel aspect; 1.2 is Doom's own")
    ap.add_argument("--record", metavar="FILE", help="write the key stream")
    ap.add_argument("--replay", metavar="FILE", help="play a recorded one back")
    ap.add_argument("--verify", action="store_true",
                    help="with --replay: no drawing, just check it reproduces")
    ap.add_argument("--delay", type=float, default=0.08,
                    help="with --replay: seconds a frame (0 for as fast as it "
                         "goes)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    if args.wad:
        args.wad = wadpath(args.wad, here)
    os.chdir(here)
    if args.replay:
        head, _ = demo_read(args.replay)
        # The fixture is rebuilt from the demo's own header, because a replay
        # against a different map is not a replay.  A recorded key stream is
        # only meaningful beside the world it was recorded in.
        args.wad = head.get("wad") if head.get("wad") != "None" else None
        # Normalised here too, so a demo recorded before play.py started
        # doing that -- its header holding whatever the shell handed over --
        # replays instead of stopping at the same prompt.
        if args.wad:
            args.wad = wadpath(args.wad, here)
        args.map = head.get("map", args.map)
        args.w, args.h = int(head.get("w", args.w)), int(head.get("h", args.h))
        args.pix = int(head.get("pix", args.pix))
        args.define = head.get("define") or None
    return play(args)


if __name__ == "__main__":
    sys.exit(main())
