from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional
import time


class MapperKind(IntEnum):
    ROM_ONLY = 0
    MBC1 = 1
    MBC2 = 2
    MBC3 = 3
    MBC5 = 5
    UNKNOWN = 255


class MBC1Mode(IntEnum):
    ROM_BANKING = 0
    RAM_BANKING = 1


@dataclass(frozen=True)
class CartridgeHeader:
    title: str
    cgb_flag: int
    new_licensee_code: int
    sgb_flag: int
    cartridge_type: int
    rom_size_code: int
    ram_size_code: int
    destination_code: int
    old_licensee_code: int
    mask_rom_version: int
    header_checksum: int
    global_checksum: int


def _decode_title_from_rom(rom: bytes) -> str:
    cgb_flag = rom[0x0143] & 0xFF
    raw = rom[0x0134:0x013F] if (cgb_flag & 0x80) else rom[0x0134:0x0144]
    raw = raw.split(b"\x00", 1)[0]
    try:
        return raw.decode("ascii", errors="replace")
    except Exception:
        return ""


def _rom_banks_from_code(code: int) -> Optional[int]:
    code &= 0xFF
    if 0x00 <= code <= 0x08:
        return 2 << code
    if code == 0x52:
        return 72
    if code == 0x53:
        return 80
    if code == 0x54:
        return 96
    return None


def _ram_size_from_code(code: int) -> int:
    code &= 0xFF
    if code == 0x00:
        return 0
    if code == 0x01:
        return 0
    if code == 0x02:
        return 0x2000
    if code == 0x03:
        return 0x8000
    if code == 0x04:
        return 0x20000
    if code == 0x05:
        return 0x10000
    return 0


def _cart_mapper_kind(cart_type: int) -> MapperKind:
    ct = cart_type & 0xFF
    if ct in (0x00, 0x08, 0x09):
        return MapperKind.ROM_ONLY
    if ct in (0x01, 0x02, 0x03):
        return MapperKind.MBC1
    if ct in (0x05, 0x06):
        return MapperKind.MBC2
    if ct in (0x0F, 0x10, 0x11, 0x12, 0x13):
        return MapperKind.MBC3
    if ct in (0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E):
        return MapperKind.MBC5
    return MapperKind.UNKNOWN


def _cart_has_external_ram(cart_type: int) -> bool:
    ct = cart_type & 0xFF
    return ct in (
        0x02,
        0x03,
        0x08,
        0x09,
        0x0C,
        0x0D,
        0x10,
        0x12,
        0x13,
        0x1A,
        0x1B,
        0x1D,
        0x1E,
        0x22,
        0xFC,
        0xFF,
    )


def _cart_has_rtc(cart_type: int) -> bool:
    ct = cart_type & 0xFF
    return ct in (0x0F, 0x10)


def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _clamp_u8(x: int) -> int:
    return x & 0xFF


def _now_seconds() -> int:
    return int(time.time())


@dataclass
class _RTC:
    seconds: int = 0
    minutes: int = 0
    hours: int = 0
    day: int = 0
    halt: bool = False
    carry: bool = False
    _last_ts: int = field(default_factory=_now_seconds)
    _latched: tuple[int, int, int, int, bool, bool] = (0, 0, 0, 0, False, False)
    _latched_valid: bool = False
    _latch_prev: int = 0

    def _sync(self) -> None:
        now = _now_seconds()
        if self.halt:
            self._last_ts = now
            return
        delta = now - self._last_ts
        if delta <= 0:
            return
        total = self.seconds + self.minutes * 60 + self.hours * 3600 + self.day * 86400
        total += delta
        days_added, rem = divmod(total, 86400)
        day_total = self.day + days_added
        if day_total >= 512:
            self.carry = True
            day_total %= 512
        self.day = day_total
        self.hours, rem = divmod(rem, 3600)
        self.minutes, self.seconds = divmod(rem, 60)
        self._last_ts = now

    def latch_write(self, value: int) -> None:
        v = value & 0xFF
        if (self._latch_prev & 0xFF) == 0x00 and v == 0x01:
            self._sync()
            self._latched = (self.seconds, self.minutes, self.hours, self.day, self.halt, self.carry)
            self._latched_valid = True
        self._latch_prev = v

    def _get_fields(self, latched: bool) -> tuple[int, int, int, int, bool, bool]:
        if latched and self._latched_valid:
            return self._latched
        self._sync()
        return (self.seconds, self.minutes, self.hours, self.day, self.halt, self.carry)

    def read_reg(self, reg: int) -> int:
        r = reg & 0xFF
        s, m, h, d, halt, carry = self._get_fields(latched=True)
        if r == 0x08:
            return s & 0x3F
        if r == 0x09:
            return m & 0x3F
        if r == 0x0A:
            return h & 0x1F
        if r == 0x0B:
            return d & 0xFF
        if r == 0x0C:
            day_hi = (1 if (d & 0x100) else 0) | (0x40 if halt else 0) | (0x80 if carry else 0)
            return day_hi & 0xFF
        return 0xFF

    def write_reg(self, reg: int, value: int) -> None:
        r = reg & 0xFF
        v = value & 0xFF
        self._sync()
        if r == 0x08:
            self.seconds = v % 60
            self._last_ts = _now_seconds()
            return
        if r == 0x09:
            self.minutes = v % 60
            self._last_ts = _now_seconds()
            return
        if r == 0x0A:
            self.hours = v % 24
            self._last_ts = _now_seconds()
            return
        if r == 0x0B:
            self.day = (self.day & 0x100) | v
            self._last_ts = _now_seconds()
            return
        if r == 0x0C:
            new_day = (self.day & 0xFF) | ((v & 0x01) << 8)
            new_halt = bool(v & 0x40)
            new_carry = bool(v & 0x80)
            if self.halt != new_halt:
                self._last_ts = _now_seconds()
            self.day = new_day & 0x1FF
            self.halt = new_halt
            self.carry = new_carry
            return


