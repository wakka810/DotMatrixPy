from __future__ import annotations

from dataclasses import dataclass, field


TIMER_INTERRUPT_MASK = 1 << 2
SERIAL_INTERRUPT_MASK = 1 << 3
JOYPAD_INTERRUPT_MASK = 1 << 4


_DMG_UNUSED_OFFSETS = frozenset(
    {0x03, 0x15, 0x1F}
    | set(range(0x08, 0x0F))
    | set(range(0x27, 0x30))
    | set(range(0x4C, 0x50))
    | set(range(0x51, 0x56))
    | set(range(0x56, 0x68))
    | set(range(0x68, 0x70))
    | set(range(0x70, 0x80))
)


@dataclass(slots=True)
class IO:
    regs: bytearray = field(default_factory=lambda: bytearray(0x80))
    interrupt_flag: int = 0xE1
    interrupt_enable: int = 0x00
    cgb_mode: bool = False
    double_speed: bool = False
    key1_prepare: bool = False

    _dpad_state: int = 0x0F
    _btn_state: int = 0x0F

    _serial_out: list[str] = field(default_factory=list)

    _div_counter: int = 0
    _apu_div_ticks_pending: int = 0
    _global_cycles: int = 0

    _tima_reload_pending: bool = False
    _tima_reload_counter: int = 0
    _tima_overflow_cancel_until: int = 0

    _serial_active: bool = False
    _serial_internal_clock: bool = True
    _serial_cycle_acc: int = 0
    _serial_bits_left: int = 0
    _serial_latch_out: int = 0
    _sc_pending_offset: int | None = None
    _sc_pending_value: int = 0
    _div_reset_pending_offset: int | None = None
    _tac_pending_offset: int | None = None
    _tac_pending_value: int = 0
    _tac_pending_old: int = 0
    _tima_pending_offset: int | None = None
    _tima_pending_value: int = 0
    _tma_pending_offset: int | None = None
    _tma_pending_value: int = 0
    _stop_mode: bool = False
    _stop_wake_delay: int = 0

    _STOP_WAKE_DELAY_CYCLES: int = 217

    def __post_init__(self) -> None:
        if len(self.regs) != 0x80:
            self.regs = bytearray(0x80)
        self._init_post_boot_dmg()

    def _init_post_boot_dmg(self) -> None:
        self.regs[:] = b"\x00" * 0x80

        self.regs[0x00] = 0x00
        self.regs[0x01] = 0x00
        self.regs[0x02] = 0x00
        self.regs[0x05] = 0x00
        self.regs[0x06] = 0x00
        self.regs[0x07] = 0x00

        self.interrupt_flag = 0xE1
        self.interrupt_enable = 0x00

        self._div_counter = 0xABCC
        self.regs[0x04] = (self._div_counter >> 8) & 0xFF
        self._apu_div_ticks_pending = 0

        self.regs[0x10] = 0x80
        self.regs[0x11] = 0xBF
        self.regs[0x12] = 0xF3
        self.regs[0x13] = 0xFF
        self.regs[0x14] = 0xBF
        self.regs[0x16] = 0x3F
        self.regs[0x17] = 0x00
        self.regs[0x18] = 0xFF
        self.regs[0x19] = 0xBF
        self.regs[0x1A] = 0x7F
        self.regs[0x1B] = 0xFF
        self.regs[0x1C] = 0x9F
        self.regs[0x1D] = 0xFF
        self.regs[0x1E] = 0xBF
        self.regs[0x20] = 0xFF
        self.regs[0x21] = 0x00
        self.regs[0x22] = 0x00
        self.regs[0x23] = 0xBF
        self.regs[0x24] = 0x77
        self.regs[0x25] = 0xF3
        self.regs[0x26] = 0xF1

        self.regs[0x40] = 0x91
        self.regs[0x41] = 0x85
        self.regs[0x42] = 0x00
        self.regs[0x43] = 0x00
        self.regs[0x44] = 0x00
        self.regs[0x45] = 0x00
        self.regs[0x46] = 0xFF
        self.regs[0x47] = 0xFC
        self.regs[0x48] = 0xFF
        self.regs[0x49] = 0xFF
        self.regs[0x4A] = 0x00
        self.regs[0x4B] = 0x00
        self.regs[0x50] = 0x01

        self._dpad_state = 0x0F
        self._btn_state = 0x0F

        self._tima_reload_pending = False
        self._tima_reload_counter = 0
        self._tima_overflow_cancel_until = 0

        self._serial_active = False
        self._serial_internal_clock = True
        self._serial_cycle_acc = 0
        self._serial_bits_left = 0
        self._serial_latch_out = 0
        self._serial_out.clear()
        self._sc_pending_offset = None
        self._sc_pending_value = 0

        self._global_cycles = 0
        self._div_reset_pending_offset = None
        self._tac_pending_offset = None
        self._tac_pending_value = 0
        self._tac_pending_old = 0
        self._tima_pending_offset = None
        self._tima_pending_value = 0
        self._tma_pending_offset = None
        self._tma_pending_value = 0
        self.double_speed = False
        self.key1_prepare = False
        self._stop_mode = False
        self._stop_wake_delay = 0

    def enter_stop(self) -> None:
        self._apply_div_reset()
        self._stop_mode = True
        self._stop_wake_delay = 0

    def exit_stop(self) -> None:
        self._stop_mode = False
        self._stop_wake_delay = 0

    def start_stop_wake_delay(self) -> None:
        if self._stop_wake_delay <= 0:
            self._stop_wake_delay = int(self._STOP_WAKE_DELAY_CYCLES)

    def tick_stop_wake_delay(self, cycles: int) -> bool:
        if self._stop_wake_delay <= 0:
            return True
        self._stop_wake_delay -= int(cycles)
        if self._stop_wake_delay <= 0:
            self._stop_wake_delay = 0
            return True
        return False

    def stop_wake_delay_active(self) -> bool:
        return self._stop_wake_delay > 0

    def stop_wake_requested(self) -> bool:
        return (self._dpad_state & 0x0F) != 0x0F or (self._btn_state & 0x0F) != 0x0F

    def _timer_bit(self, tac: int) -> int:
        sel = tac & 0x03
        if sel == 0:
            return 9
        if sel == 1:
            return 3
        if sel == 2:
            return 5
        return 7

    def _timer_input(self, tac: int, div_counter: int) -> int:
        if (tac & 0x04) == 0:
            return 0
        return 1 if (div_counter & (1 << self._timer_bit(tac))) else 0

    def _apu_div_bit(self) -> int:
        return 13 if self.double_speed else 12

    def _count_apu_div_falling_edges(self, old_div: int, new_div: int) -> int:
        bit = self._apu_div_bit()
        period = 1 << (bit + 1)
        if new_div >= old_div:
            return (new_div // period) - (old_div // period)
        return (0x10000 // period - (old_div // period)) + (new_div // period)

    def _tima_increment(self, *, global_cycles_before: int | None = None) -> bool:
        if self._tima_reload_pending:
            return False
        if global_cycles_before is None:
            global_cycles_before = self._global_cycles
        tima = self.regs[0x05]
        if tima == 0xFF:
            self.regs[0x05] = 0x00
            self._tima_reload_pending = True
            self._tima_reload_counter = 4
            now = int(global_cycles_before) + 1
            self._tima_overflow_cancel_until = (now + 3) & ~3
            return True
        else:
            self.regs[0x05] = (tima + 1) & 0xFF
            return False

    def _tick_overflow_reload(self) -> None:
        if not self._tima_reload_pending:
            return
        self._tima_reload_counter -= 1
        if self._tima_reload_counter > 0:
            return
        self._tima_reload_pending = False
        self.regs[0x05] = self.regs[0x06]
        self.request_interrupt(TIMER_INTERRUPT_MASK)

    def _joyp_low(self, sel: int) -> int:
        low = 0x0F
        if (sel & 0x10) == 0:
            low &= self._dpad_state
        if (sel & 0x20) == 0:
            low &= self._btn_state
        return low & 0x0F

    def _maybe_joypad_irq(self, old_low: int, new_low: int) -> None:
        if (old_low & (~new_low)) & 0x0F:
            self.request_interrupt(JOYPAD_INTERRUPT_MASK)

    def _apply_reload_if_due(self) -> None:
        if not self._tima_reload_pending:
            return
        if self._tima_reload_counter > 0:
            return
        self._tima_reload_pending = False
        self._tima_reload_counter = 0
        self.regs[0x05] = self.regs[0x06]
        self.request_interrupt(TIMER_INTERRUPT_MASK)

    def _tick_basic(self, cycles: int, *, defer_reload_at: int | None = None) -> None:
        cycles = int(cycles)
        if cycles <= 0:
            return

        regs = self.regs
        gc_start = self._global_cycles
        div_counter = self._div_counter & 0xFFFF

        tac = regs[0x07] & 0x07
        timer_enabled = (tac & 0x04) != 0
        timer_period = 0
        timer_mask = 0
        if timer_enabled:
            bit = self._timer_bit(tac)
            timer_period = 1 << (bit + 1)
            timer_mask = timer_period - 1

        remaining = cycles
        processed = 0
        while remaining > 0:
            step = remaining

            tima_pending_start = self._tima_reload_pending
            serial_active_start = self._serial_active and self._serial_internal_clock

            to_fall = step + 1
            if timer_enabled and (not tima_pending_start):
                to_fall = timer_period - (div_counter & timer_mask)

            to_reload = step + 1
            if tima_pending_start:
                to_reload = int(self._tima_reload_counter)

            to_shift = step + 1
            if serial_active_start:
                to_shift = 512 - int(self._serial_cycle_acc)

            step = min(step, to_fall, to_reload, to_shift)

            timer_event = timer_enabled and (not tima_pending_start) and (step == to_fall)
            reload_event = tima_pending_start and (step == to_reload)
            serial_event = serial_active_start and (step == to_shift)

            old_div = div_counter
            div_counter = (div_counter + step) & 0xFFFF
            self._apu_div_ticks_pending += self._count_apu_div_falling_edges(old_div, div_counter)
            processed += step
            remaining -= step

            overflow_started = False
            if timer_event:
                overflow_started = self._tima_increment(global_cycles_before=gc_start + processed - 1)

            if tima_pending_start:
                self._tima_reload_counter -= step

            if self._tima_reload_pending and self._tima_reload_counter <= 0:
                if defer_reload_at is not None and processed == defer_reload_at:
                    self._tima_reload_counter = 0
                else:
                    self._apply_reload_if_due()

            if serial_active_start:
                self._serial_cycle_acc += step
                if serial_event and self._serial_cycle_acc >= 512:
                    self._serial_cycle_acc -= 512
                    regs[0x01] = ((regs[0x01] << 1) & 0xFF) | 0x01
                    self._serial_bits_left -= 1
                    if self._serial_bits_left <= 0:
                        self._serial_active = False
                        regs[0x02] &= 0x01
                        self.request_interrupt(SERIAL_INTERRUPT_MASK)
                        self._serial_out.append(chr(self._serial_latch_out))

        self._div_counter = div_counter
        regs[0x04] = (div_counter >> 8) & 0xFF
        self._global_cycles = gc_start + cycles

    def _apply_div_reset(self) -> None:
        tac = self.regs[0x07] & 0x07
        old_div = self._div_counter & 0xFFFF
        old_input = self._timer_input(tac, old_div)
        old_apu_bit = 1 if (old_div & (1 << self._apu_div_bit())) else 0
        self._div_counter = 0
        self.regs[0x04] = 0
        new_input = self._timer_input(tac, self._div_counter)
        if old_input == 1 and new_input == 0:
            self._tima_increment()
        if old_apu_bit:
            self._apu_div_ticks_pending += 1

    def _apply_sc_write(self) -> None:
        value = self._sc_pending_value & 0xFF
        if (value & 0x80) == 0:
            self._serial_active = False
            self._serial_cycle_acc = 0
            self._serial_bits_left = 0
            self._serial_internal_clock = (value & 0x01) != 0
            self.regs[0x02] = value & 0x01
            return
        self.regs[0x02] = value & 0x81
        self._serial_internal_clock = (value & 0x01) != 0
        self._serial_active = True
        self._serial_cycle_acc = int(self._div_counter) & 0x1FF
        self._serial_bits_left = 8
        self._serial_latch_out = self.regs[0x01] & 0xFF

    def _apply_tac_write(self) -> None:
        old_tac = self._tac_pending_old & 0x07
        new_tac = self._tac_pending_value & 0x07
        div_counter = self._div_counter & 0xFFFF
        old_input = self._timer_input(old_tac, div_counter)
        new_input = self._timer_input(new_tac, div_counter)
        if old_input == 1 and new_input == 0:
            self._tima_increment()
        self.regs[0x07] = new_tac

    def _apply_tima_write(self) -> None:
        value = self._tima_pending_value & 0xFF
        if self._tima_reload_pending:
            if self._global_cycles <= self._tima_overflow_cancel_until:
                self._tima_reload_pending = False
                self._tima_reload_counter = 0
                self.regs[0x05] = value
            return
        self.regs[0x05] = value

    def _apply_tma_write(self) -> None:
        self.regs[0x06] = self._tma_pending_value & 0xFF

    def _next_pending_event(self) -> tuple[int, str] | None:
        events: list[tuple[int, str]] = []
        if self._sc_pending_offset is not None:
            events.append((max(0, int(self._sc_pending_offset)), "sc"))
        if self._div_reset_pending_offset is not None:
            events.append((max(0, int(self._div_reset_pending_offset)), "div"))
        if self._tac_pending_offset is not None:
            events.append((max(0, int(self._tac_pending_offset)), "tac"))
        if self._tima_pending_offset is not None:
            events.append((max(0, int(self._tima_pending_offset)), "tima"))
        if self._tma_pending_offset is not None:
            events.append((max(0, int(self._tma_pending_offset)), "tma"))
        if not events:
            return None
        return min(events, key=lambda item: item[0])

    def _shift_pending_offsets(self, delta: int) -> None:
        delta = int(delta)
        if delta <= 0:
            return
        if self._sc_pending_offset is not None:
            self._sc_pending_offset = int(self._sc_pending_offset) - delta
        if self._div_reset_pending_offset is not None:
            self._div_reset_pending_offset = int(self._div_reset_pending_offset) - delta
        if self._tac_pending_offset is not None:
            self._tac_pending_offset = int(self._tac_pending_offset) - delta
        if self._tima_pending_offset is not None:
            self._tima_pending_offset = int(self._tima_pending_offset) - delta
        if self._tma_pending_offset is not None:
            self._tma_pending_offset = int(self._tma_pending_offset) - delta

    def tick(self, cycles: int) -> None:
        cycles = int(cycles)
        if cycles <= 0:
            return
        if self._stop_mode:
            return

        remaining = cycles
        while remaining > 0:
            next_event = self._next_pending_event()
            if next_event is None:
                self._tick_basic(remaining)
                break

            event_offset, event_kind = next_event
            if event_offset > remaining:
                self._tick_basic(remaining)
                self._shift_pending_offsets(remaining)
                break

            if event_offset > 0:
                self._tick_basic(event_offset, defer_reload_at=event_offset)
                remaining -= event_offset
                self._shift_pending_offsets(event_offset)
            if event_kind == "sc":
                self._apply_sc_write()
                self._sc_pending_offset = None
            elif event_kind == "div":
                self._apply_div_reset()
                self._div_reset_pending_offset = None
            elif event_kind == "tac":
                self._apply_tac_write()
                self._tac_pending_offset = None
            elif event_kind == "tima":
                self._apply_tima_write()
                self._tima_pending_offset = None
            else:
                self._apply_tma_write()
                self._tma_pending_offset = None
            self._apply_reload_if_due()

    def _div_counter_at_offset(self, offset: int) -> int:
        offset = int(offset)
        if offset <= 0:
            return self._div_counter & 0xFFFF
        pending = self._div_reset_pending_offset
        if pending is not None and offset >= int(pending):
            pending = max(0, int(pending))
            if offset >= pending:
                return (offset - pending) & 0xFFFF
        return (self._div_counter + offset) & 0xFFFF

    def _peek_tima_at_offset(self, offset: int) -> int:
        offset = int(offset)
        if offset <= 0:
            return self.regs[0x05] & 0xFF

        div_counter = self._div_counter & 0xFFFF
        tima = self.regs[0x05] & 0xFF
        tma = self.regs[0x06] & 0xFF
        tac = self.regs[0x07] & 0x07
        reload_pending = bool(self._tima_reload_pending)
        reload_counter = int(self._tima_reload_counter)

        timer_enabled = (tac & 0x04) != 0
        bit = self._timer_bit(tac) if timer_enabled else 0

        for _ in range(offset):
            old_input = 0
            if timer_enabled and not reload_pending:
                old_input = 1 if (div_counter & (1 << bit)) else 0

            div_counter = (div_counter + 1) & 0xFFFF

            if reload_pending:
                reload_counter -= 1
                if reload_counter <= 0:
                    reload_pending = False
                    reload_counter = 0
                    tima = tma
                continue

            new_input = 0
            if timer_enabled:
                new_input = 1 if (div_counter & (1 << bit)) else 0

            if old_input == 1 and new_input == 0:
                if tima == 0xFF:
                    tima = 0x00
                    reload_pending = True
                    reload_counter = 4
                else:
                    tima = (tima + 1) & 0xFF

        return tima & 0xFF

    def _timer_irq_within(self, offset: int) -> bool:
        offset = int(offset)
        if offset <= 0:
            return False

        div_counter = self._div_counter & 0xFFFF
        tima = self.regs[0x05] & 0xFF
        tma = self.regs[0x06] & 0xFF
        tac = self.regs[0x07] & 0x07
        reload_pending = bool(self._tima_reload_pending)
        reload_counter = int(self._tima_reload_counter)

        timer_enabled = (tac & 0x04) != 0
        bit = self._timer_bit(tac) if timer_enabled else 0

        for _ in range(offset):
            old_input = 0
            if timer_enabled and not reload_pending:
                old_input = 1 if (div_counter & (1 << bit)) else 0

            div_counter = (div_counter + 1) & 0xFFFF

            if reload_pending:
                reload_counter -= 1
                if reload_counter <= 0:
                    return True
                continue

            new_input = 0
            if timer_enabled:
                new_input = 1 if (div_counter & (1 << bit)) else 0

            if old_input == 1 and new_input == 0:
                if tima == 0xFF:
                    tima = 0x00
                    reload_pending = True
                    reload_counter = 4
                else:
                    tima = (tima + 1) & 0xFF

        return False

    def request_interrupt(self, mask: int) -> None:
        self.interrupt_flag = (self.interrupt_flag | (mask & 0x1F) | 0xE0) & 0xFF

    def set_button(self, name: str, pressed: bool) -> None:
        name = name.lower()
        pressed = bool(pressed)

        sel = self.regs[0x00] & 0x30
        old_low = self._joyp_low(sel)

        if name in ("right", "left", "up", "down"):
            bit = {"right": 0, "left": 1, "up": 2, "down": 3}[name]
            if pressed:
                self._dpad_state &= ~(1 << bit)
            else:
                self._dpad_state |= 1 << bit
        elif name in ("a", "b", "select", "start"):
            bit = {"a": 0, "b": 1, "select": 2, "start": 3}[name]
            if pressed:
                self._btn_state &= ~(1 << bit)
            else:
                self._btn_state |= 1 << bit
        else:
            return

        new_low = self._joyp_low(sel)
        self._maybe_joypad_irq(old_low, new_low)

    def consume_serial_output(self) -> str:
        out = "".join(self._serial_out)
        self._serial_out.clear()
        return out

    def consume_apu_div_ticks(self) -> int:
        ticks = self._apu_div_ticks_pending
        self._apu_div_ticks_pending = 0
        return ticks

    def read(self, address: int, offset: int = 0) -> int:
        address &= 0xFFFF

        if address == 0xFF0F:
            irq = self.interrupt_flag & 0x1F
            if offset and self._timer_irq_within(offset):
                irq |= TIMER_INTERRUPT_MASK
            return (irq | 0xE0) & 0xFF
        if address == 0xFFFF:
            return self.interrupt_enable & 0xFF

        if 0xFF00 <= address <= 0xFF7F:
            off = address - 0xFF00
            if off == 0x4D and self.cgb_mode:
                speed = 0x80 if self.double_speed else 0x00
                prepare = 0x01 if self.key1_prepare else 0x00
                return 0x7E | speed | prepare
            if off in _DMG_UNUSED_OFFSETS:
                return 0xFF

            if off == 0x00:
                sel = self.regs[0x00] & 0x30
                return 0xC0 | sel | self._joyp_low(sel)

            if off == 0x02:
                pending = self._sc_pending_offset
                if pending is not None:
                    pending = max(0, int(pending))
                if pending is not None and int(offset) >= pending:
                    sc_val = self._sc_pending_value & 0x81
                else:
                    sc_val = self.regs[0x02] & 0x81
                return 0x7E | sc_val
            if off == 0x04:
                div_counter = self._div_counter_at_offset(offset)
                return (div_counter >> 8) & 0xFF
            if off == 0x07:
                pending = self._tac_pending_offset
                if pending is not None:
                    pending = max(0, int(pending))
                if pending is not None and int(offset) >= pending:
                    tac_val = self._tac_pending_value & 0x07
                else:
                    tac_val = self.regs[0x07] & 0x07
                return 0xF8 | tac_val
            if off == 0x05:
                pending = self._tima_pending_offset
                if pending is not None:
                    pending = max(0, int(pending))
                if pending is not None and int(offset) >= pending:
                    return self._tima_pending_value & 0xFF
                return self._peek_tima_at_offset(offset)
            if off == 0x06:
                pending = self._tma_pending_offset
                if pending is not None:
                    pending = max(0, int(pending))
                if pending is not None and int(offset) >= pending:
                    return self._tma_pending_value & 0xFF
                return self.regs[0x06] & 0xFF

            if off == 0x10:
                return 0x80 | (self.regs[0x10] & 0x7F)
            if off == 0x11:
                return 0x3F | (self.regs[0x11] & 0xC0)
            if off == 0x13:
                return 0xFF
            if off == 0x14:
                return 0xBF | (self.regs[0x14] & 0x40)
            if off == 0x16:
                return 0x3F | (self.regs[0x16] & 0xC0)
            if off == 0x18:
                return 0xFF
            if off == 0x19:
                return 0xBF | (self.regs[0x19] & 0x40)
            if off == 0x1A:
                return 0x7F | (self.regs[0x1A] & 0x80)
            if off == 0x1B:
                return 0xFF
            if off == 0x1C:
                return 0x9F | (self.regs[0x1C] & 0x60)
            if off == 0x1D:
                return 0xFF
            if off == 0x1E:
                return 0xBF | (self.regs[0x1E] & 0x40)
            if off == 0x20:
                return 0xFF
            if off == 0x23:
                return 0xBF | (self.regs[0x23] & 0x40)
            if off == 0x26:
                return 0x70 | (self.regs[0x26] & 0x8F)

            if off == 0x41:
                return 0x80 | (self.regs[0x41] & 0x7F)
            if off == 0x44:
                return self.regs[0x44] & 0xFF
            if off == 0x50:
                return 0xFF

            return self.regs[off] & 0xFF

        return 0xFF

    def write(self, address: int, value: int, offset: int = 0) -> None:
        address &= 0xFFFF
        value &= 0xFF

        if address == 0xFF0F:
            self.interrupt_flag = (value | 0xE0) & 0xFF
            return
        if address == 0xFFFF:
            self.interrupt_enable = value & 0xFF
            return

        if 0xFF00 <= address <= 0xFF7F:
            off = address - 0xFF00
            if off == 0x4D and self.cgb_mode:
                self.key1_prepare = (value & 0x01) != 0
                return
            if off in _DMG_UNUSED_OFFSETS:
                return

            if off == 0x00:
                old_sel = self.regs[0x00] & 0x30
                old_low = self._joyp_low(old_sel)
                self.regs[0x00] = value & 0x30
                new_sel = self.regs[0x00] & 0x30
                new_low = self._joyp_low(new_sel)
                self._maybe_joypad_irq(old_low, new_low)
                return

            if off == 0x02:
                self._sc_pending_value = value & 0xFF
                self._sc_pending_offset = int(offset)
                return

            if off == 0x04:
                self._div_reset_pending_offset = int(offset)
                self.regs[0x04] = 0
                return

            if off == 0x05:
                self._tima_pending_value = value & 0xFF
                self._tima_pending_offset = int(offset)
                return
            if off == 0x06:
                self._tma_pending_value = value & 0xFF
                self._tma_pending_offset = int(offset)
                return

            if off == 0x07:
                self._tac_pending_old = self.regs[0x07] & 0x07
                self._tac_pending_value = value & 0x07
                self._tac_pending_offset = int(offset)
                return

            if off == 0x10:
                self.regs[0x10] = value & 0x7F
                return
            if off == 0x26:
                self.regs[0x26] = (self.regs[0x26] & 0x0F) | (value & 0x80)
                return

            if off == 0x41:
                self.regs[0x41] = (self.regs[0x41] & 0x07) | (value & 0x78) | 0x80
                return
            if off == 0x44:
                return
            if off == 0x50:
                self.regs[0x50] = value
                return

            self.regs[off] = value
            return
