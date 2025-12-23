from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path


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
