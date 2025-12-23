from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .cartridge import Cartridge
from .gpu import GPU, VRAM_BEGIN, VRAM_END
from .io import IO

if TYPE_CHECKING:
    from .ppu import PPU


@dataclass
class BUS:
    cartridge: Optional[Cartridge] = None
    gpu: GPU = field(default_factory=GPU)
    io: IO = field(default_factory=IO)
    ppu: Optional["PPU"] = None

    wram: bytearray = field(default_factory=lambda: bytearray(0x2000))
    hram: bytearray = field(default_factory=lambda: bytearray(0x7F))
    oam: bytearray = field(default_factory=lambda: bytearray(0xA0))

    def read_byte(self, address: int) -> int:
        address &= 0xFFFF

        if 0x0000 <= address <= 0x7FFF:
            if self.cartridge is None:
                return 0xFF
            return self.cartridge.read_rom(address)

        if VRAM_BEGIN <= address <= VRAM_END:
            if self.ppu is not None and not self.ppu.peek_vram_accessible(4):
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
            if self.ppu is not None and not self.ppu.peek_oam_accessible(4):
                return 0xFF
            return self.oam[address - 0xFE00] & 0xFF

        if address == 0xFF41 and self.ppu is not None:
            return self.ppu.peek_stat(4)

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            return self.io.read(address)

        if 0xFF80 <= address <= 0xFFFE:
            return self.hram[address - 0xFF80] & 0xFF

        return 0xFF

    def write_byte(self, address: int, value: int) -> None:
        address &= 0xFFFF
        value &= 0xFF

        if 0x0000 <= address <= 0x7FFF:
            if self.cartridge is not None:
                self.cartridge.write_rom(address, value)
            return

        if VRAM_BEGIN <= address <= VRAM_END:
            if self.ppu is not None and not self.ppu.vram_writable(4):
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
            if self.ppu is not None and not self.ppu.oam_writable(4):
                return
            self.oam[address - 0xFE00] = value
            return

        if address == 0xFF46:
            self.io.regs[0x46] = value
            src = (value << 8) & 0xFFFF
            for i in range(0xA0):
                self.oam[i] = self.read_byte((src + i) & 0xFFFF)
            return

        if (0xFF00 <= address <= 0xFF7F) or address in (0xFF0F, 0xFFFF):
            self.io.write(address, value)
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
