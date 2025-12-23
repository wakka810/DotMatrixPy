from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
	sys.path.insert(0, str(_ROOT))


@dataclass(frozen=True)
class TestResult:
	rom: Path
	status: str
	serial: str
	elapsed_s: float
	cycles: int
	error: str | None = None


_FAIL_RE = re.compile(r"\\b(fail|failed|not\\s+ok)\\b", re.IGNORECASE)
_PASS_RE = re.compile(r"\\b(pass|passed|ok)\\b", re.IGNORECASE)

# Mooneye Test Suite pass/fail protocol (serial bytes):
# PASS: 03 05 08 0D 15 22  (Fibonacci numbers)
# FAIL: 42 42 42 42 42 42
_MOONEYE_PASS = bytes([3, 5, 8, 13, 21, 34])
_MOONEYE_FAIL = bytes([0x42] * 6)


def _iter_roms(rom_dir: Path, patterns: list[str], *, exclude_subdirs: set[str]) -> list[Path]:
	seen: set[Path] = set()
	roms: list[Path] = []
	for pattern in patterns:
		for p in rom_dir.glob(pattern):
			if not p.is_file():
				continue
			if exclude_subdirs and any(part in exclude_subdirs for part in p.parts):
				continue
			if p.suffix.lower() not in (".gb", ".gbc"):
				continue
			if p in seen:
				continue
			seen.add(p)
			roms.append(p)
	return sorted(roms)


def _tail_append(buf: str, add: str, keep: int) -> str:
	if not add:
		return buf
	buf = buf + add
	if len(buf) > keep:
		buf = buf[-keep:]
	return buf


def _detect_pass_fail(serial: str) -> str | None:
	try:
		b = bytes((ord(ch) & 0xFF) for ch in serial)
		if _MOONEYE_FAIL in b:
			return "FAIL"
		if _MOONEYE_PASS in b:
			return "PASS"
	except Exception:
		pass

	if _FAIL_RE.search(serial):
		return "FAIL"
	if _PASS_RE.search(serial):
		return "PASS"
	return None


def run_one_rom(
	rom_path: Path,
	*,
	timeout_s: float,
	max_cycles: int,
	serial_tail: int,
	print_serial: bool,
) -> TestResult:
	from gb.gameboy import GameBoy

	start = time.monotonic()
	cycles = 0
	serial = ""

	try:
		gb = GameBoy.from_rom(rom_path)

		pass_regs = (3, 5, 8, 13, 21, 34)
		fail_regs = (0x42, 0x42, 0x42, 0x42, 0x42, 0x42)

		while True:
			if timeout_s > 0 and (time.monotonic() - start) >= timeout_s:
				return TestResult(rom=rom_path, status="TIMEOUT", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)
			if max_cycles > 0 and cycles >= max_cycles:
				return TestResult(rom=rom_path, status="TIMEOUT", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)

			op = gb.bus.read_byte(gb.cpu.pc)
			if op == 0x40:
				r = gb.cpu.regs
				sig = (r.b & 0xFF, r.c & 0xFF, r.d & 0xFF, r.e & 0xFF, r.h & 0xFF, r.l & 0xFF)
				if sig == pass_regs:
					return TestResult(rom=rom_path, status="PASS", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)
				if sig == fail_regs:
					return TestResult(rom=rom_path, status="FAIL", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)

			c = gb.step()
			cycles += int(c)

			out = gb.bus.io.consume_serial_output()
			if out:
				if print_serial:
					print(out, end="", flush=True)
				serial = _tail_append(serial, out, serial_tail)

				status = _detect_pass_fail(serial)
				if status is not None:
					return TestResult(rom=rom_path, status=status, serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)

	except Exception as e:
		return TestResult(
			rom=rom_path,
			status="ERROR",
			serial=serial,
			elapsed_s=time.monotonic() - start,
			cycles=cycles,
			error=f"{type(e).__name__}: {e}",
		)


