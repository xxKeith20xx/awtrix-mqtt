"""
Generate 8x8 GIF icons for the Awtrix /ICONS folder.

Pure standard library (no Pillow) -- writes GIF87a files with a 4-color
global palette, matching the existing icons in icons/ (black = unlit pixel
on the LED matrix, no transparency block needed).

Each icon is defined as an 8x8 grid of palette indices (0-3) plus its
4-color palette. Add new icons to ICONS and re-run this script.
"""
import struct


def _lzw_encode(indices, min_code_size):
    clear_code = 1 << min_code_size
    end_code = clear_code + 1
    code_size = min_code_size + 1
    next_code = end_code + 1
    table = {(i,): i for i in range(clear_code)}

    codes = [(clear_code, code_size)]
    w = ()
    for px in indices:
        wp = w + (px,)
        if wp in table:
            w = wp
            continue
        codes.append((table[w], code_size))
        if next_code < 4096:
            table[wp] = next_code
            next_code += 1
            if next_code == (1 << code_size) + 1 and code_size < 12:
                code_size += 1
        else:
            codes.append((clear_code, code_size))
            table = {(i,): i for i in range(clear_code)}
            next_code = end_code + 1
            code_size = min_code_size + 1
        w = (px,)
    if w:
        codes.append((table[w], code_size))
    codes.append((end_code, code_size))

    bitstream, bitcount = 0, 0
    out = bytearray()
    for code, size in codes:
        bitstream |= code << bitcount
        bitcount += size
        while bitcount >= 8:
            out.append(bitstream & 0xFF)
            bitstream >>= 8
            bitcount -= 8
    if bitcount:
        out.append(bitstream & 0xFF)
    return bytes(out)


def write_gif(path, width, height, indices, palette):
    """palette: list of (r,g,b), length a power of two (>=4)."""
    bits = max(2, (len(palette) - 1).bit_length())
    n_colors = 1 << bits
    pal = list(palette) + [(0, 0, 0)] * (n_colors - len(palette))

    out = bytearray(b"GIF87a")
    out += struct.pack("<HH", width, height)
    out.append(0x80 | ((bits - 1) << 4) | (bits - 1))  # global color table
    out += bytes((0, 0))  # bg color index, pixel aspect ratio
    for r, g, b in pal:
        out += bytes((r, g, b))

    out.append(0x2C)  # image descriptor
    out += struct.pack("<HHHH", 0, 0, width, height)
    out.append(0)  # no local color table

    out.append(bits)  # LZW min code size
    data = _lzw_encode(indices, bits)
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)  # block terminator
    out.append(0x3B)  # trailer

    with open(path, "wb") as f:
        f.write(out)


# --- Icon definitions -------------------------------------------------------
# Mercury: a shaded sphere (sunlit/tan vs. shadowed/grey-brown half), like a
# small cratered planet. 0=sunlit, 1/2=shadow, 3=black (unlit pixel).
MERCURY_GRID = [
    3, 3, 0, 0, 1, 1, 3, 3,
    3, 0, 0, 0, 1, 1, 1, 3,
    0, 0, 0, 0, 1, 1, 1, 1,
    0, 0, 0, 0, 1, 1, 1, 1,
    0, 0, 0, 0, 1, 1, 1, 1,
    0, 0, 0, 0, 1, 1, 1, 1,
    3, 0, 0, 0, 1, 1, 1, 3,
    3, 3, 0, 0, 1, 1, 3, 3,
]
MERCURY_PALETTE = [(200, 185, 165), (110, 100, 95), (110, 100, 95), (0, 0, 0)]

ICONS = {
    "mercury": (MERCURY_GRID, MERCURY_PALETTE),
}


if __name__ == "__main__":
    import os
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    for name, (grid, palette) in ICONS.items():
        path = os.path.join(out_dir, f"{name}.gif")
        write_gif(path, 8, 8, grid, palette)
        print(f"wrote {path}")
