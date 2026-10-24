#!/usr/bin/env python3
"""Build a single AutoRun: False Color / EL Zone toggle, plus the camera's own
EL Zone and False Color legends (fp-ELZone-FalseColor-toggle-legends).

One file, and nothing in it but this. No menu, no key hook, no panel, no gyro,
no USB shell, no recording changes. The only button involved is the one the
camera's own Custom Button Functions menu already points at False Color.

    short release: off -> colours, legend as last left (hidden after boot);
                   on -> off, legend state remembered
    hold (0.5 second, before release): off -> both; colours <-> both

    ./tools/build_fcscale_autorun.py [--fast-echo] [--out DIR]

The scale's colours, band positions and label glyphs are read out of the
verified MAIN image here and written to src/fcscale_native.inc.S before the
assembly is built, so the card draws exactly what the firmware draws.

RAM only. Remove AutoRun.txt and cold boot to revert.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'reference/fpSup/fp_usb_shell')]
from armasm import assemble, symbols

BASE = 0xC0000000
MAIN = ROOT / 'analysis/MAIN_c0000000.bin'
MAIN_SHA = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
NATIVE_INC = ROOT / 'src/fcscale_native.inc.S'

# Free worker-region caves, checked zero in the stock image at build time.
STATE = 0xC072E200
STATE_SIZE = 0xDC
CODE = 0xC072E400

PRESS_SITE = 0xC03722E8         # CameraIF vtable +0xCC
RELEASE_SITE = 0xC0372330       # vtable +0xD0
SITE_STOCK = 0xE92D4010         # push {r4, lr}, in both
SUBMIT_SITE = 0xC02E8A08        # display submit(controller, descriptor, sub)
BOOT_SITE = 0xC037172C
BOOT_STOCK = 0xE8BD8010
SUBMIT_STOCK = 0xE92D48F0       # push {r4, r5, r6, r7, fp, lr}, replayed by fc_submit

# How an AutoRun runs code: the echo command's handler pointer is repointed,
# `echo` is issued, and the stock handler is put straight back. Same slot the
# fpSup loader itself uses.
ECHO_SLOT = 0xC0BAC2F8
ECHO_ORIG = 0xC03D99A0
CACHE_FN = 0xC000E91C
ICACHE_FN = 0xC000EABC          # I-cache invalidate; fpSup: D-clean then this, before new code runs
AUTORUN_MAX = 48 * 1024        # see the size note in build()

# The shell sleeps ~1.9 ms after every visible character it reads, through
# this one call in its line editor (bl 0xC0421770, delay(1)). NOPing it for
# the length of the card is fpSup's "faster start" (Andy_hal9000's tip,
# fpSup 7709b25, measured 11.5 s -> 3.0 s; AUTORUN_CHAR_DELAY.md). The stock
# word is put back three times, as fpSup does, since it must not be missed.
ECHO_SLEEP_AT = 0xC04215F4
ECHO_SLEEP_STOCK = 0xEB00005D
ARM_NOP = 0xE320F000

# The firmware's own EL Zone scale (FalseColorBarDrawer, 0xC057DB98).
BAND_TABLE = 0xC0D1B74C         # 15 x {u16 x0, u16 x1 inclusive, u32 palette index}
BAND_N = 15
NATIVE_PAL = 0xC0D1B34C         # 256 x {u8 alpha, u8 Y, s8 U, s8 V}
NATIVE_PAL_N = 256
GLYPH_H = 25
# The main layer's 16-bit pixel: alpha in the top nibble (0xC052A528), three
# 4-bit channels below. The order of those three is not established from the
# firmware; ARGB matches the layer's class name (DisplayOsdLayer_ARGB) and is
# confirmed or flipped by looking at the camera.
PIXEL_ORDER = 'ARGB'
GLYPH_SIGN_W = 22
# The label glyphs of scene B5_9's ElZoneScale (object 33212): XCI images in
# MAIN, in the order the label table below indexes them.
GLYPHS = [('minus', 0xC14405C4), ('plus', 0xC1440624), ('half', 0xC143FA68),
          ('zero', 0xC143DE10), ('one', 0xC143DF6C), ('two', 0xC143E15C),
          ('three', 0xC143E420), ('four', 0xC143E714), ('five', 0xC143E978),
          ('six', 0xC143EAC0),
          # FalseColorScale (object 33211) adds these. Identified by size
          # (point 6x25, gray 116x26, stop 81x25) and by rendering 8 and 9.
          ('point', 0xC14401FC), ('eight', 0xC143ED54), ('nine', 0xC143EEE8),
          ('gray', 0xC143F350), ('stop', 0xC143FD2C)]
# "%Gray" is 26 rows placed one row higher (y=29) than the 25-row labels
# (y=30); its first row is blank, so dropping it aligns it with the rest.
GLYPH_TOP_CROP = {'gray': 1}
NONE = 0xFF

# The firmware's own False Color scale: FalseColorBarDrawer setup (0xC057DB40)
# takes style 0 from 0xC005DBD8 as type 1, which draws 180 entries from
# 0xC0D1B7C4; any other style is type 2, the 15 EL Zone bands above.
FC_BAND_TABLE = 0xC0D1B7C4
FC_BAND_N_RAW = 180
# FalseColorScale's labels, one glyph each: x = object x + image rect x.
FC_LABELS = [(6, 'zero'),
             (61, 'two'), (79, 'point'), (85, 'five'),
             (335, 'one'), (353, 'eight'), (371, 'gray'),
             (520, 'plus'), (542, 'one'), (560, 'stop'),
             (914, 'nine'), (932, 'nine'),
             (964, 'one'), (982, 'zero'), (1000, 'zero')]
# Label x positions from the same scene, left to right; each is a sign glyph
# followed by a numeral or the half glyph, except the bare zero.
LABELS = [(14, 'minus', 'six'), (82, 'minus', 'five'), (150, 'minus', 'four'),
          (218, 'minus', 'three'), (286, 'minus', 'two'), (354, 'minus', 'one'),
          (410, 'minus', 'half'), (503, 'zero', None), (548, 'plus', 'half'),
          (630, 'plus', 'one'), (698, 'plus', 'two'), (766, 'plus', 'three'),
          (834, 'plus', 'four'), (902, 'plus', 'five'), (970, 'plus', 'six')]


def word(image: bytes, address: int) -> int:
    return struct.unpack_from('<I', image, address - BASE)[0]


def branch(site: int, target: int) -> int:
    offset = target - site - 8
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise SystemExit('branch out of range')
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


# ------------------------------------------------------------ the native data

def lz4_block(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        literal = token >> 4
        if literal == 15:
            while True:
                b = src[i]
                i += 1
                literal += b
                if b != 255:
                    break
        out += src[i:i + literal]
        i += literal
        if i + 2 > n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            break
        length = token & 0xF
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - offset
        for k in range(length):
            out.append(out[start + k])
    return bytes(out)


def decode_xci(image: bytes, address: int) -> tuple[int, int, list[int]]:
    """An XCI icon: 16-bit pixels, low byte luminance, high byte alpha.
    Returns (width, height, alpha per pixel, row-major)."""
    off = address - BASE
    if image[off:off + 4] != b'XC\0\0':
        raise SystemExit(f'no XCI at {address:#x}')
    w, h = struct.unpack_from('<HH', image, off + 8)
    block = struct.unpack_from('<I', image, off + 0x2F)[0] & 0x7FFFFFFF
    raw = lz4_block(image[off + 0x33:off + 0x33 + block])
    if len(raw) < w * h * 2:
        raise SystemExit(f'XCI at {address:#x} decoded short')
    return w, h, [raw[2 * i + 1] for i in range(w * h)]


def bands(image: bytes, table: int = BAND_TABLE, count: int = BAND_N) -> list[tuple[int, int, int]]:
    out = []
    for n in range(count):
        x0, x1, index = struct.unpack_from('<HHI', image, table - BASE + n * 8)
        out.append((x0, x1, index & 0xFF))
    out.sort()
    if out[0][0] != 0 or out[-1][1] != 1023:
        raise SystemExit('the band table does not span the 1024-pixel row')
    for (_, a, _), (b, _, _) in zip(out, out[1:]):
        if b != a + 1:
            raise SystemExit('the band table has a gap or an overlap')
    return out


def palette(image: bytes) -> list[tuple[int, int, int, int]]:
    return [struct.unpack_from('<BBbb', image, NATIVE_PAL - BASE + i * 4)
            for i in range(NATIVE_PAL_N)]


def ayuv_to_rgb(entry) -> tuple[int, int, int]:
    """The firmware's own conversion (0xC03E4C38), truncating as it does."""
    _, y, u, v = entry
    r = y + 1.402 * v
    g = y - 0.344136 * u - 0.714136 * v
    b = y + 1.772 * u
    return tuple(max(0, min(255, int(c))) for c in (r, g, b))


