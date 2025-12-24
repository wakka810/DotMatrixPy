from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path


_MOONEYE_PASS = bytes([3, 5, 8, 13, 21, 34])
_MOONEYE_FAIL = bytes([0x42] * 6)


def _tail_append(buf: str, add: str, keep: int) -> str:
	if not add:
		return buf
	buf = buf + add
	if len(buf) > keep:
		buf = buf[-keep:]
	return buf


def _detect_mooneye_pass_fail(serial: str) -> str | None:
	try:
		b = bytes((ord(ch) & 0xFF) for ch in serial)
		if _MOONEYE_FAIL in b:
			return "FAIL"
		if _MOONEYE_PASS in b:
			return "PASS"
	except Exception:
		pass
	return None


def _hex8(v: int) -> str:
	return f"0x{(int(v) & 0xFF):02X}"


def _hex16(v: int) -> str:
	return f"0x{(int(v) & 0xFFFF):04X}"


def _read_mem(bus, addr: int, n: int) -> list[int]:
	addr &= 0xFFFF
	return [bus.read_byte((addr + i) & 0xFFFF) & 0xFF for i in range(max(0, int(n)))]


def _dump_trace(trace: list[tuple[int, int, int, int]]) -> None:
	# (pc, op, sp, f)
	if not trace:
		return
	print("\n-- last instructions (pc op sp f) --")
	for pc, op, sp, f in trace[-len(trace):]:
		print(f"  {pc:04X}  {op:02X}  SP={sp:04X}  F={f:02X}")


