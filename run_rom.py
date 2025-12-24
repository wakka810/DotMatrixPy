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


def _run_headless_with_results(
	rom: Path,
	*,
	max_cycles: int = 80_000_000,
	timeout_s: float = 20.0,
	serial_tail: int = 4096,
	trace_last: int = 0,
	dump_lcdon_timing_gs: bool = False,
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
	if dump_lcdon_timing_gs:
		_dump_lcdon_timing_gs(gb, trace=trace)

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
		is_lcdon_timing_gs = rom_name == "lcdon_timing-gs.gb"
		if args.print_results or is_lcdon_timing_gs:
			return _run_headless_with_results(
				args.rom,
				trace_last=128 if is_lcdon_timing_gs else 0,
				dump_lcdon_timing_gs=is_lcdon_timing_gs,
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
