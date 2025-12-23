from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
	sys.path.insert(0, str(_ROOT))


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Micro-benchmark: run a ROM headless and report cycles/sec")
	parser.add_argument("rom", type=Path, help="Path to .gb/.gbc ROM")
	parser.add_argument("--seconds", type=float, default=2.0, help="Benchmark duration (default: 2.0)")
	parser.add_argument("--repeat", type=int, default=5, help="Number of measured runs (default: 5)")
	parser.add_argument("--warmup", type=float, default=0.5, help="Warmup seconds before measuring (default: 0.5)")
	args = parser.parse_args(argv)

	from gb.gameboy import GameBoy

	gb = GameBoy.from_rom(args.rom)

	warm_end = time.perf_counter() + float(args.warmup)
	while time.perf_counter() < warm_end:
		gb.step()

	repeat = int(args.repeat)
	if repeat <= 0:
		repeat = 1

	cps_list: list[float] = []
	sps_list: list[float] = []
	cycles_total = 0
	steps_total = 0
	seconds = float(args.seconds)

	for _ in range(repeat):
		start = time.perf_counter()
		end = start + seconds
		steps = 0
		cycles = 0
		while True:
			now = time.perf_counter()
			if now >= end:
				break
			c = gb.step()
			cycles += int(c)
			steps += 1

		elapsed = time.perf_counter() - start
		cps = cycles / elapsed if elapsed > 0 else 0.0
		sps = steps / elapsed if elapsed > 0 else 0.0
		cps_list.append(cps)
		sps_list.append(sps)
		cycles_total += cycles
		steps_total += steps

	cps_avg = statistics.fmean(cps_list)
	sps_avg = statistics.fmean(sps_list)
	cps_min = min(cps_list)
	cps_max = max(cps_list)

	print(
		f"dispatch=on  repeat={repeat}  seconds={seconds:.2f}  "
		f"cycles_total={cycles_total}  steps_total={steps_total}  "
		f"cycles/s(avg)={cps_avg:,.0f}  cycles/s(min..max)={cps_min:,.0f}..{cps_max:,.0f}  "
		f"steps/s(avg)={sps_avg:,.0f}"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
