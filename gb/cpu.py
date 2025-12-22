from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Tuple

from gb.bus import BUS


Z_FLAG = 0x80
N_FLAG = 0x40
H_FLAG = 0x20
C_FLAG = 0x10


@dataclass
class Registers:
    a: int = 0
    b: int = 0
    c: int = 0
    d: int = 0
    e: int = 0
    f: int = 0
    h: int = 0
    l: int = 0

    def get_af(self) -> int:
        return ((self.a & 0xFF) << 8) | (self.f & 0xF0)

    def set_af(self, value: int) -> None:
        value &= 0xFFFF
        self.a = (value >> 8) & 0xFF
        self.f = value & 0xF0

    def get_bc(self) -> int:
        return ((self.b & 0xFF) << 8) | (self.c & 0xFF)

    def set_bc(self, value: int) -> None:
        value &= 0xFFFF
        self.b = (value >> 8) & 0xFF
        self.c = value & 0xFF

    def get_de(self) -> int:
        return ((self.d & 0xFF) << 8) | (self.e & 0xFF)

    def set_de(self, value: int) -> None:
        value &= 0xFFFF
        self.d = (value >> 8) & 0xFF
        self.e = value & 0xFF

    def get_hl(self) -> int:
        return ((self.h & 0xFF) << 8) | (self.l & 0xFF)

    def set_hl(self, value: int) -> None:
        value &= 0xFFFF
        self.h = (value >> 8) & 0xFF
        self.l = value & 0xFF


