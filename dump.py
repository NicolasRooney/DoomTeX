#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""Print or export a frame from a rendered PDF.

The renderer writes each column as an indexed inline image, so a frame can be
read back exactly without rendering the PDF.  Decoding it is far more
diagnostic than looking at the picture:

    python3 dump.py out.pdf                 # ASCII by brightness
    python3 dump.py out.pdf --ramp          # ASCII by palette ROW
    python3 dump.py out.pdf --frame 3
    python3 dump.py out.pdf --png f.png

--ramp keys on which palette row produced each pixel, i.e. on *which surface*
drew it, which is what tells a wall from a floor from a sprite.

The colours come out of the PDF, which carries the palette it was drawn with.
--wad overrides that with a WAD's PLAYPAL and is kept for the case where you
want to see a render through a different WAD's eyes; it is no longer needed
to get the right ones.  For a whole recording rather than a frame, gif.py.
"""
import argparse
import re
import struct
import sys
import zlib

SHADES = " .:-=+*#%@"
RAMPCH = "0123456789ABCDEF"


def frames(pdf):
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


def playpal(wad):
    d = open(wad, "rb").read()
    n, off = struct.unpack("<ii", d[4:12])
    for i in range(n):
        pos, _, name = struct.unpack("<ii8s", d[off + 16 * i:off + 16 * i + 16])
        if name.rstrip(b"\0") == b"PLAYPAL":
            return d[pos:pos + 768]
    return None


def pdfpal(pdf):
    """The palette out of the PDF itself.

    \\dtSetupPDF writes it as an /Indexed /DeviceRGB object, so it is always
    the one that render was actually drawn with -- the built-in ramps without
    a WAD and PLAYPAL with one.  --wad predates this and is still honoured,
    but it can only ever supply the second of those, and it can supply the
    wrong WAD's.
    """
    d = open(pdf, "rb").read()
    m = re.search(rb"/Indexed\s*/DeviceRGB\s*255\s*<([0-9A-Fa-f\s]+)>", d)
    return bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode()) if m else None


def png(path, frame, pal, scale=3):
    w, h = len(frame), len(frame[0])
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            b = frame[x][y]
            rgb = pal[3 * b:3 * b + 3] if pal else bytes((b, b, b))
            row += rgb * scale
        for _ in range(scale):
            rows.append(b"\0" + bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    hdr = struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr)
                           + chunk(b"IDAT", zlib.compress(raw))
                           + chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--ramp", action="store_true")
    ap.add_argument("--png")
    ap.add_argument("--wad")
    ap.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()

    fr = frames(a.pdf)
    if not fr:
        sys.exit("no frames decoded from " + a.pdf)
    if a.frame >= len(fr):
        sys.exit(f"only {len(fr)} frames")
    f = fr[a.frame]
    pal = playpal(a.wad) if a.wad else pdfpal(a.pdf)

    if a.png:
        png(a.png, f, pal, a.scale)
        print(f"{a.png}: {len(f)}x{len(f[0])} from frame {a.frame} of {len(fr)}")
        return

    w, h = len(f), len(f[0])
    lum = None
    if pal:
        lum = [0.3 * pal[3 * i] + 0.59 * pal[3 * i + 1] + 0.11 * pal[3 * i + 2]
               for i in range(256)]
    for y in range(h):
        line = []
        for x in range(w):
            b = f[x][y]
            if a.ramp:
                line.append(RAMPCH[b // 16])
            elif lum:
                line.append(SHADES[min(9, int(lum[b] / 25.6))])
            else:
                line.append(SHADES[min(9, (b % 16) * 10 // 16)])
        print("".join(line))
    print(f"[frame {a.frame} of {len(fr)}, {w}x{h}]")


if __name__ == "__main__":
    main()
