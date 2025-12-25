from __future__ import annotations

from dataclasses import dataclass, field


SAMPLE_RATE = 44100
CPU_CLOCK = 4194304
CYCLES_PER_SAMPLE = CPU_CLOCK / SAMPLE_RATE

DUTY_WAVEFORMS = (
    (0, 0, 0, 0, 0, 0, 0, 1),
    (1, 0, 0, 0, 0, 0, 0, 1),
    (1, 0, 0, 0, 0, 1, 1, 1),
    (0, 1, 1, 1, 1, 1, 1, 0),
)


@dataclass(slots=True)
class SquareChannel:
    enabled: bool = False
    dac_enabled: bool = False
    length_counter: int = 0
    length_enabled: bool = False
    volume: int = 0
    volume_init: int = 0
    envelope_add: bool = False
    envelope_period: int = 0
    envelope_timer: int = 0
    frequency: int = 0
    timer: int = 0
    duty: int = 0
    duty_pos: int = 0
    sweep_enabled: bool = False
    sweep_period: int = 0
    sweep_negate: bool = False
    sweep_shift: int = 0
    sweep_timer: int = 0
    sweep_shadow: int = 0
    sweep_negate_used: bool = False

    def tick_timer(self) -> None:
        self.timer -= 1
        if self.timer <= 0:
            self.timer = (2048 - self.frequency) * 4
            self.duty_pos = (self.duty_pos + 1) & 7

    def output(self) -> int:
        if not self.enabled or not self.dac_enabled:
            return 0
        return self.volume if DUTY_WAVEFORMS[self.duty][self.duty_pos] else 0

    def tick_length(self) -> None:
        if self.length_enabled and self.length_counter > 0:
            self.length_counter -= 1
            if self.length_counter == 0:
                self.enabled = False

    def tick_envelope(self) -> None:
        if self.envelope_period == 0:
            return
        self.envelope_timer -= 1
        if self.envelope_timer <= 0:
            self.envelope_timer = self.envelope_period if self.envelope_period else 8
            if self.envelope_add and self.volume < 15:
                self.volume += 1
            elif not self.envelope_add and self.volume > 0:
                self.volume -= 1

    def tick_sweep(self) -> None:
        if not self.sweep_enabled:
            return
        self.sweep_timer -= 1
        if self.sweep_timer <= 0:
            self.sweep_timer = self.sweep_period if self.sweep_period else 8
            if self.sweep_period > 0:
                new_freq = self._calculate_sweep()
                if new_freq <= 2047 and self.sweep_shift > 0:
                    self.frequency = new_freq
                    self.sweep_shadow = new_freq
                    self._calculate_sweep()

    def _calculate_sweep(self) -> int:
        delta = self.sweep_shadow >> self.sweep_shift
        if self.sweep_negate:
            self.sweep_negate_used = True
            new_freq = self.sweep_shadow - delta
        else:
            new_freq = self.sweep_shadow + delta
        if new_freq > 2047:
            self.enabled = False
        return new_freq

    def trigger(self, with_sweep: bool = False) -> None:
        self.enabled = True
        if self.length_counter == 0:
            self.length_counter = 64
        self.timer = (2048 - self.frequency) * 4
        self.envelope_timer = self.envelope_period if self.envelope_period else 8
        self.volume = self.volume_init

        if with_sweep:
            self.sweep_shadow = self.frequency
            self.sweep_timer = self.sweep_period if self.sweep_period else 8
            self.sweep_enabled = self.sweep_period > 0 or self.sweep_shift > 0
            self.sweep_negate_used = False
            if self.sweep_shift > 0:
                self._calculate_sweep()

        if not self.dac_enabled:
            self.enabled = False


@dataclass(slots=True)
class WaveChannel:
    enabled: bool = False
    dac_enabled: bool = False
    length_counter: int = 0
    length_enabled: bool = False
    volume_code: int = 0
    frequency: int = 0
    timer: int = 0
    sample_pos: int = 0
    wave_ram: bytearray = field(default_factory=lambda: bytearray(16))

    def tick_timer(self) -> None:
        self.timer -= 1
        if self.timer <= 0:
            self.timer = (2048 - self.frequency) * 2
            self.sample_pos = (self.sample_pos + 1) & 31

    def output(self) -> int:
        if not self.enabled or not self.dac_enabled:
            return 0
        byte_idx = self.sample_pos >> 1
        sample = self.wave_ram[byte_idx]
        if (self.sample_pos & 1) == 0:
            sample = (sample >> 4) & 0x0F
        else:
            sample = sample & 0x0F
        shifts = (4, 0, 1, 2)
        return sample >> shifts[self.volume_code]

    def tick_length(self) -> None:
        if self.length_enabled and self.length_counter > 0:
            self.length_counter -= 1
            if self.length_counter == 0:
                self.enabled = False

    def trigger(self) -> None:
        self.enabled = True
        if self.length_counter == 0:
            self.length_counter = 256
        self.timer = (2048 - self.frequency) * 2
        self.sample_pos = 0
        if not self.dac_enabled:
            self.enabled = False