def _print_summary(results: Iterable[TestResult]) -> int:
	results = list(results)
	fail = [r for r in results if r.status == "FAIL"]
	timeout = [r for r in results if r.status == "TIMEOUT"]
	error = [r for r in results if r.status == "ERROR"]
	passed = [r for r in results if r.status == "PASS"]

	print("\n=== Mooneye results ===")
	print(f"PASS: {len(passed)}  FAIL: {len(fail)}  TIMEOUT: {len(timeout)}  ERROR: {len(error)}")

	for group_name, group in (("FAIL", fail), ("TIMEOUT", timeout), ("ERROR", error)):
		if not group:
			continue
		print(f"\n-- {group_name} --")
		for r in group:
			extra = f"  ({r.error})" if r.error else ""
			print(f"{r.rom.as_posix()}  [{r.status}]  {r.elapsed_s:.2f}s  {r.cycles} cycles{extra}")
			if r.serial:
				print(f"  serial_tail: {r.serial!r}")

	return 0 if (not fail and not timeout and not error) else 1


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Headless runner for Mooneye Test Suite ROMs")
	parser.add_argument(
		"--rom-dir",
		type=Path,
		default=None,
		help="Directory containing Mooneye ROMs (searched recursively)",
	)
	parser.add_argument(
		"--pattern",
		action="append",
		default=None,
		help="Glob pattern under --rom-dir (repeatable). Default: **/*.gb and **/*.gbc",
	)
	parser.add_argument("--timeout-seconds", type=float, default=20.0, help="Per-ROM timeout in seconds (default: 20)")
	parser.add_argument("--max-cycles", type=int, default=50_000_000, help="Per-ROM max CPU cycles (default: 50,000,000)")
	parser.add_argument("--serial-tail", type=int, default=4096, help="Keep last N serial chars for detection/debug")
	parser.add_argument("--print-serial", action="store_true", help="Print serial output while running")
	parser.add_argument("--stop-on-fail", action="store_true", help="Stop at first FAIL/TIMEOUT/ERROR")
	parser.add_argument(
		"--exclude-subdir",
		action="append",
		default=None,
		help="Skip ROMs that have this path part (repeatable). Default: manual-only, madness, utils",
	)
	parser.add_argument("--debug", action="store_true", help="Enable debug logging (CPU trace)")
	args = parser.parse_args(argv)

	if args.debug:
		logging.basicConfig(level=logging.DEBUG, format="%(message)s")

	rom_dir: Path | None = args.rom_dir
	if rom_dir is None:
		candidate = Path("test_roms") / "mooneye"
		rom_dir = candidate if candidate.exists() else None

	if rom_dir is None:
		print("ERROR: --rom-dir is required (default test_roms/mooneye not found)", file=sys.stderr)
		return 2

	rom_dir = rom_dir.resolve()
	patterns = list(args.pattern) if args.pattern else ["**/*.gb", "**/*.gbc"]
	default_excludes = {"manual-only", "madness", "utils"}
	exclude_subdirs = set(args.exclude_subdir) if args.exclude_subdir else default_excludes
	roms = _iter_roms(rom_dir, patterns, exclude_subdirs=exclude_subdirs)
	if not roms:
		extra = f" (excluded: {sorted(exclude_subdirs)!r})" if exclude_subdirs else ""
		print(f"ERROR: no ROMs found under {rom_dir} with pattern(s) {patterns!r}{extra}", file=sys.stderr)
		return 2

	extra = f" (excluded: {sorted(exclude_subdirs)!r})" if exclude_subdirs else ""
	print(f"Found {len(roms)} ROM(s) under {rom_dir} ({patterns}){extra}")

	results: list[TestResult] = []
	for i, rom in enumerate(roms, start=1):
		print(f"[{i}/{len(roms)}] {rom.as_posix()} ...", end=" ", flush=True)
		r = run_one_rom(
			rom,
			timeout_s=float(args.timeout_seconds),
			max_cycles=int(args.max_cycles),
			serial_tail=int(args.serial_tail),
			print_serial=bool(args.print_serial),
		)
		results.append(r)
		print(f"{r.status} ({r.elapsed_s:.2f}s, {r.cycles} cycles)")
		if args.stop_on_fail and r.status in ("FAIL", "TIMEOUT", "ERROR"):
			break

	return _print_summary(results)


if __name__ == "__main__":
	raise SystemExit(main())
