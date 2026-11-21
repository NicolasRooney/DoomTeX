#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""
Rebuild test.wad -- the three-room PWAD that check.py diffs against the
built-in level.

    python3 maketest.py         # writes test.wad in the current directory

test.wad is in the repository and this is where it came from.  It is not
arbitrary: check.py compares the render of this WAD against the render of
doom.tex's hand-written \\dtBuiltinMap, so the two must describe the *same*
level.  Everything below is therefore transcribed from \\dtBuiltinMap in
doom.tex, not invented, and has to be changed with it.

Two details make the comparison work:

  * Sector ramps are not stored in a WAD.  wad.tex assigns them from
    \\dtRampW/F/C indexed by (sector index mod 11), whose first three entries
    are chosen to reproduce the built-in map's own ramps -- so sectors must
    keep their original order.
  * \\DTROOT is set to NNODE-1 on the WAD path, so the root node must be last.

No THINGS lump: the built-in map places no things, and wad.tex leaves
\\PX/\\PY/\\PA at their defaults (100, 256, 0) when THINGS is absent, which is
exactly where demo.tex starts.
"""
import struct

# ---------------------------------------------------------------- level data
# \dtAddVert
VERTS = [(0, 0), (0, 512), (384, 512), (384, 352), (384, 160), (384, 0),
         (640, 512), (640, 352), (640, 160), (640, 0),
         (1024, 512), (1024, 0)]

# \dtAddSector: floor, ceiling, light
SECTORS = [(0, 128, 224), (24, 96, 176), (-16, 144, 128)]

# \dtAddSeg: v1, v2, linedef, side -- grouped by subsector
SEGS = [
    # subsector 0: west room
    (0, 1, 0, 0), (1, 2, 1, 0), (2, 3, 2, 0),
    (3, 4, 3, 0), (4, 5, 4, 0), (5, 0, 5, 0),
    # subsector 1: middle room; seg 7 is the west portal, back side
    (5, 4, 6, 0), (4, 3, 3, 1), (3, 2, 7, 0),
    (2, 6, 8, 0), (6, 7, 9, 0), (7, 8, 10, 0),
    (8, 9, 11, 0), (9, 5, 12, 0),
    # subsector 2: east room; seg 15 is the east portal, back side
    (9, 8, 13, 0), (8, 7, 10, 1), (7, 6, 14, 0),
    (6, 10, 15, 0), (10, 11, 16, 0), (11, 9, 17, 0),
]

# \dtAddSSec: seg count, first seg
SSECTORS = [(6, 0), (8, 6), (6, 14)]

# \dtAddNode: x, y, dx, dy, right child, left child
NODES = [(384, 0, 0, 512, 32769, 32768),
         (640, 0, 0, 512, 32770, 0)]

# \dtAddLine: front sector, back sector (-1 = one-sided)
LINE_SECTORS = [(0, -1), (0, -1), (0, -1), (0, 1), (0, -1), (0, -1),
                (1, -1), (1, -1), (1, -1), (1, -1), (1, 2), (1, -1), (1, -1),
                (2, -1), (2, -1), (2, -1), (2, -1), (2, -1)]

# Each linedef's own vertices, recovered from the segs that reference it.
LINE_VERTS = {}
for a, b, ld, side in SEGS:
    if side == 0:
        LINE_VERTS[ld] = (a, b)
assert len(LINE_VERTS) == len(LINE_SECTORS), "every linedef needs a front seg"

# ------------------------------------------------------------ sidedef layout
# One sidedef per linedef front, then one per portal back.  wad.tex recovers a
# linedef's sectors by dereferencing these, which is the only reason they exist.
side_sectors = [front for front, _ in LINE_SECTORS]
line_sides = []
for i, (front, back) in enumerate(LINE_SECTORS):
    if back < 0:
        line_sides.append((i, 0xFFFF))
    else:
        line_sides.append((i, len(side_sectors)))
        side_sectors.append(back)

# ------------------------------------------------------------------- packing
TWOSIDED = 0x0004


def name8(s):
    return s.encode("ascii").ljust(8, b"\0")


def lump_vertexes():
    return b"".join(struct.pack("<hh", x, y) for x, y in VERTS)


def lump_sectors():
    out = b""
    for floor, ceil, light in SECTORS:
        out += struct.pack("<hh", floor, ceil)
        out += name8("FLOOR4_8")[:8] + name8("CEIL3_5")[:8]
        out += struct.pack("<hhh", light, 0, 0)
    return out


def lump_sidedefs():
    out = b""
    for sec in side_sectors:
        out += struct.pack("<hh", 0, 0)
        out += name8("-") + name8("-") + name8("STARTAN3")
        out += struct.pack("<H", sec)
    return out


def lump_linedefs():
    out = b""
    for i, (front, back) in enumerate(LINE_SECTORS):
        v1, v2 = LINE_VERTS[i]
        right, left = line_sides[i]
        flags = TWOSIDED if back >= 0 else 0x0001  # IMPASSABLE on solid walls
        out += struct.pack("<HHHHHHH", v1, v2, flags, 0, 0, right, left)
    return out


def bams(v1, v2):
    """Doom stores a seg's angle as a BAM.  Nothing here reads it, but a
    malformed one would make the file wrong for any other tool."""
    import math
    (x1, y1), (x2, y2) = VERTS[v1], VERTS[v2]
    a = math.atan2(y2 - y1, x2 - x1) / (2 * math.pi)
    return int(round(a * 65536)) & 0xFFFF


def lump_segs():
    out = b""
    for v1, v2, ld, side in SEGS:
        out += struct.pack("<HHHHHh", v1, v2, bams(v1, v2), ld, side, 0)
    return out


def lump_ssectors():
    return b"".join(struct.pack("<HH", n, f) for n, f in SSECTORS)


def lump_nodes():
    out = b""
    for x, y, dx, dy, rc, lc in NODES:
        out += struct.pack("<hhhh", x, y, dx, dy)
        # bounding boxes: top, bottom, left, right.  Skipped by wad.tex, which
        # prunes on the open-column count instead of CheckBBox.
        out += struct.pack("<hhhh", 512, 0, 0, 1024)
        out += struct.pack("<hhhh", 512, 0, 0, 1024)
        out += struct.pack("<HH", rc, lc)
    return out


LUMPS = [
    ("E1M1", b""),
    ("LINEDEFS", lump_linedefs()),
    ("SIDEDEFS", lump_sidedefs()),
    ("VERTEXES", lump_vertexes()),
    ("SEGS", lump_segs()),
    ("SSECTORS", lump_ssectors()),
    ("NODES", lump_nodes()),
    ("SECTORS", lump_sectors()),
]


def build(path="test.wad"):
    data = b"PWAD" + struct.pack("<ii", len(LUMPS), 0)
    entries = []
    for nm, payload in LUMPS:
        entries.append((len(data), len(payload), nm))
        data += payload
    dirofs = len(data)
    for pos, size, nm in entries:
        data += struct.pack("<ii", pos, size) + name8(nm)
    data = data[:8] + struct.pack("<i", dirofs) + data[12:]
    open(path, "wb").write(data)

    print(f"{path}: {len(data)} bytes, {len(LUMPS)} lumps")
    for pos, size, nm in entries:
        print(f"   {nm:<10} {size:>5} bytes at {pos}")
    print(f"   {len(VERTS)} verts, {len(LINE_SECTORS)} lines, "
          f"{len(side_sectors)} sides, {len(SECTORS)} sectors, "
          f"{len(SEGS)} segs, {len(SSECTORS)} subsectors, {len(NODES)} nodes")


if __name__ == "__main__":
    build()
