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
	_apu_cycle_remainder: int = 0
	_ppu_cycle_remainder: int = 0

	def __post_init__(self) -> None:
		self.cpu = CPU(bus=self.bus)
		self.ppu = PPU(bus=self.bus)
		self.bus.ppu = self.ppu

	@classmethod
	def from_rom(cls, rom_path: str | Path, boot_rom: str | Path | None = None) -> "GameBoy":
		gb = cls()
		gb.load_rom(rom_path)
		if boot_rom:
			gb.load_boot_rom(boot_rom)
			gb.reset_dmg(boot=True)
		else:
			gb.reset_dmg(boot=False)
		return gb

	def load_rom(self, rom_path: str | Path) -> None:
		rom_path = Path(rom_path)
		data = rom_path.read_bytes()
		self.bus.cartridge = Cartridge.from_bytes(data)
		cgb_flag = self.bus.cartridge.header.cgb_flag & 0xC0
		if cgb_flag == 0xC0:
			raise ValueError("CGB-only ROMs are not supported by this DMG-01 emulator.")
		self.bus.io.cgb_mode = False
		self.bus.io.double_speed = False
		self.bus.io.key1_prepare = False

	def load_boot_rom(self, boot_rom_path: str | Path) -> None:
		self.bus.boot_rom = Path(boot_rom_path).read_bytes()

	def reset_dmg(self, boot: bool = False) -> None:
		if boot:
			self.cpu.regs.set_af(0x0000)
			self.cpu.regs.set_bc(0x0000)
			self.cpu.regs.set_de(0x0000)
			self.cpu.regs.set_hl(0x0000)
			self.cpu.sp = 0x0000
			self.cpu.pc = 0x0000
			self.cpu.ime = False
			self.cpu.halted = False
			self.cpu.stopped = False

			io = self.bus.io
			io.regs[:] = b"\x00" * 0x80
			io.interrupt_enable = 0x00
			io.interrupt_flag = 0x00
			io._div_counter = 0x0000
			io._apu_div_ticks_pending = 0
			
			self.bus.apu.reset_dmg()
			self.bus.apu.frame_sequencer = 0

			self.ppu._line = 0
			self.ppu._dot = 0
			return

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
		io.double_speed = False
		io.key1_prepare = False
		io.interrupt_enable = 0x00
		io.interrupt_flag = 0xE1

		io._div_counter = 0xABCC
		io.regs[0x04] = 0xAB
		io._apu_div_ticks_pending = 0

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

		self.bus.apu.reset_dmg()
		self.bus.apu.frame_sequencer = 0

		self.ppu.notify_io_write(0xFF40, io.regs[0x40])
		self.ppu.notify_io_write(0xFF45, io.regs[0x45])
		self.ppu._line = 0
		self.ppu._dot = 0

	def step(self) -> int:
		cycles = self.cpu.step()
		self.bus.advance_cycles(cycles)
		self.bus.io.tick(cycles)
		div_ticks = self.bus.io.consume_apu_div_ticks()
		wave_pre = self.bus.consume_apu_wave_pre_advance()
		speed_div = 2 if self.bus.io.double_speed else 1
		if speed_div == 1:
			self.bus.apu.tick(cycles, div_ticks, wave_pre)
			remaining = cycles - self.bus._ppu_pre_advance
			if remaining < 0:
				remaining = 0
			self.last_frame_ready = self.ppu.tick(remaining) or self.bus._ppu_pre_frame_ready
		else:
			self._apu_cycle_remainder += cycles
			apu_cycles = self._apu_cycle_remainder // speed_div
			self._apu_cycle_remainder %= speed_div
			wave_pre //= speed_div
			if apu_cycles or div_ticks or wave_pre:
				self.bus.apu.tick(apu_cycles, div_ticks, wave_pre)
			self._ppu_cycle_remainder += cycles
			ppu_cycles = self._ppu_cycle_remainder // speed_div
			self._ppu_cycle_remainder %= speed_div
			remaining = ppu_cycles - self.bus._ppu_pre_advance
			if remaining < 0:
				remaining = 0
			self.last_frame_ready = self.ppu.tick(remaining) or self.bus._ppu_pre_frame_ready
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