def _dump_lcdon_timing_gs(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	ppu = gb.ppu
	op = bus.read_byte(cpu.pc)

	print("\n=== lcdon_timing-GS.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)
	print(
		"IO:",
		f"FF40(LCDC)={_hex8(io.regs[0x40])}",
		f"FF41(STAT)={_hex8(bus.read_byte(0xFF41))}",
		f"FF44(LY)={_hex8(io.regs[0x44])}",
		f"FF45(LYC)={_hex8(io.regs[0x45])}",
		f"FF42(SCY)={_hex8(io.regs[0x42])}",
		f"FF43(SCX)={_hex8(io.regs[0x43])}",
		f"FF4A(WY)={_hex8(io.regs[0x4A])}",
		f"FF4B(WX)={_hex8(io.regs[0x4B])}",
		f"FF04(DIV)={_hex8(bus.read_byte(0xFF04))}",
		f"IF={_hex8(io.interrupt_flag)} IE={_hex8(io.interrupt_enable)}",
	)
	print(
		"PPU:",
		f"enabled={int(bool(getattr(ppu, '_enabled', False)))}",
		f"mode={int(getattr(ppu, '_mode', -1))} line={int(getattr(ppu, '_line', -1))} dot={int(getattr(ppu, '_dot', -1))}",
		f"mode3_len={int(getattr(ppu, '_mode3_len', -1))}",
		f"line_mode2_delay={int(getattr(ppu, '_line_mode2_delay', -1))}",
		f"post_enable_delay_lines={int(getattr(ppu, '_post_enable_delay_lines_remaining', -1))}",
		f"pending_mode0_dot={int(getattr(ppu, '_pending_stat_mode0_dot', -1))} pending_coin_dot={int(getattr(ppu, '_pending_coincidence_dot', -1))}",
		f"line0_quirk={int(bool(getattr(ppu, '_line0_quirk', False)))} blank_frame={int(bool(getattr(ppu, '_blank_frame', False)))}",
		f"oam_access={int(bool(ppu.oam_accessible()))} vram_access={int(bool(ppu.vram_accessible()))}",
	)
	print("BUS:", f"cycle_counter={int(getattr(bus, '_cycle_counter', -1))}")

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _dump_tma_write_reloading(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	op = bus.read_byte(cpu.pc)

	div = bus.read_byte(0xFF04) & 0xFF
	tima = bus.read_byte(0xFF05) & 0xFF
	tma = bus.read_byte(0xFF06) & 0xFF
	tac = bus.read_byte(0xFF07) & 0xFF

	print("\n=== tma_write_reloading.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)
	print(
		"TMR:",
		f"DIV={_hex8(div)} TIMA={_hex8(tima)} TMA={_hex8(tma)} TAC={_hex8(tac)}",
		f"IF={_hex8(io.interrupt_flag)} IE={_hex8(io.interrupt_enable)}",
	)

	# Internal/pending timer state (useful for TMA write timing around reload).
	tac_val = io.regs[0x07] & 0x07
	timer_enabled = (tac_val & 0x04) != 0
	timer_bit = io._timer_bit(tac_val) if timer_enabled else -1
	div_counter = int(getattr(io, "_div_counter", -1)) & 0xFFFF
	input_state = io._timer_input(tac_val, div_counter) if timer_enabled else 0

	print(
		"INT:",
		f"global_cycles={int(getattr(io, '_global_cycles', -1))}",
		f"div_counter={div_counter}",
		f"timer_enabled={int(timer_enabled)} bit={timer_bit} input={input_state}",
	)
	print(
		"REL:",
		f"reload_pending={int(bool(getattr(io, '_tima_reload_pending', False)))}",
		f"reload_counter={int(getattr(io, '_tima_reload_counter', -1))}",
		f"overflow_cancel_until={int(getattr(io, '_tima_overflow_cancel_until', -1))}",
	)
	print(
		"PEND:",
		f"div_off={getattr(io, '_div_reset_pending_offset', None)}",
		f"tac_off={getattr(io, '_tac_pending_offset', None)} tac_old={_hex8(getattr(io, '_tac_pending_old', 0))} tac_val={_hex8(getattr(io, '_tac_pending_value', 0))}",
		f"tima_off={getattr(io, '_tima_pending_offset', None)} tima_val={_hex8(getattr(io, '_tima_pending_value', 0))}",
		f"tma_off={getattr(io, '_tma_pending_offset', None)} tma_val={_hex8(getattr(io, '_tma_pending_value', 0))}",
	)

	# Show predicted TIMA over the next few cycles given current internal state.
	pred = [io._peek_tima_at_offset(i) & 0xFF for i in range(0, 9)]
	print("PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(pred)))

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _dump_boot_hwio_dmgabcmgb(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	op = bus.read_byte(cpu.pc)

	print("\n=== boot_hwio-dmgABCmgb.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)
	print("INT:", f"IF={_hex8(bus.read_byte(0xFF0F))} IE={_hex8(bus.read_byte(0xFFFF))}")

	# Read back the full HWIO range as the CPU sees it.
	print("IO[FF00..FF7F]:")
	for base in range(0xFF00, 0xFF80, 0x10):
		row = _read_mem(bus, base, 0x10)
		print(f"  {base:04X}:", " ".join(f"{b:02X}" for b in row))

	# Also show our internal IO backing regs for quick comparison.
	print("IO.regs[00..7F] (internal):")
	for off in range(0x00, 0x80, 0x10):
		row = [io.regs[off + i] & 0xFF for i in range(0x10)]
		print(f"  {off:02X}:", " ".join(f"{b:02X}" for b in row))

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _dump_boot_div_dmgabcmgb(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	op = bus.read_byte(cpu.pc)

	div = bus.read_byte(0xFF04) & 0xFF
	tima = bus.read_byte(0xFF05) & 0xFF
	tma = bus.read_byte(0xFF06) & 0xFF
	tac = bus.read_byte(0xFF07) & 0xFF

	print("\n=== boot_div-dmgABCmgb.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)
	print("INT:", f"IF={_hex8(bus.read_byte(0xFF0F))} IE={_hex8(bus.read_byte(0xFFFF))}")
	print("TMR:", f"DIV={_hex8(div)} TIMA={_hex8(tima)} TMA={_hex8(tma)} TAC={_hex8(tac)}")

	div_counter = int(getattr(io, "_div_counter", -1)) & 0xFFFF
	gc = int(getattr(io, "_global_cycles", -1))
	div_reset_off = getattr(io, "_div_reset_pending_offset", None)

	tac_val = io.regs[0x07] & 0x07
	timer_enabled = (tac_val & 0x04) != 0
	timer_bit = io._timer_bit(tac_val) if timer_enabled else -1
	input_state = io._timer_input(tac_val, div_counter) if timer_enabled else 0

	print(
		"DIV:",
		f"div_counter={div_counter} (hi={_hex8((div_counter >> 8) & 0xFF)})",
		f"div_reset_pending_offset={div_reset_off}",
		f"global_cycles={gc}",
	)
	print(
		"TMR_INT:",
		f"timer_enabled={int(timer_enabled)} bit={timer_bit} input={input_state}",
		f"reload_pending={int(bool(getattr(io, '_tima_reload_pending', False)))}",
		f"reload_counter={int(getattr(io, '_tima_reload_counter', -1))}",
	)

	# Sample the DIV register as seen by the CPU for a few cycle offsets.
	div_peek = [bus.read_byte(0xFF04, cpu_offset=i) & 0xFF for i in range(0, 9)]
	print("DIV_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(div_peek)))
	# Sample predicted TIMA evolution too (handy when DIV reset edge cases cascade into TIMA).
	pred_tima = [io._peek_tima_at_offset(i) & 0xFF for i in range(0, 9)]
	print("TIMA_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(pred_tima)))

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _dump_boot_sclk_align_dmgabcmgb(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	op = bus.read_byte(cpu.pc)

	sb = bus.read_byte(0xFF01) & 0xFF
	sc = bus.read_byte(0xFF02) & 0xFF

	print("\n=== boot_sclk_align-dmgABCmgb.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)
	print("INT:", f"IF={_hex8(bus.read_byte(0xFF0F))} IE={_hex8(bus.read_byte(0xFFFF))}")
	print("SER:", f"SB(FF01)={_hex8(sb)} SC(FF02)={_hex8(sc)}")

	# Internal serial engine state.
	print(
		"SER_INT:",
		f"active={int(bool(getattr(io, '_serial_active', False)))}",
		f"internal_clock={int(bool(getattr(io, '_serial_internal_clock', False)))}",
		f"cycle_acc={int(getattr(io, '_serial_cycle_acc', -1))}",
		f"bits_left={int(getattr(io, '_serial_bits_left', -1))}",
		f"latch_out={_hex8(int(getattr(io, '_serial_latch_out', 0)))}",
	)
	print(
		"TIME:",
		f"bus_cycle_counter={int(getattr(bus, '_cycle_counter', -1))}",
		f"io_global_cycles={int(getattr(io, '_global_cycles', -1))}",
	)

	# Peek what the CPU would read for the next few cycles.
	sb_peek = [bus.read_byte(0xFF01, cpu_offset=i) & 0xFF for i in range(0, 9)]
	sc_peek = [bus.read_byte(0xFF02, cpu_offset=i) & 0xFF for i in range(0, 9)]
	print("SB_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(sb_peek)))
	print("SC_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(sc_peek)))

	# Show any buffered serial output.
	out = gb.bus.io.consume_serial_output()
	print("SER_OUT:", repr(out))

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _dump_intr_2_0_timing(gb, *, trace: list[tuple[int, int, int, int]]) -> None:
	cpu = gb.cpu
	regs = cpu.regs
	bus = gb.bus
	io = bus.io
	ppu = gb.ppu
	op = bus.read_byte(cpu.pc)

	print("\n=== intr_2_0_timing.gb debug dump ===")
	print(
		"CPU:",
		f"PC={_hex16(cpu.pc)} OP={_hex8(op)} SP={_hex16(cpu.sp)} IME={int(bool(cpu.ime))}",
		f"HALT={int(bool(cpu.halted))} STOP={int(bool(cpu.stopped))}",
	)
	print(
		"REG:",
		f"A={_hex8(regs.a)} F={_hex8(regs.f)} B={_hex8(regs.b)} C={_hex8(regs.c)}",
		f"D={_hex8(regs.d)} E={_hex8(regs.e)} H={_hex8(regs.h)} L={_hex8(regs.l)}",
	)

	lcdc = bus.read_byte(0xFF40) & 0xFF
	stat = bus.read_byte(0xFF41) & 0xFF
	scy = bus.read_byte(0xFF42) & 0xFF
	scx = bus.read_byte(0xFF43) & 0xFF
	ly = bus.read_byte(0xFF44) & 0xFF
	lyc = bus.read_byte(0xFF45) & 0xFF
	iflag = bus.read_byte(0xFF0F) & 0xFF
	ie = bus.read_byte(0xFFFF) & 0xFF
	div = bus.read_byte(0xFF04) & 0xFF

	print(
		"IO:",
		f"FF40(LCDC)={_hex8(lcdc)} FF41(STAT)={_hex8(stat)} FF44(LY)={_hex8(ly)} FF45(LYC)={_hex8(lyc)}",
		f"FF42(SCY)={_hex8(scy)} FF43(SCX)={_hex8(scx)} FF04(DIV)={_hex8(div)}",
		f"IF={_hex8(iflag)} IE={_hex8(ie)}",
	)

	print(
		"PPU:",
		f"enabled={int(bool(getattr(ppu, '_enabled', False)))}",
		f"mode={int(getattr(ppu, '_mode', -1))} line={int(getattr(ppu, '_line', -1))} dot={int(getattr(ppu, '_dot', -1))}",
		f"mode3_len={int(getattr(ppu, '_mode3_len', -1))}",
	)
	print(
		"PPU2:",
		f"line_mode2_delay={int(getattr(ppu, '_line_mode2_delay', -1))}",
		f"post_enable_delay_lines={int(getattr(ppu, '_post_enable_delay_lines_remaining', -1))}",
		f"pending_mode0_dot={int(getattr(ppu, '_pending_stat_mode0_dot', -1))}",
		f"pending_coin_dot={int(getattr(ppu, '_pending_coincidence_dot', -1))}",
		f"stat_select={_hex8(int(getattr(ppu, '_stat_select', 0)))}",
		f"stat_irq_line={int(bool(getattr(ppu, '_stat_irq_line', False)))}",
		f"spurious_override_dots={int(getattr(ppu, '_spurious_select_override_dots', -1))}",
	)
	print(
		"BUS:",
		f"cycle_counter={int(getattr(bus, '_cycle_counter', -1))}",
		f"io_global_cycles={int(getattr(io, '_global_cycles', -1))}",
	)

	# Peek STAT/LY as observed by CPU in the next few cycles.
	stat_peek = [bus.read_byte(0xFF41, cpu_offset=i) & 0xFF for i in range(0, 9)]
	ly_peek = [bus.read_byte(0xFF44, cpu_offset=i) & 0xFF for i in range(0, 9)]
	print("STAT_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(stat_peek)))
	print("LY_PEEK:", " ".join(f"+{i}:{b:02X}" for i, b in enumerate(ly_peek)))

	sp = cpu.sp & 0xFFFF
	stack16 = _read_mem(bus, sp, 16)
	print("STACK[SP..SP+15]:", " ".join(f"{b:02X}" for b in stack16))
	_dump_trace(trace)


def _run_headless_with_results(
	rom: Path,
	*,
	max_cycles: int = 80_000_000,
	timeout_s: float = 20.0,
	serial_tail: int = 4096,
	trace_last: int = 0,
	dump_tma_write_reloading: bool = False,
	dump_boot_hwio_dmgabcmgb: bool = False,
	dump_boot_div_dmgabcmgb: bool = False,
	dump_boot_sclk_align_dmgabcmgb: bool = False,
	dump_intr_2_0_timing: bool = False,
) -> int:
	from gb.gameboy import GameBoy

	gb = GameBoy.from_rom(rom)
	start = time.monotonic()
	cycles = 0
	serial = ""

	pass_regs = (3, 5, 8, 13, 21, 34)
	fail_regs = (0x42, 0x42, 0x42, 0x42, 0x42, 0x42)
	trace: list[tuple[int, int, int, int]] = []

	status: str | None = None
	while True:
		if timeout_s > 0 and (time.monotonic() - start) >= timeout_s:
			status = "TIMEOUT"
			break
		if max_cycles > 0 and cycles >= max_cycles:
			status = "TIMEOUT"
			break

		op = gb.bus.read_byte(gb.cpu.pc)
		if trace_last > 0:
			trace.append((gb.cpu.pc & 0xFFFF, op & 0xFF, gb.cpu.sp & 0xFFFF, gb.cpu.regs.f & 0xFF))
			if len(trace) > trace_last:
				del trace[: len(trace) - trace_last]
		if op == 0x40:  # HALT
			r = gb.cpu.regs
			sig = (r.b & 0xFF, r.c & 0xFF, r.d & 0xFF, r.e & 0xFF, r.h & 0xFF, r.l & 0xFF)
			if sig == pass_regs:
				status = "PASS"
				break
			if sig == fail_regs:
				status = "FAIL"
				break

		c = gb.step()
		cycles += int(c)

		out = gb.bus.io.consume_serial_output()
		if out:
			print(out, end="", flush=True)
			serial = _tail_append(serial, out, serial_tail)
			serial_status = _detect_mooneye_pass_fail(serial)
			if serial_status is not None:
				status = serial_status
				break

	if status is None:
		status = "ERROR"

	print(f"\n=== Result: {status}  cycles={cycles}  elapsed={time.monotonic() - start:.2f}s ===")
	if dump_tma_write_reloading:
		_dump_tma_write_reloading(gb, trace=trace)
	if dump_boot_hwio_dmgabcmgb:
		_dump_boot_hwio_dmgabcmgb(gb, trace=trace)
	if dump_boot_div_dmgabcmgb:
		_dump_boot_div_dmgabcmgb(gb, trace=trace)
	if dump_boot_sclk_align_dmgabcmgb:
		_dump_boot_sclk_align_dmgabcmgb(gb, trace=trace)
	if dump_intr_2_0_timing:
		_dump_intr_2_0_timing(gb, trace=trace)

	return 0 if status == "PASS" else 1


def main() -> int:
	parser = argparse.ArgumentParser(description="Run a Game Boy ROM (minimal emulator)")
	parser.add_argument("rom", type=Path, help="Path to .gb/.gbc ROM")
	parser.add_argument("--scale", type=int, default=3, help="Window scale (default: 3)")
	parser.add_argument("--fps", type=int, default=60, help="FPS cap (default: 60)")
	parser.add_argument("--headless", action="store_true", help="Run without a window")
	parser.add_argument("--debug", action="store_true", help="Enable debug logging (CPU trace)")
	parser.add_argument("--print-results", action="store_true", help="Print detailed test results")
	args = parser.parse_args()

	if args.debug:
		logging.basicConfig(level=logging.DEBUG, format="%(message)s")

	from gb.gameboy import GameBoy
	from gb.ppu import SCREEN_H, SCREEN_W

	gb = GameBoy.from_rom(args.rom)

	if args.headless:
		rom_name = args.rom.name.lower()
		is_tma_write_reloading = rom_name == "tma_write_reloading.gb"
		is_boot_hwio_dmgabcmgb = rom_name == "boot_hwio-dmgabcmgb.gb"
		is_boot_div_dmgabcmgb = rom_name == "boot_div-dmgabcmgb.gb"
		is_boot_sclk_align_dmgabcmgb = rom_name == "boot_sclk_align-dmgabcmgb.gb"
		is_intr_2_0_timing = rom_name == "intr_2_0_timing.gb"
		if (
			args.print_results
			or is_tma_write_reloading
			or is_boot_hwio_dmgabcmgb
			or is_boot_div_dmgabcmgb
			or is_boot_sclk_align_dmgabcmgb
			or is_intr_2_0_timing
		):
			return _run_headless_with_results(
				args.rom,
				trace_last=256
				if (
					is_tma_write_reloading
					or is_boot_hwio_dmgabcmgb
					or is_boot_div_dmgabcmgb
					or is_boot_sclk_align_dmgabcmgb
					or is_intr_2_0_timing
				)
				else 0,
				dump_tma_write_reloading=is_tma_write_reloading,
				dump_boot_hwio_dmgabcmgb=is_boot_hwio_dmgabcmgb,
				dump_boot_div_dmgabcmgb=is_boot_div_dmgabcmgb,
				dump_boot_sclk_align_dmgabcmgb=is_boot_sclk_align_dmgabcmgb,
				dump_intr_2_0_timing=is_intr_2_0_timing,
			)

		for _ in range(120):
			gb.run_until_frame()
		return 0

	try:
		try:
			import sdl2dll  # type: ignore[import-not-found]
		except Exception:
			sdl2dll = None

		if sdl2dll is not None:
			set_fn = getattr(sdl2dll, "set_dll_path", None) or getattr(sdl2dll, "set_dllpath", None)
			get_fn = getattr(sdl2dll, "get_dllpath", None) or getattr(sdl2dll, "get_dll_path", None)
			if callable(set_fn):
				set_fn()
			elif callable(get_fn):
				dll_dir = get_fn()
				if dll_dir:
					import os
					if hasattr(os, "add_dll_directory"):
						os.add_dll_directory(dll_dir)
					else:
						os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")

		import ctypes
		import sdl2
	except ImportError as exc:
		raise SystemExit(
			"PySDL2 is not installed."
		) from exc

	if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_EVENTS) != 0:
		raise SystemExit(f"SDL_Init failed: {sdl2.SDL_GetError().decode('utf-8', 'replace')}")

	window = None
	renderer = None
	texture = None
	try:
		window_w = SCREEN_W * args.scale
		window_h = SCREEN_H * args.scale
		window = sdl2.SDL_CreateWindow(
			f"DotMatrixPy - {args.rom.name}".encode("utf-8"),
			sdl2.SDL_WINDOWPOS_CENTERED,
			sdl2.SDL_WINDOWPOS_CENTERED,
			window_w,
			window_h,
			sdl2.SDL_WINDOW_SHOWN,
		)
		if not window:
			raise SystemExit(f"SDL_CreateWindow failed: {sdl2.SDL_GetError().decode('utf-8', 'replace')}")

		renderer = sdl2.SDL_CreateRenderer(
			window,
			-1,
			sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC,
		)
		if not renderer:
			renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_SOFTWARE)
		if not renderer:
			raise SystemExit(
				f"SDL_CreateRenderer failed: {sdl2.SDL_GetError().decode('utf-8', 'replace')}"
			)

		texture = sdl2.SDL_CreateTexture(
			renderer,
			sdl2.SDL_PIXELFORMAT_RGB24,
			sdl2.SDL_TEXTUREACCESS_STREAMING,
			SCREEN_W,
			SCREEN_H,
		)
		if not texture:
			raise SystemExit(f"SDL_CreateTexture failed: {sdl2.SDL_GetError().decode('utf-8', 'replace')}")

		keymap = {
			sdl2.SDLK_RIGHT: "right",
			sdl2.SDLK_LEFT: "left",
			sdl2.SDLK_UP: "up",
			sdl2.SDLK_DOWN: "down",
			sdl2.SDLK_z: "a",
			sdl2.SDLK_x: "b",
			sdl2.SDLK_RETURN: "start",
			sdl2.SDLK_RSHIFT: "select",
		}

		running = True
		target_dt = 1.0 / max(1, int(args.fps))
		last_t = time.perf_counter()
		event = sdl2.SDL_Event()
		dst = sdl2.SDL_Rect(0, 0, window_w, window_h)
		pitch = SCREEN_W * 3
		pixels = (ctypes.c_uint8 * len(gb.frame_rgb)).from_buffer(gb.frame_rgb)

		while running:
			while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
				if event.type == sdl2.SDL_QUIT:
					running = False
				elif event.type == sdl2.SDL_KEYDOWN:
					keysym = event.key.keysym.sym
					if keysym == sdl2.SDLK_ESCAPE:
						running = False
					elif keysym in keymap:
						gb.bus.io.set_button(keymap[keysym], True)
				elif event.type == sdl2.SDL_KEYUP:
					keysym = event.key.keysym.sym
					if keysym in keymap:
						gb.bus.io.set_button(keymap[keysym], False)

			gb.run_until_frame()

			if sdl2.SDL_UpdateTexture(texture, None, pixels, pitch) != 0:
				raise SystemExit(
					f"SDL_UpdateTexture failed: {sdl2.SDL_GetError().decode('utf-8', 'replace')}"
				)
			sdl2.SDL_RenderClear(renderer)
			sdl2.SDL_RenderCopy(renderer, texture, None, dst)
			sdl2.SDL_RenderPresent(renderer)

			now_t = time.perf_counter()
			remaining = target_dt - (now_t - last_t)
			if remaining > 0:
				sdl2.SDL_Delay(int(remaining * 1000))
			last_t = time.perf_counter()

	finally:
		if texture:
			sdl2.SDL_DestroyTexture(texture)
		if renderer:
			sdl2.SDL_DestroyRenderer(renderer)
		if window:
			sdl2.SDL_DestroyWindow(window)
		sdl2.SDL_Quit()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