@dataclass(slots=True)
class NoiseChannel:
    enabled: bool = False
    dac_enabled: bool = False
    length_counter: int = 0
    length_enabled: bool = False
    volume: int = 0
    volume_init: int = 0
    envelope_add: bool = False
    envelope_period: int = 0
    envelope_timer: int = 0
    clock_shift: int = 0
    width_mode: int = 0
    divisor_code: int = 0
    timer: int = 0
    lfsr: int = 0x7FFF

    def tick_timer(self) -> None:
        self.timer -= 1
        if self.timer <= 0:
            divisor = (1, 2, 4, 6, 8, 10, 12, 14)[self.divisor_code]
            self.timer = divisor << (self.clock_shift + 2)
            xor_bit = (self.lfsr & 1) ^ ((self.lfsr >> 1) & 1)
            self.lfsr = (self.lfsr >> 1) | (xor_bit << 14)
            if self.width_mode == 1:
                self.lfsr = (self.lfsr & ~0x40) | (xor_bit << 6)

    def output(self) -> int:
        if not self.enabled or not self.dac_enabled:
            return 0
        return self.volume if (self.lfsr & 1) == 0 else 0

    def tick_length(self) -> None:
        if self.length_enabled and self.length_counter > 0:
            self.length_counter -= 1
            if self.length_counter == 0:
                self.enabled = False

    def tick_envelope(self) -> None:
        if self.envelope_period == 0:
            return
        self.envelope_timer -= 1
        if self.envelope_timer <= 0:
            self.envelope_timer = self.envelope_period if self.envelope_period else 8
            if self.envelope_add and self.volume < 15:
                self.volume += 1
            elif not self.envelope_add and self.volume > 0:
                self.volume -= 1

    def trigger(self) -> None:
        self.enabled = True
        if self.length_counter == 0:
            self.length_counter = 64
        divisor = (1, 2, 4, 6, 8, 10, 12, 14)[self.divisor_code]
        self.timer = divisor << (self.clock_shift + 2)
        self.envelope_timer = self.envelope_period if self.envelope_period else 8
        self.volume = self.volume_init
        self.lfsr = 0x7FFF
        if not self.dac_enabled:
            self.enabled = False


