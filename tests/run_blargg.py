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


# Blargg tests output "Passed" or "Failed" in serial output
_PASSED_RE = re.compile(r"\bPassed\b", re.IGNORECASE)
_FAILED_RE = re.compile(r"\bFailed\b", re.IGNORECASE)


A000_SIG = (0xDE, 0xB0, 0x61)
A000_STATUS_RUNNING = 0x80
A000_STATUS_PASS = 0x00
A000_CHECK_INTERVAL = 256


def _iter_roms(rom_dir: Path, patterns: list[str], *, exclude_subdirs: set[str]) -> list[Path]:
	seen: set[Path] = set()
	roms: list[Path] = []
	for pattern in patterns:
		for p in rom_dir.glob(pattern):
			if not p.is_file():
				continue
			if exclude_subdirs and any(part in exclude_subdirs for part in p.parts):
				continue
			if p.suffix.lower() != ".gb":
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
	if _FAILED_RE.search(serial):
		return "FAIL"
	if _PASSED_RE.search(serial):
		return "PASS"
	return None


def _poll_a000_output(gb, state: dict[str, int | bool], *, max_chars: int = 256) -> tuple[int | None, str]:
	if not state.get("enabled", False):
		sig = (
			gb.bus.read_byte(0xA001) & 0xFF,
			gb.bus.read_byte(0xA002) & 0xFF,
			gb.bus.read_byte(0xA003) & 0xFF,
		)
		if sig != A000_SIG:
			return None, ""
		state["enabled"] = True
		state["addr"] = 0xA004

	status = gb.bus.read_byte(0xA000) & 0xFF
	addr = int(state.get("addr", 0xA004))
	out_chars: list[str] = []
	for _ in range(max_chars):
		b = gb.bus.read_byte(addr) & 0xFF
		if b == 0:
			break
		out_chars.append(chr(b))
		addr = (addr + 1) & 0xFFFF
	state["addr"] = addr
	return status, "".join(out_chars)


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
		a000_state: dict[str, int | bool] = {"enabled": False, "addr": 0xA004}
		next_a000_check = 0

		while True:
			if timeout_s > 0 and (time.monotonic() - start) >= timeout_s:
				return TestResult(rom=rom_path, status="TIMEOUT", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)
			if max_cycles > 0 and cycles >= max_cycles:
				return TestResult(rom=rom_path, status="TIMEOUT", serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)

			c = gb.step()
			cycles += int(c)

			if cycles >= next_a000_check:
				next_a000_check = cycles + A000_CHECK_INTERVAL
				a000_status, a000_text = _poll_a000_output(gb, a000_state)
				if a000_text:
					if print_serial:
						print(a000_text, end="", flush=True)
					serial = _tail_append(serial, a000_text, serial_tail)
				if a000_status is not None and a000_status != A000_STATUS_RUNNING:
					status = "PASS" if a000_status == A000_STATUS_PASS else "FAIL"
					return TestResult(rom=rom_path, status=status, serial=serial, elapsed_s=time.monotonic() - start, cycles=cycles)

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

	print("\n=== Blargg results ===")
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