@dataclass
class CPU:
    regs: Registers = field(default_factory=Registers)
    pc: int = 0
    sp: int = 0
    bus: BUS = field(default_factory=BUS)
    halted: bool = False
    stopped: bool = False
    ime: bool = False
    cycles: int = 0
    _ei_pending: bool = False
    _halt_bug: bool = False
    _op_table: list[Callable[[int, int], int]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._build_dispatch_table()

    def _build_dispatch_table(self) -> None:
        t: list[Callable[[int, int], int]] = [self._op_unimplemented] * 0x100

        for op in range(0x40, 0x80):
            t[op] = self._op_ld_r_r

        for op in range(0x80, 0xC0):
            t[op] = self._op_alu_r

        for op in range(0x100):
            if (op & 0xC7) == 0x06:
                t[op] = self._op_ld_r_d8
            elif (op & 0xC7) == 0x04:
                t[op] = self._op_inc_r
            elif (op & 0xC7) == 0x05:
                t[op] = self._op_dec_r

        t[0x00] = self._op_nop
        t[0x10] = self._op_stop
        t[0x76] = self._op_halt

        for op in (0x07, 0x0F, 0x17, 0x1F):
            t[op] = self._op_rot_a
        t[0x27] = self._op_daa
        t[0x2F] = self._op_cpl
        t[0x37] = self._op_scf
        t[0x3F] = self._op_ccf

        t[0x08] = self._op_ld_a16_sp

        for op in (0x01, 0x11, 0x21, 0x31):
            t[op] = self._op_ld_dd_d16
        for op in (0x03, 0x13, 0x23, 0x33):
            t[op] = self._op_inc_dd
        for op in (0x0B, 0x1B, 0x2B, 0x3B):
            t[op] = self._op_dec_dd
        for op in (0x09, 0x19, 0x29, 0x39):
            t[op] = self._op_add_hl_dd

        for op in (0x02, 0x12):
            t[op] = self._op_ld_mem_rr_a
        for op in (0x0A, 0x1A):
            t[op] = self._op_ld_a_mem_rr
        for op in (0x22, 0x32):
            t[op] = self._op_ld_hli_a
        for op in (0x2A, 0x3A):
            t[op] = self._op_ld_a_hli

        t[0x18] = self._op_jr
        for op in (0x20, 0x28, 0x30, 0x38):
            t[op] = self._op_jr_cc

        for op in (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE):
            t[op] = self._op_alu_d8

        for op in (0xE0, 0xF0):
            t[op] = self._op_ldh_a8
        for op in (0xE2, 0xF2):
            t[op] = self._op_ldh_c
        for op in (0xEA, 0xFA):
            t[op] = self._op_ld_a16_a

        t[0xE8] = self._op_add_sp_r8
        t[0xF8] = self._op_ld_hl_sp_r8
        t[0xF9] = self._op_ld_sp_hl
        t[0xE9] = self._op_jp_hl

        t[0xF3] = self._op_di
        t[0xFB] = self._op_ei

        t[0xD9] = self._op_reti
        t[0xC9] = self._op_ret
        for op in (0xC0, 0xC8, 0xD0, 0xD8):
            t[op] = self._op_ret_cc

        t[0xC3] = self._op_jp_a16
        for op in (0xC2, 0xCA, 0xD2, 0xDA):
            t[op] = self._op_jp_cc_a16

        t[0xCD] = self._op_call_a16
        for op in (0xC4, 0xCC, 0xD4, 0xDC):
            t[op] = self._op_call_cc_a16

        for op in (0xC1, 0xD1, 0xE1, 0xF1):
            t[op] = self._op_pop_qq
        for op in (0xC5, 0xD5, 0xE5, 0xF5):
            t[op] = self._op_push_qq
        for op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
            t[op] = self._op_rst

        self._op_table = t

    def push_u16(self, value: int) -> None:
        value &= 0xFFFF
        msb = (value >> 8) & 0xFF
        lsb = value & 0xFF
        self.sp = (self.sp - 1) & 0xFFFF
        self.bus.write_byte(self.sp, msb)
        self.sp = (self.sp - 1) & 0xFFFF
        self.bus.write_byte(self.sp, lsb)

    def pop_u16(self) -> int:
        lsb = self.bus.read_byte(self.sp) & 0xFF
        self.sp = (self.sp + 1) & 0xFFFF
        msb = self.bus.read_byte(self.sp) & 0xFF
        self.sp = (self.sp + 1) & 0xFFFF
        return ((msb << 8) | lsb) & 0xFFFF

    def _read8(self, addr: int) -> int:
        return self.bus.read_byte(addr & 0xFFFF) & 0xFF

    def _write8(self, addr: int, value: int) -> None:
        self.bus.write_byte(addr & 0xFFFF, value & 0xFF)

    def _read16(self, addr: int) -> int:
        lo = self._read8(addr)
        hi = self._read8(addr + 1)
        return ((hi << 8) | lo) & 0xFFFF

    def _write16(self, addr: int, value: int) -> None:
        value &= 0xFFFF
        self._write8(addr, value & 0xFF)
        self._write8(addr + 1, (value >> 8) & 0xFF)

    def _get_flag(self, mask: int) -> bool:
        return (self.regs.f & mask) != 0

    def _set_flags(self, z, n, h, c) -> None:
        f = self.regs.f & 0xF0
        if z is not None:
            f = (f | Z_FLAG) if z else (f & ~Z_FLAG)
        if n is not None:
            f = (f | N_FLAG) if n else (f & ~N_FLAG)
        if h is not None:
            f = (f | H_FLAG) if h else (f & ~H_FLAG)
        if c is not None:
            f = (f | C_FLAG) if c else (f & ~C_FLAG)
        self.regs.f = f & 0xF0

    def _interrupt_pending(self) -> int:
        ie = self._read8(0xFFFF)
        ir = self._read8(0xFF0F)
        return ie & ir & 0x1F

    def _service_interrupt(self) -> int:
        pending = self._interrupt_pending()
        if pending == 0 or not self.ime:
            return 0
        self.halted = False
        self.stopped = False
        self.ime = False
        for i, vector in enumerate((0x40, 0x48, 0x50, 0x58, 0x60)):
            if pending & (1 << i):
                ir = self._read8(0xFF0F)
                self._write8(0xFF0F, ir & ~(1 << i))
                self.push_u16(self.pc)
                self.pc = vector
                return 20
        return 0

    def _imm8(self, off: int) -> int:
        return self._read8((self.pc + off) & 0xFFFF)

    def _imm16(self, off: int) -> int:
        return self._read16((self.pc + off) & 0xFFFF)

    def _imm8s(self, off: int) -> int:
        b = self._imm8(off)
        return b - 0x100 if (b & 0x80) else b

    def _cond(self, cc: int) -> bool:
        cc &= 3
        z = self._get_flag(Z_FLAG)
        c = self._get_flag(C_FLAG)
        if cc == 0:
            return not z
        if cc == 1:
            return z
        if cc == 2:
            return not c
        return c

    def _reg8_get(self, idx: int) -> int:
        idx &= 7
        if idx == 0:
            return self.regs.b & 0xFF
        if idx == 1:
            return self.regs.c & 0xFF
        if idx == 2:
            return self.regs.d & 0xFF
        if idx == 3:
            return self.regs.e & 0xFF
        if idx == 4:
            return self.regs.h & 0xFF
        if idx == 5:
            return self.regs.l & 0xFF
        if idx == 6:
            return self._read8(self.regs.get_hl())
        return self.regs.a & 0xFF

    def _reg8_set(self, idx: int, value: int) -> None:
        value &= 0xFF
        idx &= 7
        if idx == 0:
            self.regs.b = value
        elif idx == 1:
            self.regs.c = value
        elif idx == 2:
            self.regs.d = value
        elif idx == 3:
            self.regs.e = value
        elif idx == 4:
            self.regs.h = value
        elif idx == 5:
            self.regs.l = value
        elif idx == 6:
            self._write8(self.regs.get_hl(), value)
        else:
            self.regs.a = value

    def _reg16_get(self, idx: int) -> int:
        idx &= 3
        if idx == 0:
            return self.regs.get_bc()
        if idx == 1:
            return self.regs.get_de()
        if idx == 2:
            return self.regs.get_hl()
        return self.sp & 0xFFFF

    def _reg16_set(self, idx: int, value: int) -> None:
        value &= 0xFFFF
        idx &= 3
        if idx == 0:
            self.regs.set_bc(value)
        elif idx == 1:
            self.regs.set_de(value)
        elif idx == 2:
            self.regs.set_hl(value)
        else:
            self.sp = value

    def _alu_add(self, v: int) -> None:
        a = self.regs.a & 0xFF
        v &= 0xFF
        r = a + v
        res = r & 0xFF
        self.regs.a = res
        self._set_flags(res == 0, False, ((a & 0x0F) + (v & 0x0F)) > 0x0F, r > 0xFF)

    def _alu_adc(self, v: int) -> None:
        a = self.regs.a & 0xFF
        v &= 0xFF
        cin = 1 if self._get_flag(C_FLAG) else 0
        r = a + v + cin
        res = r & 0xFF
        self.regs.a = res
        self._set_flags(res == 0, False, ((a & 0x0F) + (v & 0x0F) + cin) > 0x0F, r > 0xFF)

    def _alu_sub(self, v: int) -> None:
        a = self.regs.a & 0xFF
        v &= 0xFF
        r = a - v
        res = r & 0xFF
        self.regs.a = res
        self._set_flags(res == 0, True, (a & 0x0F) < (v & 0x0F), a < v)

    def _alu_sbc(self, v: int) -> None:
        a = self.regs.a & 0xFF
        v &= 0xFF
        cin = 1 if self._get_flag(C_FLAG) else 0
        r = a - v - cin
        res = r & 0xFF
        self.regs.a = res
        self._set_flags(res == 0, True, (a & 0x0F) < ((v & 0x0F) + cin), a < (v + cin))

    def _alu_and(self, v: int) -> None:
        res = (self.regs.a & 0xFF) & (v & 0xFF)
        self.regs.a = res & 0xFF
        self._set_flags(res == 0, False, True, False)

    def _alu_xor(self, v: int) -> None:
        res = (self.regs.a & 0xFF) ^ (v & 0xFF)
        self.regs.a = res & 0xFF
        self._set_flags(res == 0, False, False, False)

    def _alu_or(self, v: int) -> None:
        res = (self.regs.a & 0xFF) | (v & 0xFF)
        self.regs.a = res & 0xFF
        self._set_flags(res == 0, False, False, False)

    def _alu_cp(self, v: int) -> None:
        a = self.regs.a & 0xFF
        v &= 0xFF
        r = a - v
        res = r & 0xFF
        self._set_flags(res == 0, True, (a & 0x0F) < (v & 0x0F), a < v)

    def _inc8(self, v: int) -> int:
        v &= 0xFF
        res = (v + 1) & 0xFF
        self._set_flags(res == 0, False, ((v & 0x0F) + 1) > 0x0F, None)
        return res

    def _dec8(self, v: int) -> int:
        v &= 0xFF
        res = (v - 1) & 0xFF
        self._set_flags(res == 0, True, (v & 0x0F) == 0x00, None)
        return res

    def _add_hl(self, v: int) -> None:
        hl = self.regs.get_hl()
        v &= 0xFFFF
        r = hl + v
        res = r & 0xFFFF
        self.regs.set_hl(res)
        self._set_flags(None, False, ((hl & 0x0FFF) + (v & 0x0FFF)) > 0x0FFF, r > 0xFFFF)

    def _add_sp_r8(self, s: int) -> None:
        sp = self.sp & 0xFFFF
        s &= 0xFFFF
        r = (sp + s) & 0xFFFF
        h = ((sp & 0x0F) + (s & 0x0F)) > 0x0F
        c = ((sp & 0xFF) + (s & 0xFF)) > 0xFF
        self._set_flags(False, False, h, c)
        self.sp = r

    def _ld_hl_sp_r8(self, s: int) -> None:
        sp = self.sp & 0xFFFF
        s &= 0xFFFF
        r = (sp + s) & 0xFFFF
        h = ((sp & 0x0F) + (s & 0x0F)) > 0x0F
        c = ((sp & 0xFF) + (s & 0xFF)) > 0xFF
        self._set_flags(False, False, h, c)
        self.regs.set_hl(r)

    def _daa(self) -> None:
        a = self.regs.a & 0xFF
        n = self._get_flag(N_FLAG)
        h = self._get_flag(H_FLAG)
        c = self._get_flag(C_FLAG)
        adj = 0
        new_c = c
        if not n:
            if c or a > 0x99:
                adj |= 0x60
                new_c = True
            if h or (a & 0x0F) > 0x09:
                adj |= 0x06
            a = (a + adj) & 0xFF
        else:
            if c:
                adj |= 0x60
            if h:
                adj |= 0x06
            a = (a - adj) & 0xFF
        self.regs.a = a
        self._set_flags(a == 0, None, False, new_c)

    def _rlc(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c = (v & 0x80) != 0
        res = ((v << 1) & 0xFF) | (1 if c else 0)
        return res & 0xFF, c

    def _rrc(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c = (v & 0x01) != 0
        res = (v >> 1) | (0x80 if c else 0)
        return res & 0xFF, c

    def _rl(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c_out = (v & 0x80) != 0
        c_in = 1 if self._get_flag(C_FLAG) else 0
        res = ((v << 1) & 0xFF) | c_in
        return res & 0xFF, c_out

    def _rr(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c_out = (v & 0x01) != 0
        c_in = 0x80 if self._get_flag(C_FLAG) else 0
        res = (v >> 1) | c_in
        return res & 0xFF, c_out

    def _sla(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c = (v & 0x80) != 0
        res = (v << 1) & 0xFF
        return res & 0xFF, c

    def _sra(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c = (v & 0x01) != 0
        res = ((v >> 1) | (v & 0x80)) & 0xFF
        return res & 0xFF, c

    def _srl(self, v: int) -> Tuple[int, bool]:
        v &= 0xFF
        c = (v & 0x01) != 0
        res = (v >> 1) & 0xFF
        return res & 0xFF, c

    def _swap(self, v: int) -> int:
        v &= 0xFF
        return (((v & 0x0F) << 4) | ((v & 0xF0) >> 4)) & 0xFF

    def step(self) -> int:
        ei_apply = self._ei_pending

        if self.stopped:
            if self._interrupt_pending() != 0:
                self.stopped = False
            else:
                self.cycles = (self.cycles + 4) & 0xFFFFFFFF
                return 4

        pending = self._interrupt_pending()
        if self.halted and pending != 0:
            self.halted = False

        int_cycles = self._service_interrupt()
        if int_cycles:
            self.cycles = (self.cycles + int_cycles) & 0xFFFFFFFF
            return int_cycles

        if self.halted:
            self.cycles = (self.cycles + 4) & 0xFFFFFFFF
            return 4

        op_off = 0 if self._halt_bug else 1
        self._halt_bug = False

        opcode = self._read8(self.pc)
        if opcode == 0xCB:
            cb = self._read8((self.pc + op_off) & 0xFFFF)
            cycles = self._exec_cb(cb, op_off)
        else:
            cycles = self._exec(opcode, op_off)

        if ei_apply and self._ei_pending:
            self.ime = True
            self._ei_pending = False

        self.cycles = (self.cycles + cycles) & 0xFFFFFFFF
        return cycles

    def _exec_cb(self, opcode: int, op_off: int) -> int:
        r = opcode & 7
        y = (opcode >> 3) & 7
        x = (opcode >> 6) & 3
        inc = 2 - (1 - op_off)

        if x == 0:
            v = self._reg8_get(r)
            if y == 0:
                res, c = self._rlc(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 1:
                res, c = self._rrc(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 2:
                res, c = self._rl(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 3:
                res, c = self._rr(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 4:
                res, c = self._sla(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 5:
                res, c = self._sra(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
            elif y == 6:
                res = self._swap(v)
                self._set_flags(res == 0, False, False, False)
                self._reg8_set(r, res)
            else:
                res, c = self._srl(v)
                self._set_flags(res == 0, False, False, c)
                self._reg8_set(r, res)
        elif x == 1:
            v = self._reg8_get(r)
            bit = y & 7
            z = ((v >> bit) & 1) == 0
            self._set_flags(z, False, True, None)
        elif x == 2:
            v = self._reg8_get(r)
            bit = y & 7
            self._reg8_set(r, v & ~(1 << bit))
        else:
            v = self._reg8_get(r)
            bit = y & 7
            self._reg8_set(r, v | (1 << bit))

        self.pc = (self.pc + inc) & 0xFFFF
        return 16 if r == 6 else 8

    def _exec(self, opcode: int, op_off: int) -> int:
        opcode &= 0xFF
        return self._op_table[opcode](opcode, op_off)

    def _op_unimplemented(self, opcode: int, op_off: int) -> int:
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_nop(self, opcode: int, op_off: int) -> int:
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_stop(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        self.pc = (self.pc + inc2) & 0xFFFF
        self.stopped = True
        return 4

    def _op_halt(self, opcode: int, op_off: int) -> int:
        pending = self._interrupt_pending()
        if not self.ime and pending != 0:
            self._halt_bug = True
            self.halted = False
        else:
            self.halted = True
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_rot_a(self, opcode: int, op_off: int) -> int:
        a = self.regs.a & 0xFF
        if opcode == 0x07:
            res, c = self._rlc(a)
        elif opcode == 0x0F:
            res, c = self._rrc(a)
        elif opcode == 0x17:
            res, c = self._rl(a)
        else:
            res, c = self._rr(a)
        self.regs.a = res
        self._set_flags(False, False, False, c)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_daa(self, opcode: int, op_off: int) -> int:
        self._daa()
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_cpl(self, opcode: int, op_off: int) -> int:
        self.regs.a = (~self.regs.a) & 0xFF
        self._set_flags(None, True, True, None)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_scf(self, opcode: int, op_off: int) -> int:
        self._set_flags(None, False, False, True)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_ccf(self, opcode: int, op_off: int) -> int:
        self._set_flags(None, False, False, not self._get_flag(C_FLAG))
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_ld_a16_sp(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        addr = self._imm16(op_off)
        self._write16(addr, self.sp)
        self.pc = (self.pc + inc3) & 0xFFFF
        return 20

    def _op_ld_dd_d16(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        dd = (opcode >> 4) & 3
        v = self._imm16(op_off)
        self._reg16_set(dd, v)
        self.pc = (self.pc + inc3) & 0xFFFF
        return 12

    def _op_inc_dd(self, opcode: int, op_off: int) -> int:
        dd = (opcode >> 4) & 3
        self._reg16_set(dd, (self._reg16_get(dd) + 1) & 0xFFFF)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_dec_dd(self, opcode: int, op_off: int) -> int:
        dd = (opcode >> 4) & 3
        self._reg16_set(dd, (self._reg16_get(dd) - 1) & 0xFFFF)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_add_hl_dd(self, opcode: int, op_off: int) -> int:
        dd = (opcode >> 4) & 3
        self._add_hl(self._reg16_get(dd))
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_mem_rr_a(self, opcode: int, op_off: int) -> int:
        addr = self.regs.get_bc() if opcode == 0x02 else self.regs.get_de()
        self._write8(addr, self.regs.a)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_a_mem_rr(self, opcode: int, op_off: int) -> int:
        addr = self.regs.get_bc() if opcode == 0x0A else self.regs.get_de()
        self.regs.a = self._read8(addr)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_hli_a(self, opcode: int, op_off: int) -> int:
        addr = self.regs.get_hl()
        self._write8(addr, self.regs.a)
        self.regs.set_hl((addr + 1) & 0xFFFF if opcode == 0x22 else (addr - 1) & 0xFFFF)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_a_hli(self, opcode: int, op_off: int) -> int:
        addr = self.regs.get_hl()
        self.regs.a = self._read8(addr)
        self.regs.set_hl((addr + 1) & 0xFFFF if opcode == 0x2A else (addr - 1) & 0xFFFF)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_r_d8(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        r = (opcode >> 3) & 7
        v = self._imm8(op_off)
        self._reg8_set(r, v)
        self.pc = (self.pc + inc2) & 0xFFFF
        return 12 if r == 6 else 8

    def _op_inc_r(self, opcode: int, op_off: int) -> int:
        r = (opcode >> 3) & 7
        self._reg8_set(r, self._inc8(self._reg8_get(r)))
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 12 if r == 6 else 4

    def _op_dec_r(self, opcode: int, op_off: int) -> int:
        r = (opcode >> 3) & 7
        self._reg8_set(r, self._dec8(self._reg8_get(r)))
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 12 if r == 6 else 4

    def _op_jr(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        off = self._imm8s(op_off)
        self.pc = (self.pc + inc2 + off) & 0xFFFF
        return 12

    def _op_jr_cc(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        cc = (opcode >> 3) & 3
        off = self._imm8s(op_off)
        if self._cond(cc):
            self.pc = (self.pc + inc2 + off) & 0xFFFF
            return 12
        self.pc = (self.pc + inc2) & 0xFFFF
        return 8

    def _op_ld_r_r(self, opcode: int, op_off: int) -> int:
        dst = (opcode >> 3) & 7
        src = opcode & 7
        self._reg8_set(dst, self._reg8_get(src))
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8 if (dst == 6 or src == 6) else 4

    def _op_alu_r(self, opcode: int, op_off: int) -> int:
        op = (opcode >> 3) & 7
        r = opcode & 7
        v = self._reg8_get(r)
        if op == 0:
            self._alu_add(v)
        elif op == 1:
            self._alu_adc(v)
        elif op == 2:
            self._alu_sub(v)
        elif op == 3:
            self._alu_sbc(v)
        elif op == 4:
            self._alu_and(v)
        elif op == 5:
            self._alu_xor(v)
        elif op == 6:
            self._alu_or(v)
        else:
            self._alu_cp(v)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8 if r == 6 else 4

    def _op_alu_d8(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        v = self._imm8(op_off)
        if opcode == 0xC6:
            self._alu_add(v)
        elif opcode == 0xCE:
            self._alu_adc(v)
        elif opcode == 0xD6:
            self._alu_sub(v)
        elif opcode == 0xDE:
            self._alu_sbc(v)
        elif opcode == 0xE6:
            self._alu_and(v)
        elif opcode == 0xEE:
            self._alu_xor(v)
        elif opcode == 0xF6:
            self._alu_or(v)
        else:
            self._alu_cp(v)
        self.pc = (self.pc + inc2) & 0xFFFF
        return 8

    def _op_ldh_a8(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        a8 = self._imm8(op_off)
        addr = 0xFF00 + a8
        if opcode == 0xE0:
            self._write8(addr, self.regs.a)
        else:
            self.regs.a = self._read8(addr)
        self.pc = (self.pc + inc2) & 0xFFFF
        return 12

    def _op_ldh_c(self, opcode: int, op_off: int) -> int:
        addr = 0xFF00 + (self.regs.c & 0xFF)
        if opcode == 0xE2:
            self._write8(addr, self.regs.a)
        else:
            self.regs.a = self._read8(addr)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_ld_a16_a(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        addr = self._imm16(op_off)
        if opcode == 0xEA:
            self._write8(addr, self.regs.a)
        else:
            self.regs.a = self._read8(addr)
        self.pc = (self.pc + inc3) & 0xFFFF
        return 16

    def _op_add_sp_r8(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        s = self._imm8s(op_off) & 0xFFFF
        self._add_sp_r8(s)
        self.pc = (self.pc + inc2) & 0xFFFF
        return 16

    def _op_ld_hl_sp_r8(self, opcode: int, op_off: int) -> int:
        inc2 = 1 + (op_off & 1)
        s = self._imm8s(op_off) & 0xFFFF
        self._ld_hl_sp_r8(s)
        self.pc = (self.pc + inc2) & 0xFFFF
        return 12

    def _op_ld_sp_hl(self, opcode: int, op_off: int) -> int:
        self.sp = self.regs.get_hl() & 0xFFFF
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_jp_hl(self, opcode: int, op_off: int) -> int:
        self.pc = self.regs.get_hl() & 0xFFFF
        return 4

    def _op_di(self, opcode: int, op_off: int) -> int:
        self.ime = False
        self._ei_pending = False
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_ei(self, opcode: int, op_off: int) -> int:
        self._ei_pending = True
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 4

    def _op_reti(self, opcode: int, op_off: int) -> int:
        self.pc = self.pop_u16()
        self.ime = True
        return 16

    def _op_ret(self, opcode: int, op_off: int) -> int:
        self.pc = self.pop_u16()
        return 16

    def _op_ret_cc(self, opcode: int, op_off: int) -> int:
        cc = (opcode >> 3) & 3
        if self._cond(cc):
            self.pc = self.pop_u16()
            return 20
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 8

    def _op_jp_a16(self, opcode: int, op_off: int) -> int:
        addr = self._imm16(op_off)
        self.pc = addr
        return 16

    def _op_jp_cc_a16(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        cc = (opcode >> 3) & 3
        addr = self._imm16(op_off)
        if self._cond(cc):
            self.pc = addr
            return 16
        self.pc = (self.pc + inc3) & 0xFFFF
        return 12

    def _op_call_a16(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        addr = self._imm16(op_off)
        self.push_u16((self.pc + inc3) & 0xFFFF)
        self.pc = addr
        return 24

    def _op_call_cc_a16(self, opcode: int, op_off: int) -> int:
        inc3 = 2 + (op_off & 1)
        cc = (opcode >> 3) & 3
        addr = self._imm16(op_off)
        if self._cond(cc):
            self.push_u16((self.pc + inc3) & 0xFFFF)
            self.pc = addr
            return 24
        self.pc = (self.pc + inc3) & 0xFFFF
        return 12

    def _op_pop_qq(self, opcode: int, op_off: int) -> int:
        qq = (opcode >> 4) & 3
        v = self.pop_u16()
        if qq == 0:
            self.regs.set_bc(v)
        elif qq == 1:
            self.regs.set_de(v)
        elif qq == 2:
            self.regs.set_hl(v)
        else:
            self.regs.set_af(v)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 12

    def _op_push_qq(self, opcode: int, op_off: int) -> int:
        qq = (opcode >> 4) & 3
        if qq == 0:
            v = self.regs.get_bc()
        elif qq == 1:
            v = self.regs.get_de()
        elif qq == 2:
            v = self.regs.get_hl()
        else:
            v = self.regs.get_af()
        self.push_u16(v)
        self.pc = (self.pc + (op_off & 1)) & 0xFFFF
        return 16

    def _op_rst(self, opcode: int, op_off: int) -> int:
        vec = opcode & 0x38
        self.push_u16((self.pc + (op_off & 1)) & 0xFFFF)
        self.pc = vec
        return 16

