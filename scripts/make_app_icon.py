"""Create the Windows ICO from the app's tiny vector-style brand mark."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "app.ico"
SIZE = 256


def rounded_rect(x: float, y: float, left: float, top: float, right: float,
                 bottom: float, radius: float) -> bool:
    nearest_x = min(max(x, left + radius), right - radius)
    nearest_y = min(max(y, top + radius), bottom - radius)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius ** 2


def png_chunk(name: bytes, payload: bytes) -> bytes:
    body = name + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def render_png() -> bytes:
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            px, py = x + 0.5, y + 0.5
            if rounded_rect(px, py, 10, 10, 246, 246, 48):
                color = (12, 24, 39, 255)
            else:
                color = (0, 0, 0, 0)
            if rounded_rect(px, py, 56, 42, 111, 214, 12) or rounded_rect(px, py, 56, 156, 207, 214, 12):
                color = (101, 216, 255, 255)
            if (px - 187) ** 2 + (py - 67) ** 2 <= 15 ** 2:
                color = (244, 248, 252, 255)
            rows.extend(color)
    header = struct.pack(">2I5I", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
            + png_chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + png_chunk(b"IEND", b""))


def main() -> None:
    payload = render_png()
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(payload), 22)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(directory + entry + payload)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
