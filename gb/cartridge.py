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


def _cart_has_battery(cart_type: int) -> bool:
    """Check if cartridge type has battery backup for save data."""
    ct = cart_type & 0xFF
    return ct in (
        0x03,  # MBC1+RAM+BATTERY
        0x06,  # MBC2+BATTERY
        0x09,  # ROM+RAM+BATTERY
        0x0D,  # MMM01+RAM+BATTERY
        0x0F,  # MBC3+TIMER+BATTERY
        0x10,  # MBC3+TIMER+RAM+BATTERY
        0x13,  # MBC3+RAM+BATTERY
        0x1B,  # MBC5+RAM+BATTERY
        0x1E,  # MBC5+RUMBLE+RAM+BATTERY
        0x22,  # MBC7+SENSOR+RUMBLE+RAM+BATTERY
        0xFF,  # HuC1+RAM+BATTERY
    )


def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _clamp_u8(x: int) -> int:
    return x & 0xFF


def _now_seconds() -> int:
    return int(time.time())


_NINTENDO_LOGO = bytes(
    [
        0xCE,
        0xED,
        0x66,
        0x66,
        0xCC,
        0x0D,
        0x00,
        0x0B,
        0x03,
        0x73,
        0x00,
        0x83,
        0x00,
        0x0C,
        0x00,
        0x0D,
        0x00,
        0x08,
        0x11,
        0x1F,
        0x88,
        0x89,
        0x00,
        0x0E,
        0xDC,
        0xCC,
        0x6E,
        0xE6,
        0xDD,
        0xDD,
        0xD9,
        0x99,
        0xBB,
        0xBB,
        0x67,
        0x63,
        0x6E,
        0x0E,
        0xEC,
        0xCC,
        0xDD,
        0xDC,
        0x99,
        0x9F,
        0xBB,
        0xB9,
        0x33,
        0x3E,
    ]
)


