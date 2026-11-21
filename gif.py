#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""Export a rendered PDF to an animated GIF.

The renderer writes each column as an indexed inline image, so the frames
come back out of the PDF exactly -- the same decode dump.py does -- and a
GIF is an indexed format too.  There is no resampling and no quantisation
anywhere in this file: a palette index goes in and the same index comes out,
so the GIF is the render, not a picture of it.

    python3 gif.py doom.pdf out.gif
    python3 gif.py doom.pdf out.gif --scale 3 --delay 7
    python3 gif.py dtplay.pdf play.gif --frames 4:40

The palette is read out of the PDF as well.  \\dtSetupPDF writes it as an
/Indexed /DeviceRGB object, so it is always the palette that frame was
actually drawn with -- the built-in ramps without a WAD and PLAYPAL with
one -- and there is nothing to pass on the command line and nothing to get
wrong.

Pure standard library.  GIF's LZW is 1984 vintage and about forty lines.
"""
import argparse
import re
import sys
import zlib


# --------------------------------------------------------------- reading in
def frames(pdf):
    """Decode a PDF into a list of frames, each a list of column bytestrings.

    Shared in spirit with dump.py and check.py: every page is one content
    stream of \\DTW inline images, each a single column of \\DTH bytes.
    """
    d = open(pdf, "rb").read()
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
        if cols:
            out.append([bytes.fromhex(c.replace(b"\n", b"").replace(b" ", b"")
                                      .decode()) for c in cols])
    return out


def palette(pdf):
    """The 256 RGB triples the PDF says it was drawn with, or None."""
    d = open(pdf, "rb").read()
    m = re.search(rb"/Indexed\s*/DeviceRGB\s*255\s*<([0-9A-Fa-f\s]+)>", d)
    if not m:
        return None
    raw = bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode())
    return [tuple(raw[3 * i:3 * i + 3]) for i in range(256)]


# ---------------------------------------------------------------------- LZW
class Bits:
    """GIF packs codes least-significant-bit first, across byte boundaries."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, code, size):
        self.acc |= code << self.n
        self.n += size
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def done(self):
        if self.n:
            self.out.append(self.acc & 0xFF)
            self.n = 0
        return bytes(self.out)


def lzw(data, mcs=8):
    """GIF's variable-width LZW.

    The one hard part is when the codes widen, because **the decoder runs
    exactly one code behind the encoder** and the two have to widen on the
    same code anyway.  The encoder adds a table entry immediately after
    emitting a code; the decoder cannot add its entry until it has read the
    FOLLOWING code, since the entry it must build ends with that code's first
    character.  So after k codes the encoder holds one entry more than the
    decoder does, and an encoder that widens the moment its own next code
    reaches 1<<size widens one code early: every code from there on is read
    at the wrong width and the picture is confetti from that byte onward.

    Hence `+ 1'.  Nothing about the output looks wrong until a decoder that
    is not this file's tries to read it, which is why gif.py is checked
    against a foreign decoder and not against a matching mistake.
    """
    clear, end = 1 << mcs, (1 << mcs) + 1
    size = mcs + 1
    table = {bytes([i]): i for i in range(clear)}
    nxt = end + 1
    bits = Bits()
    bits.put(clear, size)
    buf = b""
    for ch in data:
        c = bytes((ch,))
        if buf + c in table:
            buf += c
            continue
        bits.put(table[buf], size)
        if nxt == 4096:
            bits.put(clear, size)
            table = {bytes([i]): i for i in range(clear)}
            nxt, size = end + 1, mcs + 1
        else:
            table[buf + c] = nxt
            nxt += 1
            if nxt == (1 << size) + 1 and size < 12:
                size += 1
        buf = c
    if buf:
        bits.put(table[buf], size)
    bits.put(end, size)
    return bits.done()


def blocks(data):
    """GIF carries data in sub-blocks of at most 255 bytes, then a zero."""
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


# --------------------------------------------------------------- writing out
def rows_of(frame, w, h, scale):
    """The PDF stores columns; a GIF stores rows.  Nearest-neighbour scale,
    because anything else would invent colours that are not in the palette
    and this file's whole claim is that it invents nothing."""
    out = []
    for y in range(h):
        row = bytes(frame[x][y] for x in range(w))
        if scale > 1:
            row = bytes(b for b in row for _ in range(scale))
        out.extend([row] * scale)
    return b"".join(out)


def spare_index(fr):
    """A palette index no frame uses, or None.

    Consecutive frames of a game where the camera is not moving differ in
    almost nothing, so marking the unchanged pixels transparent and leaving
    the previous frame under them costs one index and shrinks the file by
    most of itself.  It is only available if some index really is unused --
    claiming one that is in the picture would punch holes in the walls.
    """
    seen = set()
    for f in fr:
        for col in f:
            seen |= set(col)
            if len(seen) == 256:
                return None
    return next(i for i in range(256) if i not in seen)


