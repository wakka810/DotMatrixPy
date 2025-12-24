from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .bus import BUS
from .cartridge import Cartridge
from .cpu import CPU
from .ppu import PPU, SCREEN_H, SCREEN_W


@dataclass
class GameBoy:
	bus: BUS = field(default_factory=BUS)
	cpu: CPU = field(init=False)
	ppu: PPU = field(init=False)
	frame_rgb: bytearray = field(default_factory=lambda: bytearray(SCREEN_W * SCREEN_H * 3))
	last_frame_ready: bool = False

	def __post_init__(self) -> None:
		self.cpu = CPU(bus=self.bus)
		self.ppu = PPU(bus=self.bus)
		self.bus.ppu = self.ppu

	@classmethod
	def from_rom(cls, rom_path: str | Path) -> "GameBoy":
		gb = cls()
		gb.load_rom(rom_path)
		gb.reset_dmg()
		return gb

	def load_rom(self, rom_path: str | Path) -> None:
		self.bus.cartridge = Cartridge.from_file(rom_path)

	def reset_dmg(self) -> None:
		self.cpu.regs.set_af(0x01B0)
		self.cpu.regs.set_bc(0x0013)
		self.cpu.regs.set_de(0x00D8)
		self.cpu.regs.set_hl(0x014D)
		self.cpu.sp = 0xFFFE
		self.cpu.pc = 0x0100
		self.cpu.ime = False
		self.cpu.halted = False
		self.cpu.stopped = False

		io = self.bus.io
		io.interrupt_enable = 0x00
		io.interrupt_flag = 0xE1
		io.regs[0x00] = 0x00

		io.regs[0x04] = 0x00
		io.regs[0x05] = 0x00
		io.regs[0x06] = 0x00
		io.regs[0x07] = 0x00

		io.regs[0x40] = 0x91
		io.regs[0x41] = 0x85
		io.regs[0x42] = 0x00
		io.regs[0x43] = 0x00
		io.regs[0x44] = 0x00
		io.regs[0x45] = 0x00
		io.regs[0x47] = 0xFC
		io.regs[0x48] = 0xFF
		io.regs[0x49] = 0xFF
		io.regs[0x4A] = 0x00
		io.regs[0x4B] = 0x00

		self.ppu.notify_io_write(0xFF40, io.regs[0x40])
		self.ppu.notify_io_write(0xFF45, io.regs[0x45])
		self.ppu.notify_io_write(0xFF44, io.regs[0x44])

	def step(self) -> int:
		cycles = self.cpu.step()
		self.bus.advance_cycles(cycles)
		self.bus.io.tick(cycles)
		self.last_frame_ready = self.ppu.tick(cycles)
		if self.last_frame_ready:
			self.ppu.render_frame_rgb(self.frame_rgb)
		return cycles

	def run_until_frame(self, max_cycles: int = 70224 * 4) -> bool:
		elapsed = 0
		while elapsed < max_cycles:
			c = self.step()
			elapsed += c
			if self.last_frame_ready:
				return True
			out = self.bus.io.consume_serial_output()
			if out:
				print(out, end="", flush=True)
		return False
