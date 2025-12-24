from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .cartridge import Cartridge
from .gpu import GPU, VRAM_BEGIN, VRAM_END
from .io import IO

if TYPE_CHECKING:
    from .ppu import PPU


DMA_LEN_BYTES = 0xA0
DMA_LEN_CYCLES = DMA_LEN_BYTES * 4
DMA_START_DELAY = 8


@dataclass
class BUS:
    cartridge: Optional[Cartridge] = None
    gpu: GPU = field(default_factory=GPU)
    io: IO = field(default_factory=IO)
    ppu: Optional["PPU"] = None

    wram: bytearray = field(default_factory=lambda: bytearray(0x2000))
    hram: bytearray = field(default_factory=lambda: bytearray(0x7F))
    oam: bytearray = field(default_factory=lambda: bytearray(0xA0))

    _cycle_counter: int = 0

    _dma_active: bool = False
    _dma_start: int = 0
    _dma_end: int = 0
    _dma_source: int = 0
    _dma_pending_start: int | None = None
    _dma_pending_source: int = 0

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

    def _dma_map_source(self, src: int) -> int:
        src &= 0xFFFF
        if src >= 0xE000:
            src = src & 0xDFFF
        return src & 0xFFFF

    def _dma_copy(self, src: int) -> None:
        src = self._dma_map_source(src)
        for i in range(DMA_LEN_BYTES):
            self.oam[i] = self.read_byte((src + i) & 0xFFFF, cpu_access=False)

    def _dma_start_at(self, time: int, src: int) -> None:
        self._dma_active = True
        self._dma_start = int(time)
        self._dma_end = self._dma_start + DMA_LEN_CYCLES
        self._dma_source = src & 0xFFFF
        self._dma_copy(self._dma_source)

    def _sync_dma_to_time(self, time: int) -> None:
        time = int(time)
        if self._dma_active and self._dma_end <= time:
            self._dma_active = False
        if self._dma_pending_start is not None and self._dma_pending_start <= time:
            self._dma_start_at(self._dma_pending_start, self._dma_pending_source)
            self._dma_pending_start = None
            self._dma_pending_source = 0
            if self._dma_end <= time:
                self._dma_active = False

    def _schedule_dma(self, write_time: int, value: int) -> None:
        src = (value & 0xFF) << 8
        start_time = int(write_time) + DMA_START_DELAY
        self._sync_dma_to_time(write_time)
        self._dma_pending_start = start_time
        self._dma_pending_source = src & 0xFFFF

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

    def read_byte(self, address: int, *, cpu_offset: int = 0, cpu_access: bool = True) -> int:
        address &= 0xFFFF
        access_time = self._cycle_counter + int(cpu_offset)

        if cpu_access and self._dma_blocked_at(access_time) and 0xFE00 <= address <= 0xFE9F:
            return 0xFF

        if 0x0000 <= address <= 0x7FFF:
            if self.cartridge is None:
                return 0xFF
            return self.cartridge.read_rom(address)

        if VRAM_BEGIN <= address <= VRAM_END:
            if self.ppu is not None and not self.ppu.peek_vram_accessible(cpu_offset):
                return 0xFF
            return self.gpu.read_vram(address - VRAM_BEGIN)

        if 0xA000 <= address <= 0xBFFF:
            if self.cartridge is None:
                return 0xFF
            return self.cartridge.read_ram(address - 0xA000)

        if 0xC000 <= address <= 0xDFFF:
            return self.wram[address - 0xC000] & 0xFF

        if 0xE000 <= address <= 0xFDFF:
            return self.wram[address - 0xE000] & 0xFF

        if 0xFE00 <= address <= 0xFE9F:
            if self.ppu is not None and not self.ppu.peek_oam_accessible(cpu_offset):
                return 0xFF
            return self.oam[address - 0xFE00] & 0xFF

        if address == 0xFF41 and self.ppu is not None:
            return self.ppu.peek_stat(cpu_offset)
        if address == 0xFF44 and self.ppu is not None:
            return self.ppu.peek_ly(cpu_offset)

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            return self.io.read(address, offset=cpu_offset)

        if 0xFF80 <= address <= 0xFFFE:
            return self.hram[address - 0xFF80] & 0xFF

        return 0xFF

    def write_byte(self, address: int, value: int, *, cpu_offset: int = 0, cpu_access: bool = True) -> None:
        address &= 0xFFFF
        value &= 0xFF
        access_time = self._cycle_counter + int(cpu_offset)

        if cpu_access and self._dma_blocked_at(access_time) and 0xFE00 <= address <= 0xFE9F:
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
            return

        if 0xC000 <= address <= 0xDFFF:
            self.wram[address - 0xC000] = value
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

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            self.io.write(address, value, offset=cpu_offset)
            if self.ppu is not None and address in (0xFF40, 0xFF41, 0xFF44, 0xFF45):
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