# Available test suites
TEST_SUITES = {
	"cpu_instrs": "cpu_instrs",
	"instr_timing": "instr_timing",
	"mem_timing": "mem_timing",
	"mem_timing-2": "mem_timing-2",
	"dmg_sound": "dmg_sound",
	"oam_bug": "oam_bug",
	"halt_bug": "halt_bug.gb",
}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Headless runner for Blargg Test Suite ROMs")
	parser.add_argument(
		"--rom-dir",
		type=Path,
		default=None,
		help="Directory containing Blargg ROMs (searched recursively)",
	)
	parser.add_argument(
		"--suite",
		choices=list(TEST_SUITES.keys()) + ["all"],
		default="all",
		help="Test suite to run (default: all)",
	)
	parser.add_argument(
		"--pattern",
		action="append",
		default=None,
		help="Glob pattern under --rom-dir (repeatable). Default: **/*.gb",
	)
	parser.add_argument("--timeout-seconds", type=float, default=120.0, help="Per-ROM timeout in seconds (default: 120)")
	parser.add_argument("--max-cycles", type=int, default=500_000_000, help="Per-ROM max CPU cycles (default: 500,000,000)")
	parser.add_argument("--serial-tail", type=int, default=8192, help="Keep last N serial chars for detection/debug")
	parser.add_argument("--print-serial", action="store_true", help="Print serial output while running")
	parser.add_argument("--stop-on-fail", action="store_true", help="Stop at first FAIL/TIMEOUT/ERROR")
	parser.add_argument(
		"--exclude-subdir",
		action="append",
		default=None,
		help="Skip ROMs that have this path part (repeatable). Default: source",
	)
	parser.add_argument("--individual", action="store_true", help="Run individual sub-tests instead of main ROMs")
	parser.add_argument("--debug", action="store_true", help="Enable debug logging (CPU trace)")
	parser.add_argument("-v", "--verbose", action="store_true", help="Show progress for each ROM (hidden by default)")
	parser.add_argument("--list", action="store_true", help="List all available test suites and exit")
	args = parser.parse_args(argv)

	if args.list:
		print("Available Blargg test suites:")
		for name, path in TEST_SUITES.items():
			print(f"  {name}: {path}")
		return 0

	if args.debug:
		logging.basicConfig(level=logging.DEBUG, format="%(message)s")

	rom_dir: Path | None = args.rom_dir
	if rom_dir is None:
		candidate = Path("gb-test-roms")
		rom_dir = candidate if candidate.exists() else None

	if rom_dir is None:
		print("ERROR: --rom-dir is required (default gb-test-roms not found)", file=sys.stderr)
		return 2

	rom_dir = rom_dir.resolve()

	# Determine which ROMs to run
	if args.suite == "all":
		patterns = list(args.pattern) if args.pattern else ["**/*.gb"]
	else:
		suite_path = TEST_SUITES[args.suite]
		if suite_path.endswith(".gb"):
			# Single ROM file
			patterns = [suite_path]
		elif args.individual:
			# Run individual tests
			patterns = [f"{suite_path}/individual/*.gb"]
		else:
			# Run main ROM only
			patterns = [f"{suite_path}/*.gb"]

	default_excludes = {"source"}
	exclude_subdirs = set(args.exclude_subdir) if args.exclude_subdir else default_excludes

	# For --suite=all with individual, also include individual subdirs
	if args.suite == "all" and args.individual:
		patterns = ["**/individual/*.gb"]

	roms = _iter_roms(rom_dir, patterns, exclude_subdirs=exclude_subdirs)
	if not roms:
		extra = f" (excluded: {sorted(exclude_subdirs)!r})" if exclude_subdirs else ""
		print(f"ERROR: no ROMs found under {rom_dir} with pattern(s) {patterns!r}{extra}", file=sys.stderr)
		return 2

	extra = f" (excluded: {sorted(exclude_subdirs)!r})" if exclude_subdirs else ""
	print(f"Found {len(roms)} ROM(s) under {rom_dir} ({patterns}){extra}")

	results: list[TestResult] = []
	for i, rom in enumerate(roms, start=1):
		if args.verbose:
			print(f"[{i}/{len(roms)}] {rom.as_posix()} ...", end=" ", flush=True)
		r = run_one_rom(
			rom,
			timeout_s=float(args.timeout_seconds),
			max_cycles=int(args.max_cycles),
			serial_tail=int(args.serial_tail),
			print_serial=bool(args.print_serial),
		)
		results.append(r)
		if args.verbose:
			print(f"{r.status} ({r.elapsed_s:.2f}s, {r.cycles} cycles)")
		if args.stop_on_fail and r.status in ("FAIL", "TIMEOUT", "ERROR"):
			break

	return _print_summary(results)


if __name__ == "__main__":
	raise SystemExit(main())