def _has_logo_at_bank(rom: bytes, bank: int) -> bool:
    base = bank * 0x4000
    off = base + 0x0104
    end = off + len(_NINTENDO_LOGO)
    return end <= len(rom) and rom[off:end] == _NINTENDO_LOGO


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

    def to_bytes(self) -> bytes:
        """Serialize RTC state to 48-byte VBA-M compatible format (little-endian)."""
        import struct
        self._sync()
        # Build day_hi byte: bit0 = day bit8, bit6 = halt, bit7 = carry
        day_hi = ((self.day >> 8) & 0x01) | (0x40 if self.halt else 0) | (0x80 if self.carry else 0)
        latched_day_hi = 0
        if self._latched_valid:
            ls, lm, lh, ld, lhalt, lcarry = self._latched
            latched_day_hi = ((ld >> 8) & 0x01) | (0x40 if lhalt else 0) | (0x80 if lcarry else 0)
        else:
            ls, lm, lh, ld = self.seconds, self.minutes, self.hours, self.day
        # 48-byte format:
        # 0-3: seconds, 4-7: minutes, 8-11: hours, 12-15: days (low 8 bits), 16-19: day_hi
        # 20-23: latched sec, 24-27: latched min, 28-31: latched hour, 32-35: latched day, 36-39: latched day_hi
        # 40-47: unix timestamp (64-bit)
        return struct.pack(
            "<IIIIIIIIIIQ",
            self.seconds & 0xFF,
            self.minutes & 0xFF,
            self.hours & 0xFF,
            self.day & 0xFF,
            day_hi & 0xFF,
            ls & 0xFF,
            lm & 0xFF,
            lh & 0xFF,
            ld & 0xFF,
            latched_day_hi & 0xFF,
            self._last_ts & 0xFFFFFFFFFFFFFFFF,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "_RTC":
        """Deserialize RTC state from 44 or 48-byte format."""
        import struct
        if len(data) < 44:
            return cls()
        # Support both 44-byte (32-bit timestamp) and 48-byte (64-bit timestamp)
        if len(data) >= 48:
            sec, min_, hr, day_lo, day_hi, lsec, lmin, lhr, lday_lo, lday_hi, ts = struct.unpack("<IIIIIIIIIIQ", data[:48])
        else:
            sec, min_, hr, day_lo, day_hi, lsec, lmin, lhr, lday_lo, lday_hi, ts32 = struct.unpack("<IIIIIIIIIII", data[:44])
            ts = ts32
        day = (day_lo & 0xFF) | ((day_hi & 0x01) << 8)
        halt = bool(day_hi & 0x40)
        carry = bool(day_hi & 0x80)
        lday = (lday_lo & 0xFF) | ((lday_hi & 0x01) << 8)
        lhalt = bool(lday_hi & 0x40)
        lcarry = bool(lday_hi & 0x80)
        rtc = cls(
            seconds=sec % 60,
            minutes=min_ % 60,
            hours=hr % 24,
            day=day & 0x1FF,
            halt=halt,
            carry=carry,
        )
        rtc._last_ts = ts if ts != 0x7FFFFFFF7FFFFFFF else _now_seconds()
        rtc._latched = (lsec % 60, lmin % 60, lhr % 24, lday & 0x1FF, lhalt, lcarry)
        rtc._latched_valid = True
        # Sync time to advance RTC based on saved timestamp
        rtc._sync()
        return rtc


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
    _mbc1_multicart: bool = False

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
        rom_banks = (
            actual_rom_banks
            if expected_rom_banks is None
            else max(1, min(max(expected_rom_banks, 1), actual_rom_banks))
        )

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
            if cart._rom_banks >= 64 and _has_logo_at_bank(data, 0x10):
                cart._mbc1_multicart = True

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
            raw5 = self._mbc1_low5 & 0x1F
            high2 = self._mbc1_high2 & 0x03

            eff5 = 1 if raw5 == 0 else raw5

            if self._mbc1_multicart:
                bank_hi = high2 << 4
                bank_lo = eff5 & 0x0F
            else:
                bank_hi = high2 << 5
                bank_lo = eff5

            if a <= 0x3FFF:
                bank0 = 0
                if self._mbc1_mode == MBC1Mode.RAM_BANKING:
                    bank0 = bank_hi
                b0 = self._rom_bank_index(bank0)
                return self.rom[b0 * 0x4000 + a] & 0xFF

            bank = bank_hi | bank_lo
            b = self._rom_bank_index(bank)
            return self.rom[b * 0x4000 + (a - 0x4000)] & 0xFF

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

    def has_battery(self) -> bool:
        """Check if this cartridge has battery backup for save data."""
        return _cart_has_battery(self.header.cartridge_type)

    def save_ram(self, path: str | Path) -> bool:
        """Save RAM (and RTC if applicable) to a .sav file.
        
        Returns True if data was saved, False if cartridge has no battery.
        """
        if not self.has_battery():
            return False
        if len(self.ram) == 0 and self._mbc3_rtc is None:
            return False
        
        path = Path(path)
        data = bytes(self.ram)
        
        # Append RTC data if MBC3 with RTC
        if self._mbc3_rtc is not None:
            data += self._mbc3_rtc.to_bytes()
        
        path.write_bytes(data)
        return True

    def load_ram(self, path: str | Path) -> bool:
        """Load RAM (and RTC if applicable) from a .sav file.
        
        Returns True if data was loaded, False if file doesn't exist or cartridge has no battery.
        """
        if not self.has_battery():
            return False
        
        path = Path(path)
        if not path.exists():
            return False
        
        try:
            data = path.read_bytes()
        except Exception:
            return False
        
        if len(data) == 0:
            return False
        
        ram_size = len(self.ram)
        
        # Detect RTC data: file is either 44 or 48 bytes larger than expected RAM
        has_rtc_data = False
        rtc_data = b""
        if self._mbc3_rtc is not None:
            if len(data) == ram_size + 48:
                has_rtc_data = True
                rtc_data = data[ram_size:ram_size + 48]
                data = data[:ram_size]
            elif len(data) == ram_size + 44:
                has_rtc_data = True
                rtc_data = data[ram_size:ram_size + 44]
                data = data[:ram_size]
            elif len(data) > ram_size:
                # Try to detect RTC at end (file may be padded)
                extra = len(data) - ram_size
                if extra >= 44:
                    has_rtc_data = True
                    rtc_data = data[ram_size:ram_size + min(extra, 48)]
                    data = data[:ram_size]
        
        # Load RAM data
        if ram_size > 0:
            copy_len = min(len(data), ram_size)
            self.ram[:copy_len] = data[:copy_len]
        
        # Load RTC data
        if has_rtc_data and self._mbc3_rtc is not None and len(rtc_data) >= 44:
            self._mbc3_rtc = _RTC.from_bytes(rtc_data)
        
        return True

    def get_save_path(self, rom_path: str | Path) -> Path:
        """Get the default save file path for a ROM."""
        rom_path = Path(rom_path)
        return rom_path.with_suffix(".sav")