@dataclass
class Cartridge:
    rom: bytes
    header: CartridgeHeader
    ram: bytearray

    _mapper: MapperKind = MapperKind.UNKNOWN
    _rom_banks: int = 1
    _ram_banks: int = 0

    _ram_enabled: bool = False

    _mbc1_low5: int = 1
    _mbc1_high2: int = 0
    _mbc1_mode: MBC1Mode = MBC1Mode.ROM_BANKING
    _mbc1_large_rom_wiring: bool = False

    _mbc2_bank: int = 1

    _mbc3_bank: int = 1
    _mbc3_sel: int = 0
    _mbc3_rtc: Optional[_RTC] = None

    _mbc5_bank: int = 1
    _mbc5_bank_hi: int = 0
    _mbc5_ram_bank: int = 0
    _mbc5_rumble: bool = False

    @classmethod
    def from_file(cls, path: str | Path) -> "Cartridge":
        data = Path(path).read_bytes()
        return cls.from_bytes(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Cartridge":
        if len(data) < 0x150:
            raise ValueError("ROM image too small to contain a valid header")

        header = CartridgeHeader(
            title=_decode_title_from_rom(data),
            cgb_flag=_clamp_u8(data[0x0143]),
            new_licensee_code=((data[0x0144] << 8) | data[0x0145]) & 0xFFFF,
            sgb_flag=_clamp_u8(data[0x0146]),
            cartridge_type=_clamp_u8(data[0x0147]),
            rom_size_code=_clamp_u8(data[0x0148]),
            ram_size_code=_clamp_u8(data[0x0149]),
            destination_code=_clamp_u8(data[0x014A]),
            old_licensee_code=_clamp_u8(data[0x014B]),
            mask_rom_version=_clamp_u8(data[0x014C]),
            header_checksum=_clamp_u8(data[0x014D]),
            global_checksum=((data[0x014E] << 8) | data[0x014F]) & 0xFFFF,
        )

        mapper = _cart_mapper_kind(header.cartridge_type)
        actual_rom_banks = max(1, len(data) // 0x4000)
        expected_rom_banks = _rom_banks_from_code(header.rom_size_code)
        rom_banks = actual_rom_banks if expected_rom_banks is None else max(1, min(max(expected_rom_banks, 1), actual_rom_banks))

        if mapper == MapperKind.MBC2:
            ram = bytearray(0x200)
        else:
            ram_size = _ram_size_from_code(header.ram_size_code)
            if ram_size == 0 and _cart_has_external_ram(header.cartridge_type):
                ram_size = 0x2000
            ram = bytearray(ram_size)

        cart = cls(rom=data, header=header, ram=ram)
        cart._mapper = mapper
        cart._rom_banks = rom_banks
        cart._ram_banks = (len(ram) // 0x2000) if len(ram) else 0

        if mapper == MapperKind.MBC1:
            rb = _rom_banks_from_code(header.rom_size_code)
            cart._mbc1_large_rom_wiring = bool(rb is not None and rb >= 64)
        if mapper == MapperKind.MBC3:
            cart._mbc3_rtc = _RTC() if _cart_has_rtc(header.cartridge_type) else None
        return cart

    def mapper_name(self) -> str:
        if self._mapper == MapperKind.ROM_ONLY:
            return "ROM_ONLY"
        if self._mapper == MapperKind.MBC1:
            return "MBC1"
        if self._mapper == MapperKind.MBC2:
            return "MBC2"
        if self._mapper == MapperKind.MBC3:
            return "MBC3"
        if self._mapper == MapperKind.MBC5:
            return "MBC5"
        return "UNKNOWN"

    def _rom_bank_index(self, bank: int) -> int:
        if self._rom_banks <= 0:
            return 0
        b = bank % self._rom_banks
        if b < 0:
            b += self._rom_banks
        return b

    def _ram_bank_index(self, bank: int) -> int:
        if self._ram_banks <= 0:
            return 0
        b = bank % self._ram_banks
        if b < 0:
            b += self._ram_banks
        return b

    def read_rom(self, address: int) -> int:
        a = address & 0x7FFF
        if self._mapper == MapperKind.ROM_ONLY or self._mapper == MapperKind.UNKNOWN:
            return self.rom[a] & 0xFF

        if self._mapper == MapperKind.MBC1:
            if a <= 0x3FFF:
                bank0 = 0
                if self._mbc1_mode == MBC1Mode.RAM_BANKING:
                    bank0 = (self._mbc1_high2 & 0x03) << 5
                b = self._rom_bank_index(bank0)
                idx = b * 0x4000 + a
                return self.rom[idx] & 0xFF
            bank = ((self._mbc1_high2 & 0x03) << 5) | (self._mbc1_low5 & 0x1F)
            if (bank & 0x1F) == 0:
                bank |= 1
            b = self._rom_bank_index(bank)
            idx = b * 0x4000 + (a - 0x4000)
            return self.rom[idx] & 0xFF

        if self._mapper == MapperKind.MBC2:
            if a <= 0x3FFF:
                return self.rom[a] & 0xFF
            b = self._rom_bank_index(self._mbc2_bank & 0x0F)
            idx = b * 0x4000 + (a - 0x4000)
            return self.rom[idx] & 0xFF

        if self._mapper == MapperKind.MBC3:
            if a <= 0x3FFF:
                return self.rom[a] & 0xFF
            bank = self._mbc3_bank & 0x7F
            if bank == 0:
                bank = 1
            b = self._rom_bank_index(bank)
            idx = b * 0x4000 + (a - 0x4000)
            return self.rom[idx] & 0xFF

        if self._mapper == MapperKind.MBC5:
            if a <= 0x3FFF:
                return self.rom[a] & 0xFF
            bank = ((self._mbc5_bank_hi & 0x01) << 8) | (self._mbc5_bank & 0xFF)
            b = self._rom_bank_index(bank)
            idx = b * 0x4000 + (a - 0x4000)
            return self.rom[idx] & 0xFF

        return self.rom[a] & 0xFF

    def write_rom(self, address: int, value: int) -> None:
        a = address & 0x7FFF
        v = value & 0xFF

        if self._mapper == MapperKind.MBC1:
            if a <= 0x1FFF:
                self._ram_enabled = (v & 0x0F) == 0x0A
                return
            if 0x2000 <= a <= 0x3FFF:
                self._mbc1_low5 = v & 0x1F
                if self._mbc1_low5 == 0:
                    self._mbc1_low5 = 1
                return
            if 0x4000 <= a <= 0x5FFF:
                self._mbc1_high2 = v & 0x03
                return
            if 0x6000 <= a <= 0x7FFF:
                self._mbc1_mode = MBC1Mode.RAM_BANKING if (v & 0x01) else MBC1Mode.ROM_BANKING
                return

        if self._mapper == MapperKind.MBC2:
            if a <= 0x3FFF:
                if (a & 0x0100) == 0:
                    self._ram_enabled = (v & 0x0F) == 0x0A
                else:
                    bank = v & 0x0F
                    if bank == 0:
                        bank = 1
                    self._mbc2_bank = bank
                return

        if self._mapper == MapperKind.MBC3:
            if a <= 0x1FFF:
                self._ram_enabled = (v & 0x0F) == 0x0A
                return
            if 0x2000 <= a <= 0x3FFF:
                bank = v & 0x7F
                if bank == 0:
                    bank = 1
                self._mbc3_bank = bank
                return
            if 0x4000 <= a <= 0x5FFF:
                self._mbc3_sel = v & 0xFF
                return
            if 0x6000 <= a <= 0x7FFF:
                if self._mbc3_rtc is not None:
                    self._mbc3_rtc.latch_write(v)
                return

        if self._mapper == MapperKind.MBC5:
            if a <= 0x1FFF:
                self._ram_enabled = (v & 0x0F) == 0x0A
                return
            if 0x2000 <= a <= 0x2FFF:
                self._mbc5_bank = v
                return
            if 0x3000 <= a <= 0x3FFF:
                self._mbc5_bank_hi = v & 0x01
                return
            if 0x4000 <= a <= 0x5FFF:
                self._mbc5_ram_bank = v & 0x0F
                self._mbc5_rumble = bool(v & 0x08) and (self.header.cartridge_type & 0xFF) in (0x1C, 0x1D, 0x1E)
                return
            return

    def _mbc1_ram_bank(self) -> int:
        if self._mbc1_large_rom_wiring:
            return 0
        if self._mbc1_mode == MBC1Mode.RAM_BANKING:
            return self._mbc1_high2 & 0x03
        return 0

    def read_ram(self, address: int) -> int:
        a = address & 0x1FFF

        if self._mapper == MapperKind.MBC2:
            if not self._ram_enabled:
                return 0xFF
            idx = a & 0x01FF
            return (self.ram[idx] & 0x0F) | 0xF0

        if len(self.ram) == 0:
            return 0xFF

        if self._mapper == MapperKind.ROM_ONLY or self._mapper == MapperKind.UNKNOWN:
            idx = a % len(self.ram)
            return self.ram[idx] & 0xFF

        if self._mapper == MapperKind.MBC1:
            if not self._ram_enabled:
                return 0xFF
            bank = self._mbc1_ram_bank()
            b = self._ram_bank_index(bank)
            idx = b * 0x2000 + a
            return self.ram[idx] & 0xFF

        if self._mapper == MapperKind.MBC3:
            if not self._ram_enabled:
                return 0xFF
            sel = self._mbc3_sel & 0xFF
            if 0x08 <= sel <= 0x0C and self._mbc3_rtc is not None:
                return self._mbc3_rtc.read_reg(sel) & 0xFF
            if self._ram_banks == 0:
                return 0xFF
            bank = sel & 0x03
            b = self._ram_bank_index(bank)
            idx = b * 0x2000 + a
            return self.ram[idx] & 0xFF

        if self._mapper == MapperKind.MBC5:
            if not self._ram_enabled:
                return 0xFF
            if self._ram_banks == 0:
                return 0xFF
            b = self._ram_bank_index(self._mbc5_ram_bank)
            idx = b * 0x2000 + a
            return self.ram[idx] & 0xFF

        return 0xFF

    def write_ram(self, address: int, value: int) -> None:
        a = address & 0x1FFF
        v = value & 0xFF

        if self._mapper == MapperKind.MBC2:
            if not self._ram_enabled or len(self.ram) == 0:
                return
            idx = a & 0x01FF
            self.ram[idx] = v & 0x0F
            return

        if len(self.ram) == 0:
            return

        if self._mapper == MapperKind.ROM_ONLY or self._mapper == MapperKind.UNKNOWN:
            idx = a % len(self.ram)
            self.ram[idx] = v
            return

        if self._mapper == MapperKind.MBC1:
            if not self._ram_enabled:
                return
            bank = self._mbc1_ram_bank()
            b = self._ram_bank_index(bank)
            idx = b * 0x2000 + a
            self.ram[idx] = v
            return

        if self._mapper == MapperKind.MBC3:
            if not self._ram_enabled:
                return
            sel = self._mbc3_sel & 0xFF
            if 0x08 <= sel <= 0x0C and self._mbc3_rtc is not None:
                self._mbc3_rtc.write_reg(sel, v)
                return
            if self._ram_banks == 0:
                return
            bank = sel & 0x03
            b = self._ram_bank_index(bank)
            idx = b * 0x2000 + a
            self.ram[idx] = v
            return

        if self._mapper == MapperKind.MBC5:
            if not self._ram_enabled:
                return
            if self._ram_banks == 0:
                return
            b = self._ram_bank_index(self._mbc5_ram_bank)
            idx = b * 0x2000 + a
            self.ram[idx] = v
            return