def write_gif(path, fr, pal, scale, delay, loop, diff):
    w, h = len(fr[0]), len(fr[0][0])
    W, H = w * scale, h * scale
    trans = spare_index(fr) if diff and len(fr) > 1 else None

    out = bytearray(b"GIF89a")
    out += bytes((W & 0xFF, W >> 8, H & 0xFF, H >> 8))
    out += bytes((0xF7, 0, 0))                     # global table, 256 entries
    for r, g, b in pal:
        out += bytes((r, g, b))
    # Unconditionally, and 0 is the interesting value: a GIF with no Netscape
    # block plays once, and 0 inside one means forever.  Writing the block
    # only `if loop' therefore does the exact opposite of what --loop 0 asks
    # for, which is also the default.
    out += (b"\x21\xFF\x0BNETSCAPE2.0\x03\x01"
            + bytes((loop & 0xFF, loop >> 8)) + b"\x00")

    prev = None
    used = 0
    for f in fr:
        cur = rows_of(f, w, h, scale)
        whole = lzw(cur, 8)
        body, clear = whole, False
        if prev is not None and trans is not None:
            held = bytes(trans if a == b else a
                         for a, b in zip(cur, prev))
            # Per frame, and by measuring rather than by assuming.  Holding
            # the unchanged pixels is a large win when the camera is still
            # and a LOSS when it is turning: substituting one index for many
            # different ones breaks up exactly the runs LZW was living on,
            # and a whole-frame turn came out 20% BIGGER as a diff.  Both
            # encodings are cheap next to everything else here, so take the
            # smaller and do not guess.
            alt = lzw(held, 8)
            if len(alt) < len(whole):
                body, clear, used = alt, True, used + 1
        # Disposal 1 (leave it in place) is what makes a transparent pixel
        # mean "whatever was already there"; disposal 2 would clear it to the
        # background and every held pixel would show as a hole.
        flags = (1 << 2) | (1 if clear else 0)
        out += (b"\x21\xF9\x04" + bytes((flags, delay & 0xFF, delay >> 8,
                                         trans if clear else 0)) + b"\x00")
        out += b"\x2C" + bytes((0, 0, 0, 0, W & 0xFF, W >> 8, H & 0xFF, H >> 8,
                                0))
        out += bytes((8,)) + blocks(body)
        prev = cur
    out += b"\x3B"
    open(path, "wb").write(bytes(out))
    return W, H, trans, used


def main():
    ap = argparse.ArgumentParser(
        description="export a doom.tex PDF recording to an animated GIF")
    ap.add_argument("pdf")
    ap.add_argument("gif")
    ap.add_argument("--scale", type=int, default=2,
                    help="integer pixel scale (default 2)")
    ap.add_argument("--delay", type=int, default=8,
                    help="hundredths of a second a frame (default 8)")
    ap.add_argument("--frames", default=":",
                    help="a python-style slice, e.g. 4:40 or ::2")
    ap.add_argument("--loop", type=int, default=0,
                    help="Netscape loop count; 0 is forever")
    ap.add_argument("--no-diff", action="store_true",
                    help="write every frame whole rather than only what moved")
    a = ap.parse_args()

    fr = frames(a.pdf)
    if not fr:
        sys.exit(f"no frames decoded from {a.pdf}")
    pal = palette(a.pdf)
    if pal is None:
        sys.exit(f"{a.pdf} carries no /Indexed palette -- is it a doom.tex "
                 f"render?")
    sl = slice(*[int(p) if p else None for p in a.frames.split(":")])
    fr = fr[sl]
    if not fr:
        sys.exit(f"--frames {a.frames} selected none of {len(frames(a.pdf))}")
    if len({(len(f), len(f[0])) for f in fr}) != 1:
        sys.exit("frames are not all the same size")

    W, H, trans, used = write_gif(a.gif, fr, pal, a.scale, a.delay, a.loop,
                                  not a.no_diff)
    import os
    if a.no_diff:
        how = "whole frames (--no-diff)"
    elif trans is None:
        how = "whole frames: every palette index is in use, none to spare"
    else:
        how = (f"{used} of {len(fr)} frames held on index {trans}, "
               f"{len(fr) - used} whole")
    print(f"{a.gif}: {len(fr)} frames, {W}x{H}, "
          f"{os.path.getsize(a.gif) / 1024:.0f} KB, {how}")


if __name__ == "__main__":
    main()
