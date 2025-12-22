from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from .bus import BUS

SCREEN_W = 160
SCREEN_H = 144

DOTS_PER_LINE = 456
VBLANK_START_LINE = 144
VBLANK_END_LINE = 154

VBLANK_INTERRUPT_MASK = 1 << 0
STAT_INTERRUPT_MASK = 1 << 1


def _to_signed8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if (v & 0x80) else v


Sprite = Tuple[int, int, int, int, int]


@dataclass
class PPU:
    bus: "BUS"

    frame_ready: bool = False

    _enabled: bool = False
    _line: int = 0
    _dot: int = 0
    _mode: int = 0
    _mode3_len: int = 172

    _ly_read: int = 0
    _lyc: int = 0
    _coin: bool = False
    _coin_zero_delay: bool = False

    _stat_select: int = 0
    _stat_irq_line: bool = False

    _spurious_select_override_dots: int = 0

    _blank_frame: bool = False

    _window_line: int = 0

    _line_sprites: List[Sprite] = field(default_factory=list)

    framebuffer: bytearray = field(default_factory=lambda: bytearray(SCREEN_W * SCREEN_H))

    def notify_io_write(self, addr: int, value: int) -> None:
        addr &= 0xFFFF
        value &= 0xFF
        if addr == 0xFF40:
            self._handle_lcdc_change(value)
        elif addr == 0xFF41:
            self._handle_stat_write(value)
        elif addr == 0xFF45:
            self._lyc = value
            if self._enabled:
                self._update_coincidence(immediate=True)
        elif addr == 0xFF44:
            if self._enabled:
                self._line = 0
                self._dot = 0
                self._mode = 2
                self._window_line = 0
                self._prepare_visible_line()
                self._update_ly_register()
                self._update_coincidence(immediate=True)

    def tick(self, t_cycles: int) -> bool:
        t_cycles = int(t_cycles)
        if t_cycles <= 0:
            return False

        io = self.bus.io
        lcdc = io.regs[0x40] & 0xFF
        self._handle_lcdc_change(lcdc)

        self.frame_ready = False
        if not self._enabled:
            self._write_stat_disabled()
            self._ly_read = 0
            io.regs[0x44] = 0
            return False

        new_lyc = io.regs[0x45] & 0xFF
        if new_lyc != self._lyc:
            self._lyc = new_lyc
            self._update_coincidence(immediate=True)

        select = io.regs[0x41] & 0x78
        if select != self._stat_select:
            self._handle_stat_write(io.regs[0x41] & 0xFF)

        while t_cycles > 0 and self._enabled:
            step = self._next_event_distance()
            if step > t_cycles:
                step = t_cycles
            self._dot += step
            if self._spurious_select_override_dots:
                if step >= self._spurious_select_override_dots:
                    self._spurious_select_override_dots = 0
                else:
                    self._spurious_select_override_dots -= step
            if self._coin_zero_delay and self._line == 153 and self._dot >= 8:
                self._coin_zero_delay = False
                self._update_coincidence(immediate=True)

            self._process_boundary_events()
            t_cycles -= step

        return self.frame_ready

    def vram_accessible(self) -> bool:
        if not self._enabled:
            return True
        return self._mode != 3

    def oam_accessible(self) -> bool:
        if not self._enabled:
            return True
        return self._mode in (0, 1)

    def render_frame_rgb(self, out_rgb: bytearray) -> None:
        if len(out_rgb) < SCREEN_W * SCREEN_H * 3:
            raise ValueError("out_rgb buffer too small")

        if not self._enabled or self._blank_frame:
            out_rgb[:] = bytes([0xFF]) * (SCREEN_W * SCREEN_H * 3)
            return

        shade_to_rgb = (255, 170, 85, 0)
        p = 0
        for s in self.framebuffer:
            g = shade_to_rgb[s & 3]
            out_rgb[p] = g
            out_rgb[p + 1] = g
            out_rgb[p + 2] = g
            p += 3

    def _handle_lcdc_change(self, lcdc: int) -> None:
        lcd_enabled = (lcdc & 0x80) != 0
        if lcd_enabled and not self._enabled:
            self._enable()
        elif (not lcd_enabled) and self._enabled:
            self._disable()

    def _handle_stat_write(self, stat_value: int) -> None:
        select = stat_value & 0x78
        if self._enabled:
            if (self._mode in (0, 1, 2)) or self._coin:
                self._spurious_select_override_dots = 4
        self._stat_select = select
        self._write_stat()
        self._update_stat_irq()

    def _disable(self) -> None:
        io = self.bus.io
        self._enabled = False
        self.frame_ready = False
        self._line = 0
        self._dot = 0
        self._mode = 0
        self._mode3_len = 172
        self._coin = False
        self._coin_zero_delay = False
        self._stat_irq_line = False
        self._spurious_select_override_dots = 0
        self._blank_frame = False
        self._window_line = 0
        self._line_sprites.clear()
        io.regs[0x44] = 0
        self._write_stat_disabled()

    def _enable(self) -> None:
        io = self.bus.io
        self._enabled = True
        self.frame_ready = False
        self._line = 0
        self._dot = 0
        self._mode = 2
        self._window_line = 0
        self._lyc = io.regs[0x45] & 0xFF
        self._stat_select = io.regs[0x41] & 0x78
        self._stat_irq_line = False
        self._spurious_select_override_dots = 0
        self._blank_frame = True
        self.framebuffer[:] = bytes([0]) * (SCREEN_W * SCREEN_H)
        self._prepare_visible_line()
        self._update_ly_register()
        self._update_coincidence(immediate=True)
        self._write_stat()
        self._update_stat_irq()

    def _next_event_distance(self) -> int:
        d = DOTS_PER_LINE - self._dot
        if d <= 0:
            return 1

        if self._line == 153:
            if self._dot < 4:
                d = min(d, 4 - self._dot)
            if self._coin_zero_delay and self._dot < 8:
                d = min(d, 8 - self._dot)

        if self._spurious_select_override_dots:
            d = min(d, self._spurious_select_override_dots)

        if self._mode == 2 and self._dot < 80:
            d = min(d, 80 - self._dot)
        elif self._mode == 3:
            end = 80 + self._mode3_len
            if self._dot < end:
                d = min(d, end - self._dot)

        return max(1, d)

    def _process_boundary_events(self) -> None:
        while self._enabled:
            if self._line == 153 and self._dot == 4:
                self._update_ly_register()
                self._coin_zero_delay = True

            if self._mode == 2 and self._dot == 80:
                self._mode = 3
                self._write_stat()
                self._update_stat_irq()
                continue

            if self._mode == 3 and self._dot == 80 + self._mode3_len:
                if self._line < VBLANK_START_LINE and (not self._blank_frame):
                    self._render_scanline(self._line)
                self._mode = 0
                self._write_stat()
                self._update_stat_irq()
                continue

            if self._dot >= DOTS_PER_LINE:
                self._dot -= DOTS_PER_LINE
                self._advance_line()
                continue

            if self._coin_zero_delay and self._line == 153 and self._dot == 8:
                self._coin_zero_delay = False
                self._update_coincidence(immediate=True)

            if self._spurious_select_override_dots == 0:
                self._write_stat()
                self._update_stat_irq()

            break

    def _advance_line(self) -> None:
        io = self.bus.io

        if self._line < VBLANK_START_LINE:
            lcdc = io.regs[0x40] & 0xFF
            bg_on = (lcdc & 0x01) != 0
            win_on = bg_on and ((lcdc & 0x20) != 0)
            wy = io.regs[0x4A] & 0xFF
            wx = io.regs[0x4B] & 0xFF
            if win_on and (self._line >= wy) and (wx <= 166):
                self._window_line = (self._window_line + 1) & 0xFF

        self._line += 1
        if self._line >= VBLANK_END_LINE:
            self._line = 0
            if self._blank_frame:
                self._blank_frame = False
            self._window_line = 0
            if not self._blank_frame:
                self.framebuffer[:] = bytes([0]) * (SCREEN_W * SCREEN_H)

        self._mode = 1 if self._line >= VBLANK_START_LINE else 2

        if self._line == VBLANK_START_LINE:
            io.request_interrupt(VBLANK_INTERRUPT_MASK)
            self.frame_ready = True

        if self._mode == 2:
            self._prepare_visible_line()

        self._update_ly_register()
        if not (self._line == 153 and self._dot >= 4):
            self._coin_zero_delay = False
            self._update_coincidence(immediate=True)
        self._write_stat()
        self._update_stat_irq()

    def _update_ly_register(self) -> None:
        io = self.bus.io
        if self._line == 153 and self._dot >= 4:
            self._ly_read = 0
        else:
            self._ly_read = self._line & 0xFF
        io.regs[0x44] = self._ly_read

    def _update_coincidence(self, immediate: bool) -> None:
        if not self._enabled:
            self._coin = False
            self._write_stat_disabled()
            self._update_stat_irq()
            return
        if self._line == 153 and self._dot >= 4 and (not immediate):
            return
        self._coin = (self._ly_read == (self._lyc & 0xFF))
        self._write_stat()
        self._update_stat_irq()

    def _effective_stat_select(self) -> int:
        if self._spurious_select_override_dots:
            return 0x78
        return self._stat_select

    def _write_stat_disabled(self) -> None:
        io = self.bus.io
        select = io.regs[0x41] & 0x78
        io.regs[0x41] = 0x80 | select

    def _write_stat(self) -> None:
        io = self.bus.io
        select = io.regs[0x41] & 0x78
        self._stat_select = select
        if not self._enabled:
            io.regs[0x41] = 0x80 | select
            return
        mode = self._mode & 0x03
        coin = 0x04 if self._coin else 0x00
        io.regs[0x41] = 0x80 | select | coin | mode

    def _update_stat_irq(self) -> None:
        if not self._enabled:
            self._stat_irq_line = False
            return

        select = self._effective_stat_select()
        line = False
        if (select & 0x08) and self._mode == 0:
            line = True
        elif (select & 0x10) and self._mode == 1:
            line = True
        elif (select & 0x20) and self._mode == 2:
            line = True
        elif (select & 0x40) and self._coin:
            line = True

        if line and (not self._stat_irq_line):
            self.bus.io.request_interrupt(STAT_INTERRUPT_MASK)
        self._stat_irq_line = line

    def _prepare_visible_line(self) -> None:
        io = self.bus.io
        lcdc = io.regs[0x40] & 0xFF
        self._line_sprites = self._eval_sprites_for_line(self._line, lcdc)
        self._mode3_len = self._compute_mode3_len(self._line, lcdc, self._line_sprites)

    def _get_oam(self):
        gpu = self.bus.gpu
        oam = getattr(gpu, "oam", None)
        if oam is None:
            oam = getattr(gpu, "oam_ram", None)
        return oam

    def _eval_sprites_for_line(self, ly: int, lcdc: int) -> List[Sprite]:
        if (lcdc & 0x02) == 0:
            return []
        oam = self._get_oam()
        if oam is None:
            return []
        height = 16 if (lcdc & 0x04) else 8
        out: List[Sprite] = []
        for i in range(40):
            base = i * 4
            oam_y = oam[base] & 0xFF
            oam_x = oam[base + 1] & 0xFF
            sy = oam_y - 16
            if ly >= sy and ly < sy + height:
                tile = oam[base + 2] & 0xFF
                attr = oam[base + 3] & 0xFF
                out.append((oam_x, oam_y, tile, attr, i))
                if len(out) >= 10:
                    break
        return out

    def _compute_mode3_len(self, ly: int, lcdc: int, sprites: List[Sprite]) -> int:
        io = self.bus.io
        scx = io.regs[0x43] & 0xFF
        scy = io.regs[0x42] & 0xFF
        wy = io.regs[0x4A] & 0xFF
        wx = io.regs[0x4B] & 0xFF

        bg_on = (lcdc & 0x01) != 0
        win_on = bg_on and ((lcdc & 0x20) != 0) and (ly >= wy) and (wx <= 166)
        win_x = wx - 7

        length = 172
        if bg_on:
            length += scx & 7
            if win_on and (0 < win_x < 160):
                length += 6

        if (lcdc & 0x02) != 0 and sprites:
            lst = []
            for oam_x, oam_y, tile, attr, idx in sprites:
                sx = oam_x - 8
                if oam_x == 0 or (sx < 160 and (sx + 8) > 0):
                    lst.append((oam_x, idx))
            lst.sort()
            seen = set()
            for oam_x, idx in lst:
                if oam_x == 0:
                    length += 11
                    continue
                px = oam_x - 8
                in_win = win_on and (px >= win_x)
                if in_win:
                    tile_x = (px - win_x) // 8
                    tile_y = (self._window_line & 0xFF) // 8
                    offs = (px - win_x) & 7
                    key = (1, tile_x, tile_y)
                else:
                    bx = (px + scx) & 0xFF
                    tile_x = bx >> 3
                    tile_y = ((ly + scy) & 0xFF) >> 3
                    offs = bx & 7
                    key = (0, tile_x, tile_y)
                if key not in seen:
                    extra = (7 - offs) - 2
                    if extra > 0:
                        length += extra
                    seen.add(key)
                length += 6

        if length > 289:
            length = 289
        return length

    def _vram_read(self, addr: int) -> int:
        vram = self.bus.gpu.vram
        return vram[(addr - 0x8000) & 0x1FFF] & 0xFF

    def _render_scanline(self, ly: int) -> None:
        io = self.bus.io
        lcdc = io.regs[0x40] & 0xFF
        bg_on = (lcdc & 0x01) != 0
        obj_on = (lcdc & 0x02) != 0
        use_8000 = (lcdc & 0x10) != 0

        scx = io.regs[0x43] & 0xFF
        scy = io.regs[0x42] & 0xFF
        wy = io.regs[0x4A] & 0xFF
        wx = io.regs[0x4B] & 0xFF
        win_x = wx - 7
        win_on = bg_on and ((lcdc & 0x20) != 0) and (ly >= wy) and (wx <= 166)

        bgp = io.regs[0x47] & 0xFF
        obp0 = io.regs[0x48] & 0xFF
        obp1 = io.regs[0x49] & 0xFF

        bg_shades = [(bgp >> (i * 2)) & 3 for i in range(4)]
        obp0_shades = [(obp0 >> (i * 2)) & 3 for i in range(4)]
        obp1_shades = [(obp1 >> (i * 2)) & 3 for i in range(4)]

        bg_map_base = 0x9C00 if (lcdc & 0x08) else 0x9800
        win_map_base = 0x9C00 if (lcdc & 0x40) else 0x9800

        height = 16 if (lcdc & 0x04) else 8

        sprites: List[Tuple[int, int, int, int, int]] = []
        if obj_on and self._line_sprites:
            for oam_x, oam_y, tid, attr, idx in self._line_sprites:
                sprites.append((oam_x, oam_y, tid, attr, idx))
            sprites.sort(key=lambda t: (t[0], t[4]))

        def bg_tile_addr(tile_id: int, row: int) -> int:
            if use_8000:
                base = 0x8000 + (tile_id & 0xFF) * 16
            else:
                base = 0x9000 + (_to_signed8(tile_id) * 16)
            return base + (row * 2)

        fb_off = ly * SCREEN_W
        for x in range(SCREEN_W):
            bg_cid = 0
            if bg_on:
                if win_on and x >= win_x:
                    wxp = x - win_x
                    tile_x = (wxp >> 3) & 0x1F
                    tile_y = ((self._window_line & 0xFF) >> 3) & 0x1F
                    row = self._window_line & 7
                    col = wxp & 7
                    map_addr = win_map_base + tile_y * 32 + tile_x
                    tid = self._vram_read(map_addr)
                    addr = bg_tile_addr(tid, row)
                else:
                    px = (x + scx) & 0xFF
                    py = (ly + scy) & 0xFF
                    tile_x = (px >> 3) & 0x1F
                    tile_y = (py >> 3) & 0x1F
                    row = py & 7
                    col = px & 7
                    map_addr = bg_map_base + tile_y * 32 + tile_x
                    tid = self._vram_read(map_addr)
                    addr = bg_tile_addr(tid, row)

                b1 = self._vram_read(addr)
                b2 = self._vram_read(addr + 1)
                mask = 1 << (7 - col)
                bg_cid = ((1 if (b2 & mask) else 0) << 1) | (1 if (b1 & mask) else 0)

            shade = bg_shades[bg_cid]

            if obj_on and sprites:
                for oam_x, oam_y, tid, attr, idx in sprites:
                    sx = oam_x - 8
                    if x < sx or x >= sx + 8:
                        continue
                    sy = oam_y - 16
                    row = ly - sy
                    if row < 0 or row >= height:
                        continue
                    xflip = (attr & 0x20) != 0
                    yflip = (attr & 0x40) != 0
                    if yflip:
                        row = (height - 1) - row
                    if height == 16:
                        tid &= 0xFE
                        if row >= 8:
                            tid |= 0x01
                        row &= 7
                    col = (x - sx) & 7
                    if xflip:
                        col = 7 - col
                    addr = 0x8000 + (tid & 0xFF) * 16 + row * 2
                    b1 = self._vram_read(addr)
                    b2 = self._vram_read(addr + 1)
                    mask = 1 << (7 - col)
                    cid = ((1 if (b2 & mask) else 0) << 1) | (1 if (b1 & mask) else 0)
                    if cid == 0:
                        continue
                    behind = (attr & 0x80) != 0
                    if behind and bg_on and bg_cid != 0:
                        continue
                    pal = obp1_shades if (attr & 0x10) else obp0_shades
                    shade = pal[cid]
                    break

            self.framebuffer[fb_off + x] = shade & 3
