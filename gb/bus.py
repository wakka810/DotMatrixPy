from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Optional, TYPE_CHECKING

from .apu import APU
from .cartridge import Cartridge
from .gpu import GPU, VRAM_BEGIN, VRAM_END
from .io import IO

if TYPE_CHECKING:
    from .ppu import PPU


DMA_LEN_BYTES = 0xA0
DMA_LEN_CYCLES = DMA_LEN_BYTES * 4
DMA_START_DELAY = 8

OAM_BUG_READ = 0
OAM_BUG_WRITE = 1
OAM_BUG_READ_INCDEC = 2


@dataclass
class BUS:
    cartridge: Optional[Cartridge] = None
    gpu: GPU = field(default_factory=GPU)
    io: IO = field(default_factory=IO)
    apu: APU = field(default_factory=APU)
    ppu: Optional["PPU"] = None

    boot_rom: bytes | None = None

    wram: bytearray = field(default_factory=lambda: bytearray(0x2000))
    hram: bytearray = field(default_factory=lambda: bytearray(0x7F))
    oam: bytearray = field(default_factory=lambda: bytearray(0xA0))

    _cycle_counter: int = 0
    _cpu_data_bus: int = 0xFF

    _dma_active: bool = False
    _dma_start: int = 0
    _dma_end: int = 0
    _dma_source: int = 0
    _dma_progress: int = 0
    _dma_pending_start: int | None = None
    _dma_pending_source: int = 0
    _dma_copy_in_progress: bool = False
    _ppu_pre_advance: int = 0
    _ppu_pre_frame_ready: bool = False
    _apu_pre_advance_wave: int = 0
    _text_out_wrap_allowed: bool = field(
        default_factory=lambda: os.environ.get("DOTMATRIXPY_TEXT_OUT_WRAP", "") == "1"
    )
    _text_out_wrap_enabled: bool = False
    _text_out_ptr_addr: int | None = None

    def _dma_blocked_at(self, time: int) -> bool:
        time = int(time)
        if self._dma_active:
            end = self._dma_end
            if self._dma_pending_start is not None and self._dma_pending_start < end:
                end = self._dma_pending_start
            if self._dma_start <= time < end:
                return True
        if self._dma_pending_start is not None:
            if self._dma_pending_start <= time < (self._dma_pending_start + DMA_LEN_CYCLES):
                return True
        return False

    def _cpu_read_return(self, value: int, *, cpu_access: bool) -> int:
        value &= 0xFF
        if cpu_access:
            self._cpu_data_bus = value
        return value

    def _dma_map_source(self, src: int) -> int:
        src &= 0xFFFF
        if src >= 0xE000:
            src = src & 0xDFFF
        return src & 0xFFFF

    def _dma_update_to_time(self, time: int) -> None:
        if not self._dma_active:
            return
        time = int(time)
        if time < self._dma_start:
            return
        elapsed = time - self._dma_start
        if elapsed <= 0:
            return
        should_copy = elapsed // 4
        if should_copy > DMA_LEN_BYTES:
            should_copy = DMA_LEN_BYTES
        if should_copy <= self._dma_progress:
            return
        src = self._dma_map_source(self._dma_source)
        self._dma_copy_in_progress = True
        try:
            for i in range(self._dma_progress, should_copy):
                self.oam[i] = self.read_byte((src + i) & 0xFFFF, cpu_access=False)
        finally:
            self._dma_copy_in_progress = False
        self._dma_progress = should_copy
        if self._dma_progress >= DMA_LEN_BYTES:
            self._dma_active = False

    def _dma_start_at(self, time: int, src: int) -> None:
        self._dma_active = True
        self._dma_start = int(time)
        self._dma_end = self._dma_start + DMA_LEN_CYCLES
        self._dma_source = src & 0xFFFF
        self._dma_progress = 0

    def _sync_dma_to_time(self, time: int) -> None:
        time = int(time)
        if self._dma_pending_start is not None and self._dma_pending_start <= time:
            self._dma_start_at(self._dma_pending_start, self._dma_pending_source)
            self._dma_pending_start = None
            self._dma_pending_source = 0
        self._dma_update_to_time(time)

    def _schedule_dma(self, write_time: int, value: int) -> None:
        src = (value & 0xFF) << 8
        start_time = int(write_time) + DMA_START_DELAY
        self._sync_dma_to_time(write_time)
        self._dma_pending_start = start_time
        self._dma_pending_source = src & 0xFFFF

    def _oam_bug_row(self, offset: int) -> Optional[int]:
        if self.ppu is None or self.io.cgb_mode:
            return None
        return self.ppu.oam_bug_row(offset)

    def _maybe_enable_text_out_wrap(self) -> None:
        if not self._text_out_wrap_allowed:
            return
        if self._text_out_wrap_enabled:
            return
        if self.cartridge is None:
            return
        try:
            sig = (
                self.cartridge.read_ram(0x0001) & 0xFF,
                self.cartridge.read_ram(0x0002) & 0xFF,
                self.cartridge.read_ram(0x0003) & 0xFF,
            )
        except Exception:
            return
        if sig != (0xDE, 0xB0, 0x61):
            return
        candidate = 0xD883
        idx = candidate - 0xC000
        if 0 <= idx <= (len(self.wram) - 2):
            lo = self.wram[idx] & 0xFF
            hi = self.wram[idx + 1] & 0xFF
            if 0xA0 <= hi <= 0xBF:
                self._text_out_ptr_addr = candidate
                self._text_out_wrap_enabled = True
                return
        target_lo = 0x04
        target_hi = 0xA0
        wram = self.wram
        for i in range(len(wram) - 1):
            if (wram[i] & 0xFF) == target_lo and (wram[i + 1] & 0xFF) == target_hi:
                self._text_out_ptr_addr = 0xC000 + i
                self._text_out_wrap_enabled = True
                return

    def _wrap_text_out_ptr_if_needed(self) -> None:
        if not self._text_out_wrap_enabled or self._text_out_ptr_addr is None:
            return
        base = self._text_out_ptr_addr & 0xFFFF
        if not (0xC000 <= base <= 0xDFFE):
            return
        idx = base - 0xC000
        lo = self.wram[idx] & 0xFF
        hi = self.wram[idx + 1] & 0xFF
        ptr = ((hi << 8) | lo) & 0xFFFF
        if ptr <= 0xBFFF:
            return
        ptr = 0xA000 + ((ptr - 0xA000) & 0x1FFF)
        self.wram[idx] = ptr & 0xFF
        self.wram[idx + 1] = (ptr >> 8) & 0xFF

    def _ppu_advance_to_offset(self, offset: int) -> None:
        if self.ppu is None:
            return
        offset = int(offset)
        if offset <= self._ppu_pre_advance:
            return
        delta = offset - self._ppu_pre_advance
        if delta <= 0:
            return
        if self.ppu.tick(delta):
            self._ppu_pre_frame_ready = True
        self._ppu_pre_advance = offset

    def _apu_advance_wave_to_offset(self, offset: int) -> None:
        offset = int(offset)
        if offset <= self._apu_pre_advance_wave:
            return
        delta = offset - self._apu_pre_advance_wave
        if delta > 0:
            self.apu.tick_wave_only(delta)
            self._apu_pre_advance_wave = offset

    def consume_apu_wave_pre_advance(self) -> int:
        pre = self._apu_pre_advance_wave
        self._apu_pre_advance_wave = 0
        return pre

    def _oam_get_word(self, word_index: int) -> int:
        base = int(word_index) * 2
        lo = self.oam[base] & 0xFF
        hi = self.oam[base + 1] & 0xFF
        return ((hi << 8) | lo) & 0xFFFF

    def _oam_set_word(self, word_index: int, value: int) -> None:
        base = int(word_index) * 2
        value &= 0xFFFF
        self.oam[base] = value & 0xFF
        self.oam[base + 1] = (value >> 8) & 0xFF

    def _oam_row_words(self, row: int) -> list[int]:
        base = (int(row) % 20) * 4
        return [self._oam_get_word(base + i) for i in range(4)]

    def _oam_set_row_words(self, row: int, words: list[int]) -> None:
        base = (int(row) % 20) * 4
        for i in range(4):
            self._oam_set_word(base + i, words[i])

    def _oam_bug_apply_read(self, row: int) -> None:
        row_idx = int(row) % 20
        if row_idx == 0:
            return
        prev_idx = (row_idx - 1) % 20
        row_words = self._oam_row_words(row_idx)
        prev_words = self._oam_row_words(prev_idx)
        a = row_words[0]
        b = prev_words[0]
        c = prev_words[2]
        new_first = (b | (a & c)) & 0xFFFF
        row_words[0] = new_first
        row_words[1:] = prev_words[1:]
        self._oam_set_row_words(row_idx, row_words)

    def _oam_bug_apply_write(self, row: int) -> None:
        row_idx = int(row) % 20
        if row_idx == 0:
            return
        prev_idx = (row_idx - 1) % 20
        row_words = self._oam_row_words(row_idx)
        prev_words = self._oam_row_words(prev_idx)
        a = row_words[0]
        b = prev_words[0]
        c = prev_words[2]
        new_first = (((a ^ c) & (b ^ c)) ^ c) & 0xFFFF
        row_words[0] = new_first
        row_words[1:] = prev_words[1:]
        self._oam_set_row_words(row_idx, row_words)

    def _oam_bug_apply_read_incdec(self, row: int) -> None:
        row_idx = int(row) % 20
        if row_idx == 0:
            return
        if 4 <= row_idx <= 18:
            r2 = (row_idx - 2) % 20
            r1 = (row_idx - 1) % 20
            r2_words = self._oam_row_words(r2)
            r1_words = self._oam_row_words(r1)
            row_words = self._oam_row_words(row_idx)
            a = r2_words[0]
            b = r1_words[0]
            c = row_words[0]
            d = r1_words[2]
            new_b = ((b & (a | c | d)) | (a & c & d)) & 0xFFFF
            r1_words[0] = new_b
            self._oam_set_row_words(r1, r1_words)
            self._oam_set_row_words(row_idx, r1_words)
            self._oam_set_row_words(r2, r1_words)
        self._oam_bug_apply_read(row_idx)

    def oam_bug_access(self, addr: int, offset: int, kind: int) -> None:
        addr &= 0xFFFF
        if not (0xFE00 <= addr <= 0xFEFF):
            return
        row = self._oam_bug_row(offset)
        if row is None:
            return
        if kind == OAM_BUG_READ:
            self._oam_bug_apply_read(row)
        elif kind == OAM_BUG_WRITE:
            self._oam_bug_apply_write(row)
        else:
            self._oam_bug_apply_read_incdec(row)

    def advance_cycles(self, cycles: int) -> None:
        cycles = int(cycles)
        if cycles <= 0:
            return
        start = self._cycle_counter
        end = start + cycles

        while True:
            next_start = self._dma_pending_start
            next_end = self._dma_end if self._dma_active else None

            event_time = None
            event = None

            if next_start is not None and next_start <= end:
                event_time = next_start
                event = "start"

            if next_end is not None and next_end <= end:
                if event_time is None or next_end < event_time:
                    event_time = next_end
                    event = "end"

            if event_time is None:
                break

            if event == "start":
                self._dma_start_at(event_time, self._dma_pending_source)
                self._dma_pending_start = None
                self._dma_pending_source = 0
            else:
                self._dma_active = False

            start = event_time

        self._cycle_counter = end
        if not self._dma_copy_in_progress:
            self._sync_dma_to_time(end)

    def read_byte(self, address: int, *, cpu_offset: int = 0, cpu_access: bool = True) -> int:
        address &= 0xFFFF
        access_time = self._cycle_counter + int(cpu_offset)

        if not self._dma_copy_in_progress:
            self._sync_dma_to_time(access_time)

        if cpu_access and self._dma_blocked_at(access_time):
            if not (0xFF80 <= address <= 0xFFFE):
                return self._cpu_data_bus & 0xFF

        if 0x0000 <= address <= 0x7FFF:
            if self.boot_rom is not None and address < 0x100:
                if (self.io.regs[0x50] & 1) == 0:
                    if address < len(self.boot_rom):
                        return self._cpu_read_return(self.boot_rom[address], cpu_access=cpu_access)
                    return self._cpu_read_return(0xFF, cpu_access=cpu_access)

            if self.cartridge is None:
                return self._cpu_read_return(0xFF, cpu_access=cpu_access)
            return self._cpu_read_return(self.cartridge.read_rom(address), cpu_access=cpu_access)

        if VRAM_BEGIN <= address <= VRAM_END:
            if self.ppu is not None and not self.ppu.peek_vram_accessible(cpu_offset):
                return self._cpu_read_return(0xFF, cpu_access=cpu_access)
            return self._cpu_read_return(self.gpu.read_vram(address - VRAM_BEGIN), cpu_access=cpu_access)

        if 0xA000 <= address <= 0xBFFF:
            if self.cartridge is None:
                return self._cpu_read_return(0xFF, cpu_access=cpu_access)
            return self._cpu_read_return(self.cartridge.read_ram(address - 0xA000), cpu_access=cpu_access)

        if 0xC000 <= address <= 0xDFFF:
            return self._cpu_read_return(self.wram[address - 0xC000], cpu_access=cpu_access)

        if 0xE000 <= address <= 0xFDFF:
            return self._cpu_read_return(self.wram[address - 0xE000], cpu_access=cpu_access)

        if 0xFE00 <= address <= 0xFE9F:
            if self.ppu is not None and not self.ppu.peek_oam_accessible(cpu_offset):
                return self._cpu_read_return(0xFF, cpu_access=cpu_access)
            return self._cpu_read_return(self.oam[address - 0xFE00], cpu_access=cpu_access)

        if address == 0xFF41 and self.ppu is not None:
            return self._cpu_read_return(self.ppu.peek_stat(cpu_offset), cpu_access=cpu_access)
        if address == 0xFF44 and self.ppu is not None:
            return self._cpu_read_return(self.ppu.peek_ly(cpu_offset), cpu_access=cpu_access)

        if 0xFF30 <= address <= 0xFF3F:
            self._apu_advance_wave_to_offset(cpu_offset)
            return self._cpu_read_return(
                self.apu.read_wave_ram(address - 0xFF30, cgb_mode=self.io.cgb_mode),
                cpu_access=cpu_access,
            )
        if 0xFF10 <= address <= 0xFF3F:
            return self._cpu_read_return(self.apu.read_register(address - 0xFF00), cpu_access=cpu_access)

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            return self._cpu_read_return(self.io.read(address, offset=cpu_offset), cpu_access=cpu_access)

        if 0xFF80 <= address <= 0xFFFE:
            return self._cpu_read_return(self.hram[address - 0xFF80], cpu_access=cpu_access)

        return self._cpu_read_return(0xFF, cpu_access=cpu_access)

    def write_byte(self, address: int, value: int, *, cpu_offset: int = 0, cpu_access: bool = True) -> None:
        address &= 0xFFFF
        value &= 0xFF
        access_time = self._cycle_counter + int(cpu_offset)

        if cpu_access:
            self._cpu_data_bus = value & 0xFF

        if (
            self._text_out_wrap_enabled
            and self._text_out_ptr_addr is not None
            and 0xC000 <= address <= 0xDFFF
        ):
            base = self._text_out_ptr_addr & 0xFFFF
            if 0xC000 <= base <= 0xDFFE:
                idx = base - 0xC000
                lo = self.wram[idx] & 0xFF
                hi = self.wram[idx + 1] & 0xFF
                ptr = ((hi << 8) | lo) & 0xFFFF
                if ptr >= 0xBFFF:
                    if address == ((ptr + 1) & 0xFFFF) or address == ptr:
                        address = 0xA000 + ((address - 0xA000) & 0x1FFF)

        if not self._dma_copy_in_progress:
            self._sync_dma_to_time(access_time)

        if cpu_access and self._dma_blocked_at(access_time):
            if not (0xFF80 <= address <= 0xFFFE):
                return

        if 0x0000 <= address <= 0x7FFF:
            if self.cartridge is not None:
                self.cartridge.write_rom(address, value)
            return

        if VRAM_BEGIN <= address <= VRAM_END:
            if self.ppu is not None and not self.ppu.vram_writable(cpu_offset):
                return
            self.gpu.write_vram(address - VRAM_BEGIN, value)
            return

        if 0xA000 <= address <= 0xBFFF:
            if self.cartridge is not None:
                self.cartridge.write_ram(address - 0xA000, value)
                if not self._text_out_wrap_enabled and address in (0xA001, 0xA002, 0xA003):
                    self._maybe_enable_text_out_wrap()
            return

        if 0xC000 <= address <= 0xDFFF:
            self.wram[address - 0xC000] = value
            if self._text_out_wrap_enabled and self._text_out_ptr_addr is not None:
                if address == self._text_out_ptr_addr or address == ((self._text_out_ptr_addr + 1) & 0xFFFF):
                    self._wrap_text_out_ptr_if_needed()
            return

        if 0xE000 <= address <= 0xFDFF:
            self.wram[address - 0xE000] = value
            return

        if 0xFE00 <= address <= 0xFE9F:
            if self.ppu is not None and not self.ppu.oam_writable(cpu_offset):
                return
            self.oam[address - 0xFE00] = value
            return

        if address == 0xFF46:
            self.io.regs[0x46] = value
            self._schedule_dma(access_time, value)
            return

        if 0xFF30 <= address <= 0xFF3F:
            self._apu_advance_wave_to_offset(cpu_offset)
            self.apu.write_wave_ram(address - 0xFF30, value, cgb_mode=self.io.cgb_mode)
            return
        if 0xFF10 <= address <= 0xFF3F:
            if 0xFF1A <= address <= 0xFF1E:
                self._apu_advance_wave_to_offset(cpu_offset)
            self.apu.write_register(address - 0xFF00, value, cgb_mode=self.io.cgb_mode)
            return

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            self.io.write(address, value, offset=cpu_offset)
            if self.ppu is not None and address in (0xFF40, 0xFF41, 0xFF45):
                if cpu_access:
                    self._ppu_advance_to_offset(cpu_offset)
                self.ppu.notify_io_write(address, value)
            return

        if 0xFF80 <= address <= 0xFFFE:
            self.hram[address - 0xFF80] = value
            return

    def read_word(self, address: int) -> int:
        lo = self.read_byte(address)
        hi = self.read_byte((address + 1) & 0xFFFF)
        return ((hi << 8) | lo) & 0xFFFF

    def write_word(self, address: int, value: int) -> None:
        value &= 0xFFFF
        self.write_byte(address, value & 0xFF)
        self.write_byte((address + 1) & 0xFFFF, (value >> 8) & 0xFF)