@dataclass(slots=True)
class APU:
    ch1: SquareChannel = field(default_factory=SquareChannel)
    ch2: SquareChannel = field(default_factory=SquareChannel)
    ch3: WaveChannel = field(default_factory=WaveChannel)
    ch4: NoiseChannel = field(default_factory=NoiseChannel)
    enabled: bool = True
    left_volume: int = 7
    right_volume: int = 7
    vin_left: bool = False
    vin_right: bool = False
    panning: int = 0xFF
    frame_sequencer: int = 0
    frame_sequencer_cycles: int = 0
    sample_cycles: float = 0.0
    audio_buffer: list[float] = field(default_factory=list)
    buffer_size: int = 2048

    def tick(self, cycles: int) -> None:
        if not self.enabled:
            return

        for _ in range(cycles):
            self.ch1.tick_timer()
            self.ch2.tick_timer()
            self.ch3.tick_timer()
            self.ch4.tick_timer()

            self.frame_sequencer_cycles += 1
            if self.frame_sequencer_cycles >= 8192:
                self.frame_sequencer_cycles = 0
                self._tick_frame_sequencer()

            self.sample_cycles += 1.0
            if self.sample_cycles >= CYCLES_PER_SAMPLE:
                self.sample_cycles -= CYCLES_PER_SAMPLE
                self._generate_sample()

    def _tick_frame_sequencer(self) -> None:
        step = self.frame_sequencer
        self.frame_sequencer = (self.frame_sequencer + 1) & 7

        if (step & 1) == 0:
            self.ch1.tick_length()
            self.ch2.tick_length()
            self.ch3.tick_length()
            self.ch4.tick_length()

        if step == 2 or step == 6:
            self.ch1.tick_sweep()

        if step == 7:
            self.ch1.tick_envelope()
            self.ch2.tick_envelope()
            self.ch4.tick_envelope()

    def _generate_sample(self) -> None:
        ch1_out = self.ch1.output()
        ch2_out = self.ch2.output()
        ch3_out = self.ch3.output()
        ch4_out = self.ch4.output()

        left = 0.0
        right = 0.0

        if self.panning & 0x10:
            left += ch1_out
        if self.panning & 0x01:
            right += ch1_out

        if self.panning & 0x20:
            left += ch2_out
        if self.panning & 0x02:
            right += ch2_out

        if self.panning & 0x40:
            left += ch3_out
        if self.panning & 0x04:
            right += ch3_out

        if self.panning & 0x80:
            left += ch4_out
        if self.panning & 0x08:
            right += ch4_out

        left *= (self.left_volume + 1) / 8.0
        right *= (self.right_volume + 1) / 8.0

        left = (left / 60.0) * 2.0 - 0.1
        right = (right / 60.0) * 2.0 - 0.1

        left = max(-1.0, min(1.0, left))
        right = max(-1.0, min(1.0, right))

        self.audio_buffer.append(left)
        self.audio_buffer.append(right)

    def get_samples(self) -> list[float]:
        samples = self.audio_buffer
        self.audio_buffer = []
        return samples

    def read_register(self, address: int) -> int:
        addr = address & 0xFF

        if addr == 0x10:
            return (
                0x80
                | (self.ch1.sweep_period << 4)
                | (0x08 if self.ch1.sweep_negate else 0)
                | self.ch1.sweep_shift
            )
        if addr == 0x11:
            return 0x3F | (self.ch1.duty << 6)
        if addr == 0x12:
            return (
                (self.ch1.volume_init << 4)
                | (0x08 if self.ch1.envelope_add else 0)
                | self.ch1.envelope_period
            )
        if addr == 0x13:
            return 0xFF
        if addr == 0x14:
            return 0xBF | (0x40 if self.ch1.length_enabled else 0)

        if addr == 0x16:
            return 0x3F | (self.ch2.duty << 6)
        if addr == 0x17:
            return (
                (self.ch2.volume_init << 4)
                | (0x08 if self.ch2.envelope_add else 0)
                | self.ch2.envelope_period
            )
        if addr == 0x18:
            return 0xFF
        if addr == 0x19:
            return 0xBF | (0x40 if self.ch2.length_enabled else 0)

        if addr == 0x1A:
            return 0x7F | (0x80 if self.ch3.dac_enabled else 0)
        if addr == 0x1B:
            return 0xFF
        if addr == 0x1C:
            return 0x9F | (self.ch3.volume_code << 5)
        if addr == 0x1D:
            return 0xFF
        if addr == 0x1E:
            return 0xBF | (0x40 if self.ch3.length_enabled else 0)

        if addr == 0x20:
            return 0xFF
        if addr == 0x21:
            return (
                (self.ch4.volume_init << 4)
                | (0x08 if self.ch4.envelope_add else 0)
                | self.ch4.envelope_period
            )
        if addr == 0x22:
            return (
                (self.ch4.clock_shift << 4)
                | (self.ch4.width_mode << 3)
                | self.ch4.divisor_code
            )
        if addr == 0x23:
            return 0xBF | (0x40 if self.ch4.length_enabled else 0)

        if addr == 0x24:
            return (
                (0x80 if self.vin_left else 0)
                | (self.left_volume << 4)
                | (0x08 if self.vin_right else 0)
                | self.right_volume
            )
        if addr == 0x25:
            return self.panning
        if addr == 0x26:
            return (
                0x70
                | (0x80 if self.enabled else 0)
                | (0x01 if self.ch1.enabled else 0)
                | (0x02 if self.ch2.enabled else 0)
                | (0x04 if self.ch3.enabled else 0)
                | (0x08 if self.ch4.enabled else 0)
            )

        if 0x30 <= addr <= 0x3F:
            return self.ch3.wave_ram[addr - 0x30]

        return 0xFF

    def write_register(self, address: int, value: int) -> None:
        addr = address & 0xFF
        value &= 0xFF

        if addr == 0x26:
            was_enabled = self.enabled
            self.enabled = (value & 0x80) != 0
            if was_enabled and not self.enabled:
                self._power_off()
            return

        if not self.enabled and not (0x30 <= addr <= 0x3F):
            return

        if addr == 0x10:
            old_negate = self.ch1.sweep_negate
            self.ch1.sweep_period = (value >> 4) & 7
            self.ch1.sweep_negate = (value & 0x08) != 0
            self.ch1.sweep_shift = value & 7
            if old_negate and not self.ch1.sweep_negate and self.ch1.sweep_negate_used:
                self.ch1.enabled = False
        elif addr == 0x11:
            self.ch1.duty = (value >> 6) & 3
            self.ch1.length_counter = 64 - (value & 0x3F)
        elif addr == 0x12:
            self.ch1.volume_init = (value >> 4) & 0x0F
            self.ch1.envelope_add = (value & 0x08) != 0
            self.ch1.envelope_period = value & 7
            self.ch1.dac_enabled = (value & 0xF8) != 0
            if not self.ch1.dac_enabled:
                self.ch1.enabled = False
        elif addr == 0x13:
            self.ch1.frequency = (self.ch1.frequency & 0x700) | value
        elif addr == 0x14:
            self.ch1.frequency = (self.ch1.frequency & 0xFF) | ((value & 7) << 8)
            self.ch1.length_enabled = (value & 0x40) != 0
            if value & 0x80:
                self.ch1.trigger(with_sweep=True)

        elif addr == 0x16:
            self.ch2.duty = (value >> 6) & 3
            self.ch2.length_counter = 64 - (value & 0x3F)
        elif addr == 0x17:
            self.ch2.volume_init = (value >> 4) & 0x0F
            self.ch2.envelope_add = (value & 0x08) != 0
            self.ch2.envelope_period = value & 7
            self.ch2.dac_enabled = (value & 0xF8) != 0
            if not self.ch2.dac_enabled:
                self.ch2.enabled = False
        elif addr == 0x18:
            self.ch2.frequency = (self.ch2.frequency & 0x700) | value
        elif addr == 0x19:
            self.ch2.frequency = (self.ch2.frequency & 0xFF) | ((value & 7) << 8)
            self.ch2.length_enabled = (value & 0x40) != 0
            if value & 0x80:
                self.ch2.trigger(with_sweep=False)

        elif addr == 0x1A:
            self.ch3.dac_enabled = (value & 0x80) != 0
            if not self.ch3.dac_enabled:
                self.ch3.enabled = False
        elif addr == 0x1B:
            self.ch3.length_counter = 256 - value
        elif addr == 0x1C:
            self.ch3.volume_code = (value >> 5) & 3
        elif addr == 0x1D:
            self.ch3.frequency = (self.ch3.frequency & 0x700) | value
        elif addr == 0x1E:
            self.ch3.frequency = (self.ch3.frequency & 0xFF) | ((value & 7) << 8)
            self.ch3.length_enabled = (value & 0x40) != 0
            if value & 0x80:
                self.ch3.trigger()

        elif addr == 0x20:
            self.ch4.length_counter = 64 - (value & 0x3F)
        elif addr == 0x21:
            self.ch4.volume_init = (value >> 4) & 0x0F
            self.ch4.envelope_add = (value & 0x08) != 0
            self.ch4.envelope_period = value & 7
            self.ch4.dac_enabled = (value & 0xF8) != 0
            if not self.ch4.dac_enabled:
                self.ch4.enabled = False
        elif addr == 0x22:
            self.ch4.clock_shift = (value >> 4) & 0x0F
            self.ch4.width_mode = (value >> 3) & 1
            self.ch4.divisor_code = value & 7
        elif addr == 0x23:
            self.ch4.length_enabled = (value & 0x40) != 0
            if value & 0x80:
                self.ch4.trigger()

        elif addr == 0x24:
            self.vin_left = (value & 0x80) != 0
            self.left_volume = (value >> 4) & 7
            self.vin_right = (value & 0x08) != 0
            self.right_volume = value & 7
        elif addr == 0x25:
            self.panning = value

        elif 0x30 <= addr <= 0x3F:
            self.ch3.wave_ram[addr - 0x30] = value

    def _power_off(self) -> None:
        self.ch1 = SquareChannel()
        self.ch2 = SquareChannel()
        wave_ram = self.ch3.wave_ram
        self.ch3 = WaveChannel()
        self.ch3.wave_ram = wave_ram
        self.ch4 = NoiseChannel()

        self.left_volume = 0
        self.right_volume = 0
        self.vin_left = False
        self.vin_right = False
        self.panning = 0
        self.frame_sequencer = 0