def pack16(rgb) -> int:
    """Opaque 4:4:4 with alpha in the top nibble, channels per PIXEL_ORDER."""
    r, g, b = ((c + 8) // 17 for c in rgb)
    if PIXEL_ORDER == 'ARGB':
        return 0xF000 | (r << 8) | (g << 4) | b
    if PIXEL_ORDER == 'ABGR':
        return 0xF000 | (b << 8) | (g << 4) | r
    raise SystemExit(f'unknown PIXEL_ORDER {PIXEL_ORDER}')


def fc_runs(image: bytes, pal) -> list[tuple[int, int, int, int]]:
    """The False Color table, with neighbours merged when they pack to the same
    16-bit colour: the grey ramp steps by four palette entries, which 4:4:4
    cannot tell apart, so 180 entries draw as 20 runs, pixel for pixel."""
    runs: list[list[int]] = []
    for x0, x1, index in bands(image, FC_BAND_TABLE, FC_BAND_N_RAW):
        colour = pack16(ayuv_to_rgb(pal[index]))
        if runs and runs[-1][3] == colour:
            runs[-1][1] = x1
        else:
            runs.append([x0, x1, index, colour])
    return [tuple(r) for r in runs]


def native_include(image: bytes) -> str:
    """The include: bands, labels and glyph bitmaps, all read from MAIN."""
    pal = palette(image)
    white = [i for i, entry in enumerate(pal) if entry == (255, 255, 0, 0)]
    if not white:
        raise SystemExit('no opaque white in the native palette for the labels')
    if pal[0][0] != 0:
        raise SystemExit('native palette index 0 is not transparent')

    lines = ['/* Generated by tools/build_fcscale_autorun.py from the verified',
             ' * SIGMA fp Ver.5.02 MAIN image. Do not edit; rebuild the card. */',
             f'.equ BAND_N,       {BAND_N}',
             f'.equ LABEL_N,      {len(LABELS)}',
             f'.equ GLYPH_N,      {len(GLYPHS)}',
             f'.equ GLYPH_H,      {GLYPH_H * 9 // 10}',
             f'.equ GLYPH_SIGN_W, {GLYPH_SIGN_W * 9 // 10}',
             f'.equ C_LABEL,      {white[0]}          /* index of opaque white */',
             f'.equ C_LABEL16,    {pack16((255, 255, 255)):#06x}      /* opaque white, {PIXEL_ORDER} 4:4:4 */',
             f'.equ C_RED16,     {pack16((255, 0, 0)):#06x}      /* labels strictly beyond clipping */',
             '.balign 4',
             f'bands:                      /* {BAND_TABLE:#x}: x0, x1 inclusive, palette index, 16-bit colour */']
    for x0, x1, index in bands(image):
        x0, x1 = 51 + x0 * 9 // 10, 51 + (x1 + 1) * 9 // 10 - 1
        a, y, u, v = pal[index]
        rgb = ayuv_to_rgb(pal[index])
        lines.append(f'    .short {x0}, {x1}; .byte {index}, 0; .short {pack16(rgb):#06x}'
                     f'    /* a{a} y{y} u{u} v{v} -> rgb {rgb} */')

    names = [name for name, _ in GLYPHS]
    lines.append('labels:                     /* x, first glyph, second glyph or 0xFF */')
    for x, first, second in LABELS:
        x = 51 + x * 9 // 10
        lines.append(f'    .short {x}; .byte {names.index(first)}, '
                     f'{names.index(second) if second else NONE}')

    runs = fc_runs(image, pal)
    lines[3:3] = [f'.equ FC_BAND_N,    {len(runs)}',
                  f'.equ FC_LABEL_N,   {len(FC_LABELS)}']
    lines.append(f'fc_bands:                   /* {FC_BAND_TABLE:#x}: {FC_BAND_N_RAW} entries '
                 f'merged by 16-bit colour, same layout as bands */')
    for x0, x1, index, colour in runs:
        x0, x1 = 51 + x0 * 9 // 10, 51 + (x1 + 1) * 9 // 10 - 1
        lines.append(f'    .short {x0}, {x1}; .byte {index}, 0; .short {colour:#06x}'
                     f'    /* rgb {ayuv_to_rgb(pal[index])} */')
    lines.append('fc_labels:                  /* x, glyph, 0xFF: one glyph per label */')
    for x, name in FC_LABELS:
        lines.append(f'    .short {51 + x * 9 // 10}; .byte {names.index(name)}, {NONE}')

    headers, bits = [], []
    for name, address in GLYPHS:
        w, h, alpha = decode_xci(image, address)
        crop = GLYPH_TOP_CROP.get(name, 0)
        if crop:
            if any(a >= 128 for a in alpha[:crop * w]):
                raise SystemExit(f'glyph {name} has ink in the rows being cropped')
            alpha, h = alpha[crop * w:], h - crop
            if h > GLYPH_H:
                if any(a >= 128 for a in alpha[GLYPH_H * w:]):
                    raise SystemExit(f'glyph {name} has ink below row {GLYPH_H}')
                alpha, h = alpha[:GLYPH_H * w], GLYPH_H
        if h != GLYPH_H:
            raise SystemExit(f'glyph {name} is {h} rows, expected {GLYPH_H}')
        if name in ('minus', 'plus') and w != GLYPH_SIGN_W:
            raise SystemExit(f'sign glyph {name} is {w} wide, expected {GLYPH_SIGN_W}')
        sw, sh = w, h
        w, h = w * 9 // 10, h * 9 // 10
        alpha = [alpha[min(sh-1, y*10//9)*sw + min(sw-1, x*10//9)] for y in range(h) for x in range(w)]
        per_row = (w + 31) // 32
        headers.append((len(bits), w, per_row, name))
        for row in range(h):
            value = 0
            for x in range(w):
                if alpha[row * w + x] >= 128:       # the icons are white; alpha is coverage
                    value |= 1 << x
            for n in range(per_row):
                bits.append((value >> (32 * n)) & 0xFFFFFFFF)
    lines.append('.balign 4')
    lines.append('glyph_hdr:                  /* offset in words, width, words per row */')
    for offset, w, per_row, name in headers:
        lines.append(f'    .short {offset}; .byte {w}, {per_row}    /* {name} */')
    lines.append('.balign 4')
    lines.append('glyph_bits:')
    for i in range(0, len(bits), 4):
        lines.append('    .word ' + ', '.join(f'{b:#010x}' for b in bits[i:i + 4]))
    return '\n'.join(lines) + '\n'


# ------------------------------------------------------------------ the card

def publish() -> list[str]:
    """Make memory writes real to the CPU: D-cache clean, then I-cache
    invalidate, each called through the echo command's handler slot."""
    return [f'mem set 0x{ECHO_SLOT:08X} 0x{CACHE_FN:08X}', 'echo',
            f'mem set 0x{ECHO_SLOT:08X} 0x{ICACHE_FN:08X}', 'echo']


def build(out: Path, fast_echo: bool = False) -> str:
    image = MAIN.read_bytes()
    if hashlib.sha256(image).hexdigest() != MAIN_SHA:
        raise SystemExit('requires the verified SIGMA fp Ver.5.02 MAIN image')
    if fast_echo and word(image, ECHO_SLEEP_AT) != ECHO_SLEEP_STOCK:
        raise SystemExit(f'the shell sleep at {ECHO_SLEEP_AT:#x} is not {ECHO_SLEEP_STOCK:#x}')
    for site, why in ((PRESS_SITE, 'press'), (RELEASE_SITE, 'release')):
        got = word(image, site)
        if got != SITE_STOCK:
            raise SystemExit(f'the {why} method at {site:#x} starts {got:#x}, '
                             f'expected {SITE_STOCK:#x}')
    if word(image, SUBMIT_SITE) != SUBMIT_STOCK:
        raise SystemExit(f'the display submit at {SUBMIT_SITE:#x} does not start '
                         f'{SUBMIT_STOCK:#x}')
    if word(image, BOOT_SITE) != BOOT_STOCK:
        raise SystemExit("CameraIF constructor epilogue differs from verified firmware")
    NATIVE_INC.write_text(native_include(image))
    defines = (f'STATE={STATE:#x}', f'SUBMIT_RESUME={SUBMIT_SITE + 4:#x}')
    code = assemble(ROOT / 'src/fcscale.S', defines)
    marks = symbols(ROOT / 'src/fcscale.S', defines)
    if STATE + STATE_SIZE > CODE:
        raise SystemExit('the state block runs into the code')
    for address, length, why in ((STATE, STATE_SIZE, 'state'), (CODE, len(code), 'code')):
        for at in range(address, address + length, 4):
            if word(image, at):
                raise SystemExit(f'the {why} cave at {at:#x} is not zero in stock')

    words = struct.unpack(f'<{len(code) // 4}I', code)
    press = CODE + marks['fc_press']
    release = CODE + marks['fc_release']
    submit = CODE + marks['fc_submit']
    lines = [
        '# ==========================================================',
        '# fp-ELZone-FalseColor-toggle-legends: False Color / EL Zone toggle + legends',
        '# SIGMA fp Ver.5.02 ONLY. RAM only: remove this file and cold',
        '# boot to revert. Nothing is written to flash.',
        '#',
        '# FIRST, in the camera:',
        '#   Menu -> Custom -> button settings -> False Color',
        '# (this changes what that button does; it does not create it)',
        '#',
        '# Then, on that button:',
        '# Short press: colours on (legend as last left) / everything off',
        '# Half-second hold: show or hide the legend',
        '#',
        '# EL Zone or False Color is your own False Color Style setting.',
        '# Nothing else is touched: no menu, no other buttons, no',
        '# recording changes, no USB shell.',
        '# ==========================================================',
        *([f'mem set 0x{ECHO_SLEEP_AT:08X} 0x{ARM_NOP:08X}'] if fast_echo else []),
        'display monitor 0 1',
        'display osd 1 0x00000000',
        'display text fp ELZone/FC',
        'display osd 1',
        '',
        # Fix 15: a power-switch restart keeps the last card's code and hooks
        # in memory, still live. Put every hook site back to stock and make
        # that real to both caches BEFORE any code word is rewritten, so
        # nothing can enter the code while it is being replaced. On a cold
        # start these are the words already there.
        '# --- disconnect whatever build ran before this restart ---',
        f'mem set 0x{SUBMIT_SITE:08X} 0x{SUBMIT_STOCK:08X}',
        f'mem set 0x{PRESS_SITE:08X} 0x{SITE_STOCK:08X}',
        f'mem set 0x{RELEASE_SITE:08X} 0x{SITE_STOCK:08X}',
        f'mem set 0x{BOOT_SITE:08X} 0x{BOOT_STOCK:08X}',
        *publish(),
        f'# --- state @ 0x{STATE:08X} ---',
    ]
    lines += [f'mem set 0x{CODE + i * 4:08X} 0x{value:X}'
              for i, value in enumerate(words)]
    lines += [
        *publish(),
        f'mem set 0x{ECHO_SLOT:08X} 0x{CODE + marks["fc_init"]:08X}',
        'echo',
        '# --- arm the press and the release, then make the new code real to',
        "#     the caches by borrowing the echo command's handler slot ---",
        f'mem set 0x{PRESS_SITE:08X} 0x{branch(PRESS_SITE, press):08X}',
        f'mem set 0x{RELEASE_SITE:08X} 0x{branch(RELEASE_SITE, release):08X}',
        f'mem set 0x{BOOT_SITE:08X} 0x{branch(BOOT_SITE, CODE + marks["fc_boot_reset"]):08X}',
        *publish(),
        '# --- the display submit fires every frame, so it is armed only once',
        '#     the code is clean, and the site itself is flushed after ---',
        f'mem set 0x{SUBMIT_SITE:08X} 0x{branch(SUBMIT_SITE, submit):08X}',
        *publish(),
        '# Start independent hold task from shell context after cache clean.',
        f'mem set 0x{ECHO_SLOT:08X} 0x{CODE + marks["fc_start"]:08X}',
        'echo',
        f'mem set 0x{ECHO_SLOT:08X} 0x{ECHO_ORIG:08X}',
        *([f'mem set 0x{ECHO_SLEEP_AT:08X} 0x{ECHO_SLEEP_STOCK:08X}'] * 3 if fast_echo else []),
        '',
        'display osd 1 0x00000000',
        'display text fp ELZone/FC ready',
        'display osd 1',
        '',
    ]
    text = '\n'.join(lines)
    # 32 KiB was this builder's own envelope, not a firmware limit: the shell
    # reads AutoRun a character at a time to the real end of the file (fpSup
    # research, AUTORUN_CHAR_DELAY.md, 2026-10-11). The cost of a longer file
    # is boot time, ~1.9 ms per visible character. Comments are dropped as
    # soon as the card is past 32 KiB, because they cost that time too.
    if len(text.encode()) > 32768:
        text = '\n'.join(line for line in lines if line and not line.startswith('#')) + '\n'
    if len(text.encode()) > AUTORUN_MAX:
        raise SystemExit(f'AutoRun exceeds {AUTORUN_MAX} bytes; use a smaller loader before shipping')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'AutoRun.txt').write_text(text)
    print(f'{out / "AutoRun.txt"}: {len(words)} words, {len(text)} bytes')
    print(f'  press   {PRESS_SITE:#010x} -> {press:#010x}')
    print(f'  release {RELEASE_SITE:#010x} -> {release:#010x}')
    print(f'  submit  {SUBMIT_SITE:#010x} -> {submit:#010x}   state {STATE:#010x}')
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=None)
    parser.add_argument('--fast-echo', action='store_true',
                        help=f'NOP the shell per-character sleep ({ECHO_SLEEP_AT:#x}) '
                             'for the card, put it back three times at the end')
    args = parser.parse_args()
    out = args.out or ROOT / ('builds/fcscale-fast' if args.fast_echo else 'builds/fcscale')
    build(out, args.fast_echo)


if __name__ == '__main__':
    main()
