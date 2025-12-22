from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
	parser = argparse.ArgumentParser(description="Run a Game Boy ROM (minimal emulator)")
	parser.add_argument("rom", type=Path, help="Path to .gb/.gbc ROM")
	parser.add_argument("--scale", type=int, default=3, help="Window scale (default: 3)")
	parser.add_argument("--fps", type=int, default=60, help="FPS cap (default: 60)")
	parser.add_argument("--headless", action="store_true", help="Run without a window")
	args = parser.parse_args()

	from gb.gameboy import GameBoy
	from gb.ppu import SCREEN_H, SCREEN_W

	gb = GameBoy.from_rom(args.rom)

	if args.headless:
		for _ in range(120):
			gb.run_until_frame()
		return 0

	import pygame

	pygame.init()
	try:
		win = pygame.display.set_mode((SCREEN_W * args.scale, SCREEN_H * args.scale))
		surf = pygame.Surface((SCREEN_W, SCREEN_H))
		clock = pygame.time.Clock()
		running = True

		keymap_down = {
			pygame.K_RIGHT: ("right", True),
			pygame.K_LEFT: ("left", True),
			pygame.K_UP: ("up", True),
			pygame.K_DOWN: ("down", True),
			pygame.K_z: ("a", True),
			pygame.K_x: ("b", True),
			pygame.K_RETURN: ("start", True),
			pygame.K_RSHIFT: ("select", True),
			pygame.K_ESCAPE: ("quit", True),
		}
		keymap_up = {
			pygame.K_RIGHT: ("right", False),
			pygame.K_LEFT: ("left", False),
			pygame.K_UP: ("up", False),
			pygame.K_DOWN: ("down", False),
			pygame.K_z: ("a", False),
			pygame.K_x: ("b", False),
			pygame.K_RETURN: ("start", False),
			pygame.K_RSHIFT: ("select", False),
		}

		while running:
			for event in pygame.event.get():
				if event.type == pygame.QUIT:
					running = False
				elif event.type == pygame.KEYDOWN:
					if event.key in keymap_down:
						name, pressed = keymap_down[event.key]
						if name == "quit":
							running = False
						else:
							gb.bus.io.set_button(name, pressed)
				elif event.type == pygame.KEYUP:
					if event.key in keymap_up:
						name, pressed = keymap_up[event.key]
						gb.bus.io.set_button(name, pressed)

			gb.run_until_frame()

			frame_surface = pygame.image.frombuffer(gb.frame_rgb, (SCREEN_W, SCREEN_H), "RGB")
			surf.blit(frame_surface, (0, 0))
			scaled = pygame.transform.scale(surf, (SCREEN_W * args.scale, SCREEN_H * args.scale))
			win.blit(scaled, (0, 0))
			pygame.display.flip()
			clock.tick(args.fps)

	finally:
		pygame.quit()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
