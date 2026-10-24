#!/usr/bin/env python3
"""Replay a generated AutoRun line by line in the emulator, the way the
camera's shell runs it, with display frames and button presses arriving in
between -- to check the load itself, not just the finished code.

Every 25 lines a frame is submitted and the button is pressed. If a hook
site is armed at that moment, the code it leads to must already be complete
(every cave word equal to its final value); entering code that is still
being rewritten is recorded as a violation instead of being executed. That
is what froze the camera when a new build was loaded after a power-switch
restart: the previous build's hooks were still live while its code was
overwritten.

    ./tools/verify_autorun_replay.py

Cases:
  cold start                        cave and hook sites stock
  warm restart, same build          hooks live, early press starts the task
  warm restart over another build   different code at the same place, its
                                    hooks live and pointing elsewhere
  control: no disconnect            the same card without its first-lines
                                    disconnect, which must be caught
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'reference/fpSup/fp_usb_shell')]
import verify_fcscale as v
import verify_gestures as g
import build_fcscale_autorun as b

SITES = {b.SUBMIT_SITE: b.SUBMIT_STOCK, b.PRESS_SITE: b.SITE_STOCK,
         b.RELEASE_SITE: b.SITE_STOCK, b.BOOT_SITE: b.BOOT_STOCK}
CAVE = range(0xC072E000, 0xC0733000)
SET = re.compile(r'^mem set (0x[0-9A-Fa-f]+) (0x[0-9A-Fa-f]+)$')


def parse(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith('#')]


def writes(lines: list[str]) -> list[tuple[int, int]]:
    return [(int(m[1], 16), int(m[2], 16)) for m in map(SET.match, lines) if m]


class Replay:
    def __init__(self, cam: g.Camera, lines: list[str]):
        self.c, self.lines = cam, lines
        self.final = {a: w for a, w in writes(lines) if a in CAVE}
        # Each site's armed word as this card writes it last: a live hook
        # must lead to this build's own entry, not the previous build's.
        self.armed_word = {a: w for a, w in writes(lines) if a in SITES and w != SITES[a]}
        self.violations: list[str] = []
        self.published: list[str] = []
        self.presses_stock = 0

    def word(self, address: int) -> int:
        return struct.unpack('<I', self.c.u.mem_read(address, 4))[0]

    def complete(self) -> bool:
        return all(self.word(a) == w for a, w in self.final.items())

    def armed(self, site: int) -> bool:
        return self.word(site) != SITES[site]

    def safe(self, site: int) -> bool:
        return self.complete() and self.word(site) == self.armed_word.get(site)

    def frame(self, n: int) -> None:
        if self.armed(b.SUBMIT_SITE):
            if not self.safe(b.SUBMIT_SITE):
                self.violations.append(f'line {n}: display submit enters code being rewritten '
                                       f'or another build\'s entry')
                return
            self.c.ui_submit()
        if self.c.creates and self.complete() and not self.violations:
            self.c.call('fc_worker')
            self.c.tick += 50000

    def press(self, n: int) -> None:
        if not self.armed(b.PRESS_SITE):
            self.presses_stock += 1         # the camera's own False Color: not ours
            return
        if not (self.safe(b.PRESS_SITE) and self.safe(b.RELEASE_SITE)):
            self.violations.append(f'line {n}: button press enters code being rewritten '
                                   f'or another build\'s entry')
            return
        self.c._run(b.PRESS_SITE, r0=v.CAMERA_IF)
        self.c.tick += 100000
        self.c._run(b.RELEASE_SITE, r0=v.CAMERA_IF)

    def run(self, frames: bool = True, presses: bool = False) -> 'Replay':
        for n, line in enumerate(self.lines, 1):
            m = SET.match(line)
            if m:
                self.c.u.mem_write(int(m[1], 16), struct.pack('<I', int(m[2], 16)))
            elif line == 'echo':
                target = self.word(b.ECHO_SLOT)
                if target == b.CACHE_FN:
                    self.published.append('D')
                elif target == b.ICACHE_FN:
                    self.published.append('I')
                elif target != b.ECHO_ORIG:
                    assert target in CAVE, hex(target)
                    if not self.complete():
                        self.violations.append(f'line {n}: echo calls code being rewritten')
                    else:
                        self.c._run(target)
            if n % 25 == 0:
                if frames:
                    self.frame(n)
                if presses:
                    self.press(n)
        return self


def load_previous(cam: g.Camera, lines: list[str]) -> None:
    """Leave memory as a previous build's finished load would: its cave code
    and its four hook sites, which a power-switch restart keeps."""
    for address in range(0xC072E000, 0xC0733000, 4):
        cam.u.mem_write(address, b'\0\0\0\0')
    for address, value in writes(lines):
        if address in CAVE or address in SITES:
            cam.u.mem_write(address, struct.pack('<I', value))


def other_build(lines: list[str]) -> list[str]:
    """A stand-in for a different build left in memory by a warm restart:
    different code over the same cave, and hooks aimed at other entries."""
    out = []
    for address, value in writes(lines):
        if address in CAVE:
            out.append(f'mem set 0x{address:08X} 0x{value ^ 0x00000001:X}')
        elif address in SITES and value != SITES[address]:
            out.append(f'mem set 0x{address:08X} 0x{(value & 0xFF000000) | ((value + 2) & 0xFFFFFF):08X}')
    return out


def stock(cam: g.Camera) -> None:
    for address in range(0xC072E000, 0xC0733000, 4):
        cam.u.mem_write(address, b'\0\0\0\0')
    for site, word in SITES.items():
        cam.u.mem_write(site, struct.pack('<I', word))


def toggles(c: g.Camera) -> None:
    """After the load, two short presses must toggle cleanly from whatever
    state the presses made during the load left (on, or off)."""
    for _ in range(2):
        before = c.state(0)
        c.gesture(100000)
        for _ in range(3):c.call('fc_worker');c.tick += 100000
        if before == 0:
            assert c.state(0) == 1 and c.events[-1] == 0x21, (before, c.state(0), c.events)
        else:
            assert c.state(0) == 0 and c.events[-1] == 0x22, (before, c.state(0), c.events)


def main() -> None:
    card = parse((ROOT / 'builds/fcscale-fast/AutoRun.txt').read_text())
    previous = other_build(card)
    first_cave = next(i for i, l in enumerate(card)
                      if (m := SET.match(l)) and int(m[1], 16) in CAVE)
    no_disconnect = [l for i, l in enumerate(card)
                     if not (i < first_cave and (m := SET.match(l)) and int(m[1], 16) in SITES)]
    assert len(no_disconnect) == len(card) - 4
    cases = 0

    # Static: no cave word is written while any hook site is armed, and the
    # disconnect is published (D then I) before the first cave word.
    sites = {s: 'armed' for s in SITES}
    first_cave = None
    for n, (address, value) in enumerate(writes(card)):
        if address in SITES:
            sites[address] = 'stock' if value == SITES[address] else 'armed'
        elif address in CAVE:
            first_cave = n if first_cave is None else first_cave
            assert all(s == 'stock' for s in sites.values()), (hex(address), sites)
    text = '\n'.join(card)
    head = text[:text.index(f'mem set 0x{min(a for a, _ in writes(card) if a in CAVE):08X}')]
    assert head.count(f'0x{b.CACHE_FN:08X}\necho\nmem set 0x{b.ECHO_SLOT:08X} 0x{b.ICACHE_FN:08X}\necho') == 1
    assert card.count('echo') == 4 * 2 + 2, card.count('echo')   # 4 publishes, init, start
    cases += 1

    # Cold start, frames and presses throughout.
    c = g.Camera(); stock(c)
    r = Replay(c, card).run(presses=True)
    assert not r.violations, r.violations
    assert r.published == ['D', 'I'] * 4, r.published
    assert c.creates == 1 and r.presses_stock > 0
    toggles(c)
    cases += 1

    # Warm restart, same build, early press before AutoRun starts: the task
    # it started survives the load and the toggle stays in step.
    c = g.Camera(); load_previous(c, card)
    c._run(0xC03716F0, r0=v.CAMERA_IF)            # patched CameraIF constructor
    c._run(b.PRESS_SITE, r0=v.CAMERA_IF); c.tick += 100000
    c._run(b.RELEASE_SITE, r0=v.CAMERA_IF)
    for _ in range(3):c.call('fc_worker');c.tick += 100000
    assert c.state(0) == 1 and c.creates == 1
    task = c.state(0x80)
    r = Replay(c, card).run(presses=False)        # frames only: nothing else touches the toggle
    assert not r.violations, r.violations
    assert c.creates == 1 and c.state(0x80) == task and c.state(0) == 1, (c.creates, c.state(0))
    c.gesture(100000)
    assert c.state(0) == 0 and c.events[-1] == 0x22, (c.state(0), c.events)
    cases += 1

    # Warm restart over another build: its hooks are live when this card starts.
    c = g.Camera(); load_previous(c, previous)
    r = Replay(c, card).run(presses=True)
    assert not r.violations, r.violations
    assert c.creates == 1
    toggles(c)
    cases += 1

    # Control: the load order before the disconnect existed must be caught,
    # or this test proves nothing.
    c = g.Camera(); load_previous(c, previous)
    r = Replay(c, no_disconnect).run(presses=True)
    assert r.violations, 'the old card should have entered code while it was rewritten'
    cases += 1

    print(f'PASS: {cases} AutoRun replay cases -- no hook enters code while it is '
          f'being rewritten (without the disconnect: {len(r.violations)} times).')


if __name__ == '__main__':
    main()
