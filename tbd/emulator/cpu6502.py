"""An exact (instruction-semantics-accurate) MOS 6502 CPU emulator.

This emulator implements the full official (documented) 6502 instruction
set operating on a flat 64KiB memory space. It is designed to be used as
an oracle in program-synthesis search: given a program (a sequence of
machine-code bytes) and an initial machine state, it deterministically and
exactly computes the resulting machine state, exactly as real 6502
hardware would (decimal-mode ADC/SBC is intentionally unsupported and will
raise, since it is rarely needed for small synthesized programs and its
NV flag behaviour is famously under-specified on the NMOS 6502).

Unofficial/illegal opcodes are not implemented; executing one raises
:class:`IllegalOpcodeError` rather than silently producing an approximate
result, so that callers (e.g. the synthesizer) can never mistake an
unsupported opcode for a correctly emulated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

MEM_SIZE = 0x10000

# Processor status flag bit positions.
FLAG_C = 0x01
FLAG_Z = 0x02
FLAG_I = 0x04
FLAG_D = 0x08
FLAG_B = 0x10
FLAG_U = 0x20  # unused, always 1
FLAG_V = 0x40
FLAG_N = 0x80

STACK_BASE = 0x0100
RESET_VECTOR = 0xFFFC
IRQ_VECTOR = 0xFFFE
NMI_VECTOR = 0xFFFA


class IllegalOpcodeError(Exception):
    """Raised when the CPU encounters an unimplemented/illegal opcode."""

    def __init__(self, opcode: int, pc: int):
        super().__init__(f"illegal/unsupported opcode 0x{opcode:02X} at 0x{pc:04X}")
        self.opcode = opcode
        self.pc = pc


class CpuHalted(Exception):
    """Raised when the CPU executes BRK, used to stop a synthesized program."""


class Memory:
    """A flat 64KiB byte-addressable memory space."""

    __slots__ = ("data",)

    def __init__(self, initial: bytes = b""):
        self.data = bytearray(MEM_SIZE)
        if initial:
            self.data[: len(initial)] = initial

    def read(self, addr: int) -> int:
        return self.data[addr & 0xFFFF]

    def write(self, addr: int, value: int) -> None:
        self.data[addr & 0xFFFF] = value & 0xFF

    def read16(self, addr: int) -> int:
        lo = self.read(addr)
        hi = self.read((addr + 1) & 0xFFFF)
        return lo | (hi << 8)

    def load(self, addr: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.write(addr + i, b)


@dataclass
class Opcode:
    mnemonic: str
    mode: str
    length: int
    cycles: int


def _build_opcode_table() -> Dict[int, Opcode]:
    # (opcode, mnemonic, mode, length, cycles)
    rows = [
        # Load/Store
        (0xA9, "LDA", "imm", 2, 2), (0xA5, "LDA", "zp", 2, 3), (0xB5, "LDA", "zpx", 2, 4),
        (0xAD, "LDA", "abs", 3, 4), (0xBD, "LDA", "absx", 3, 4), (0xB9, "LDA", "absy", 3, 4),
        (0xA1, "LDA", "indx", 2, 6), (0xB1, "LDA", "indy", 2, 5),
        (0xA2, "LDX", "imm", 2, 2), (0xA6, "LDX", "zp", 2, 3), (0xB6, "LDX", "zpy", 2, 4),
        (0xAE, "LDX", "abs", 3, 4), (0xBE, "LDX", "absy", 3, 4),
        (0xA0, "LDY", "imm", 2, 2), (0xA4, "LDY", "zp", 2, 3), (0xB4, "LDY", "zpx", 2, 4),
        (0xAC, "LDY", "abs", 3, 4), (0xBC, "LDY", "absx", 3, 4),
        (0x85, "STA", "zp", 2, 3), (0x95, "STA", "zpx", 2, 4), (0x8D, "STA", "abs", 3, 4),
        (0x9D, "STA", "absx", 3, 5), (0x99, "STA", "absy", 3, 5),
        (0x81, "STA", "indx", 2, 6), (0x91, "STA", "indy", 2, 6),
        (0x86, "STX", "zp", 2, 3), (0x96, "STX", "zpy", 2, 4), (0x8E, "STX", "abs", 3, 4),
        (0x84, "STY", "zp", 2, 3), (0x94, "STY", "zpx", 2, 4), (0x8C, "STY", "abs", 3, 4),
        # Transfers
        (0xAA, "TAX", "imp", 1, 2), (0xA8, "TAY", "imp", 1, 2),
        (0x8A, "TXA", "imp", 1, 2), (0x98, "TYA", "imp", 1, 2),
        (0xBA, "TSX", "imp", 1, 2), (0x9A, "TXS", "imp", 1, 2),
        # Stack
        (0x48, "PHA", "imp", 1, 3), (0x68, "PLA", "imp", 1, 4),
        (0x08, "PHP", "imp", 1, 3), (0x28, "PLP", "imp", 1, 4),
        # Logic
        (0x29, "AND", "imm", 2, 2), (0x25, "AND", "zp", 2, 3), (0x35, "AND", "zpx", 2, 4),
        (0x2D, "AND", "abs", 3, 4), (0x3D, "AND", "absx", 3, 4), (0x39, "AND", "absy", 3, 4),
        (0x21, "AND", "indx", 2, 6), (0x31, "AND", "indy", 2, 5),
        (0x09, "ORA", "imm", 2, 2), (0x05, "ORA", "zp", 2, 3), (0x15, "ORA", "zpx", 2, 4),
        (0x0D, "ORA", "abs", 3, 4), (0x1D, "ORA", "absx", 3, 4), (0x19, "ORA", "absy", 3, 4),
        (0x01, "ORA", "indx", 2, 6), (0x11, "ORA", "indy", 2, 5),
        (0x49, "EOR", "imm", 2, 2), (0x45, "EOR", "zp", 2, 3), (0x55, "EOR", "zpx", 2, 4),
        (0x4D, "EOR", "abs", 3, 4), (0x5D, "EOR", "absx", 3, 4), (0x59, "EOR", "absy", 3, 4),
        (0x41, "EOR", "indx", 2, 6), (0x51, "EOR", "indy", 2, 5),
        (0x24, "BIT", "zp", 2, 3), (0x2C, "BIT", "abs", 3, 4),
        # Arithmetic
        (0x69, "ADC", "imm", 2, 2), (0x65, "ADC", "zp", 2, 3), (0x75, "ADC", "zpx", 2, 4),
        (0x6D, "ADC", "abs", 3, 4), (0x7D, "ADC", "absx", 3, 4), (0x79, "ADC", "absy", 3, 4),
        (0x61, "ADC", "indx", 2, 6), (0x71, "ADC", "indy", 2, 5),
        (0xE9, "SBC", "imm", 2, 2), (0xE5, "SBC", "zp", 2, 3), (0xF5, "SBC", "zpx", 2, 4),
        (0xED, "SBC", "abs", 3, 4), (0xFD, "SBC", "absx", 3, 4), (0xF9, "SBC", "absy", 3, 4),
        (0xE1, "SBC", "indx", 2, 6), (0xF1, "SBC", "indy", 2, 5),
        (0xC9, "CMP", "imm", 2, 2), (0xC5, "CMP", "zp", 2, 3), (0xD5, "CMP", "zpx", 2, 4),
        (0xCD, "CMP", "abs", 3, 4), (0xDD, "CMP", "absx", 3, 4), (0xD9, "CMP", "absy", 3, 4),
        (0xC1, "CMP", "indx", 2, 6), (0xD1, "CMP", "indy", 2, 5),
        (0xE0, "CPX", "imm", 2, 2), (0xE4, "CPX", "zp", 2, 3), (0xEC, "CPX", "abs", 3, 4),
        (0xC0, "CPY", "imm", 2, 2), (0xC4, "CPY", "zp", 2, 3), (0xCC, "CPY", "abs", 3, 4),
        # Increment/Decrement
        (0xE6, "INC", "zp", 2, 5), (0xF6, "INC", "zpx", 2, 6),
        (0xEE, "INC", "abs", 3, 6), (0xFE, "INC", "absx", 3, 7),
        (0xC6, "DEC", "zp", 2, 5), (0xD6, "DEC", "zpx", 2, 6),
        (0xCE, "DEC", "abs", 3, 6), (0xDE, "DEC", "absx", 3, 7),
        (0xE8, "INX", "imp", 1, 2), (0xC8, "INY", "imp", 1, 2),
        (0xCA, "DEX", "imp", 1, 2), (0x88, "DEY", "imp", 1, 2),
        # Shifts/Rotates
        (0x0A, "ASL", "acc", 1, 2), (0x06, "ASL", "zp", 2, 5), (0x16, "ASL", "zpx", 2, 6),
        (0x0E, "ASL", "abs", 3, 6), (0x1E, "ASL", "absx", 3, 7),
        (0x4A, "LSR", "acc", 1, 2), (0x46, "LSR", "zp", 2, 5), (0x56, "LSR", "zpx", 2, 6),
        (0x4E, "LSR", "abs", 3, 6), (0x5E, "LSR", "absx", 3, 7),
        (0x2A, "ROL", "acc", 1, 2), (0x26, "ROL", "zp", 2, 5), (0x36, "ROL", "zpx", 2, 6),
        (0x2E, "ROL", "abs", 3, 6), (0x3E, "ROL", "absx", 3, 7),
        (0x6A, "ROR", "acc", 1, 2), (0x66, "ROR", "zp", 2, 5), (0x76, "ROR", "zpx", 2, 6),
        (0x6E, "ROR", "abs", 3, 6), (0x7E, "ROR", "absx", 3, 7),
        # Jumps/Calls
        (0x4C, "JMP", "abs", 3, 3), (0x6C, "JMP", "ind", 3, 5),
        (0x20, "JSR", "abs", 3, 6), (0x60, "RTS", "imp", 1, 6),
        # Branches
        (0x90, "BCC", "rel", 2, 2), (0xB0, "BCS", "rel", 2, 2),
        (0xF0, "BEQ", "rel", 2, 2), (0xD0, "BNE", "rel", 2, 2),
        (0x30, "BMI", "rel", 2, 2), (0x10, "BPL", "rel", 2, 2),
        (0x50, "BVC", "rel", 2, 2), (0x70, "BVS", "rel", 2, 2),
        # Status flag changes
        (0x18, "CLC", "imp", 1, 2), (0x38, "SEC", "imp", 1, 2),
        (0x58, "CLI", "imp", 1, 2), (0x78, "SEI", "imp", 1, 2),
        (0xB8, "CLV", "imp", 1, 2),
        (0xD8, "CLD", "imp", 1, 2), (0xF8, "SED", "imp", 1, 2),
        # System
        (0x00, "BRK", "imp", 1, 7), (0x40, "RTI", "imp", 1, 6),
        (0xEA, "NOP", "imp", 1, 2),
    ]
    table: Dict[int, Opcode] = {}
    for opcode, mnemonic, mode, length, cycles in rows:
        table[opcode] = Opcode(mnemonic, mode, length, cycles)
    return table


OPCODE_TABLE = _build_opcode_table()


def _to_signed8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


class CPU6502:
    """An exact instruction-level MOS 6502 emulator.

    Usage::

        mem = Memory()
        mem.load(0x0600, program_bytes)
        cpu = CPU6502(mem, pc=0x0600)
        cpu.run(max_steps=1000)
    """

    def __init__(self, memory: Memory, pc: int = 0x0600, sp: int = 0xFD):
        self.mem = memory
        self.a = 0
        self.x = 0
        self.y = 0
        self.sp = sp
        self.pc = pc
        self.c = False
        self.z = False
        self.i = True
        self.d = False
        self.b = False
        self.v = False
        self.n = False
        self.cycles = 0
        self.halted = False

    # -- status register packing -------------------------------------------------
    def get_status(self) -> int:
        status = FLAG_U
        if self.c:
            status |= FLAG_C
        if self.z:
            status |= FLAG_Z
        if self.i:
            status |= FLAG_I
        if self.d:
            status |= FLAG_D
        if self.b:
            status |= FLAG_B
        if self.v:
            status |= FLAG_V
        if self.n:
            status |= FLAG_N
        return status

    def set_status(self, status: int) -> None:
        self.c = bool(status & FLAG_C)
        self.z = bool(status & FLAG_Z)
        self.i = bool(status & FLAG_I)
        self.d = bool(status & FLAG_D)
        self.b = bool(status & FLAG_B)
        self.v = bool(status & FLAG_V)
        self.n = bool(status & FLAG_N)

    # -- stack helpers -------------------------------------------------------------
    def _push(self, value: int) -> None:
        self.mem.write(STACK_BASE + self.sp, value & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    def _pop(self) -> int:
        self.sp = (self.sp + 1) & 0xFF
        return self.mem.read(STACK_BASE + self.sp)

    def _push16(self, value: int) -> None:
        self._push((value >> 8) & 0xFF)
        self._push(value & 0xFF)

    def _pop16(self) -> int:
        lo = self._pop()
        hi = self._pop()
        return lo | (hi << 8)

    # -- flag helpers ----------------------------------------------------------
    def _set_zn(self, value: int) -> None:
        value &= 0xFF
        self.z = value == 0
        self.n = bool(value & 0x80)

    # -- addressing modes: return effective address, or None for imm/imp/acc ---
    def _operand_address(self, mode: str) -> int:
        pc = self.pc
        if mode == "imm":
            addr = pc
            self.pc += 1
            return addr
        if mode == "zp":
            addr = self.mem.read(pc)
            self.pc += 1
            return addr
        if mode == "zpx":
            addr = (self.mem.read(pc) + self.x) & 0xFF
            self.pc += 1
            return addr
        if mode == "zpy":
            addr = (self.mem.read(pc) + self.y) & 0xFF
            self.pc += 1
            return addr
        if mode == "abs":
            addr = self.mem.read16(pc)
            self.pc += 2
            return addr
        if mode == "absx":
            addr = (self.mem.read16(pc) + self.x) & 0xFFFF
            self.pc += 2
            return addr
        if mode == "absy":
            addr = (self.mem.read16(pc) + self.y) & 0xFFFF
            self.pc += 2
            return addr
        if mode == "indx":
            zp = (self.mem.read(pc) + self.x) & 0xFF
            addr = self.mem.read(zp) | (self.mem.read((zp + 1) & 0xFF) << 8)
            self.pc += 1
            return addr
        if mode == "indy":
            zp = self.mem.read(pc)
            base = self.mem.read(zp) | (self.mem.read((zp + 1) & 0xFF) << 8)
            addr = (base + self.y) & 0xFFFF
            self.pc += 1
            return addr
        if mode == "ind":
            ptr = self.mem.read16(pc)
            # Reproduce the famous 6502 page-boundary-wrap bug for JMP (ind).
            lo = self.mem.read(ptr)
            hi_addr = (ptr & 0xFF00) | ((ptr + 1) & 0xFF)
            hi = self.mem.read(hi_addr)
            self.pc += 2
            return lo | (hi << 8)
        raise ValueError(f"unknown addressing mode {mode!r}")

    def step(self) -> int:
        """Execute a single instruction. Returns the number of cycles taken."""
        if self.halted:
            raise CpuHalted("CPU is halted (BRK executed)")
        opcode = self.mem.read(self.pc)
        info = OPCODE_TABLE.get(opcode)
        if info is None:
            raise IllegalOpcodeError(opcode, self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        self._execute(info)
        self.cycles += info.cycles
        return info.cycles

    def run(self, max_steps: int = 10_000) -> int:
        """Run instructions until BRK/halt or max_steps is reached.

        Returns the number of instructions executed. Raises
        :class:`IllegalOpcodeError` if an unsupported opcode is hit.
        """
        steps = 0
        while steps < max_steps and not self.halted:
            self.step()
            steps += 1
        return steps

    # -- execution ---------------------------------------------------------------
    def _execute(self, info: Opcode) -> None:
        handler = getattr(self, f"_op_{info.mnemonic}")
        handler(info.mode)

    # Load/store -----------------------------------------------------------------
    def _op_LDA(self, mode):
        addr = self._operand_address(mode)
        self.a = self.mem.read(addr)
        self._set_zn(self.a)

    def _op_LDX(self, mode):
        addr = self._operand_address(mode)
        self.x = self.mem.read(addr)
        self._set_zn(self.x)

    def _op_LDY(self, mode):
        addr = self._operand_address(mode)
        self.y = self.mem.read(addr)
        self._set_zn(self.y)

    def _op_STA(self, mode):
        addr = self._operand_address(mode)
        self.mem.write(addr, self.a)

    def _op_STX(self, mode):
        addr = self._operand_address(mode)
        self.mem.write(addr, self.x)

    def _op_STY(self, mode):
        addr = self._operand_address(mode)
        self.mem.write(addr, self.y)

    # Transfers --------------------------------------------------------------
    def _op_TAX(self, mode):
        self.x = self.a
        self._set_zn(self.x)

    def _op_TAY(self, mode):
        self.y = self.a
        self._set_zn(self.y)

    def _op_TXA(self, mode):
        self.a = self.x
        self._set_zn(self.a)

    def _op_TYA(self, mode):
        self.a = self.y
        self._set_zn(self.a)

    def _op_TSX(self, mode):
        self.x = self.sp
        self._set_zn(self.x)

    def _op_TXS(self, mode):
        self.sp = self.x

    # Stack --------------------------------------------------------------------
    def _op_PHA(self, mode):
        self._push(self.a)

    def _op_PLA(self, mode):
        self.a = self._pop()
        self._set_zn(self.a)

    def _op_PHP(self, mode):
        self._push(self.get_status() | FLAG_B)

    def _op_PLP(self, mode):
        self.set_status(self._pop())

    # Logic ----------------------------------------------------------------------
    def _op_AND(self, mode):
        addr = self._operand_address(mode)
        self.a &= self.mem.read(addr)
        self._set_zn(self.a)

    def _op_ORA(self, mode):
        addr = self._operand_address(mode)
        self.a |= self.mem.read(addr)
        self._set_zn(self.a)

    def _op_EOR(self, mode):
        addr = self._operand_address(mode)
        self.a ^= self.mem.read(addr)
        self._set_zn(self.a)

    def _op_BIT(self, mode):
        addr = self._operand_address(mode)
        value = self.mem.read(addr)
        self.z = (self.a & value) == 0
        self.n = bool(value & 0x80)
        self.v = bool(value & 0x40)

    # Arithmetic -------------------------------------------------------------
    def _op_ADC(self, mode):
        addr = self._operand_address(mode)
        value = self.mem.read(addr)
        if self.d:
            raise NotImplementedError("decimal mode ADC is not supported")
        total = self.a + value + (1 if self.c else 0)
        result = total & 0xFF
        self.c = total > 0xFF
        self.v = bool((~(self.a ^ value) & (self.a ^ result)) & 0x80)
        self.a = result
        self._set_zn(self.a)

    def _op_SBC(self, mode):
        addr = self._operand_address(mode)
        value = self.mem.read(addr)
        if self.d:
            raise NotImplementedError("decimal mode SBC is not supported")
        inverted = value ^ 0xFF
        total = self.a + inverted + (1 if self.c else 0)
        result = total & 0xFF
        self.c = total > 0xFF
        self.v = bool((~(self.a ^ inverted) & (self.a ^ result)) & 0x80)
        self.a = result
        self._set_zn(self.a)

    def _compare(self, reg: int, mode: str) -> None:
        addr = self._operand_address(mode)
        value = self.mem.read(addr)
        result = (reg - value) & 0x1FF
        self.c = reg >= value
        self._set_zn(result & 0xFF)

    def _op_CMP(self, mode):
        self._compare(self.a, mode)

    def _op_CPX(self, mode):
        self._compare(self.x, mode)

    def _op_CPY(self, mode):
        self._compare(self.y, mode)

    # Increment/decrement ------------------------------------------------------
    def _op_INC(self, mode):
        addr = self._operand_address(mode)
        value = (self.mem.read(addr) + 1) & 0xFF
        self.mem.write(addr, value)
        self._set_zn(value)

    def _op_DEC(self, mode):
        addr = self._operand_address(mode)
        value = (self.mem.read(addr) - 1) & 0xFF
        self.mem.write(addr, value)
        self._set_zn(value)

    def _op_INX(self, mode):
        self.x = (self.x + 1) & 0xFF
        self._set_zn(self.x)

    def _op_INY(self, mode):
        self.y = (self.y + 1) & 0xFF
        self._set_zn(self.y)

    def _op_DEX(self, mode):
        self.x = (self.x - 1) & 0xFF
        self._set_zn(self.x)

    def _op_DEY(self, mode):
        self.y = (self.y - 1) & 0xFF
        self._set_zn(self.y)

    # Shifts/rotates -------------------------------------------------------------
    def _op_ASL(self, mode):
        if mode == "acc":
            value = self.a
            self.c = bool(value & 0x80)
            result = (value << 1) & 0xFF
            self.a = result
        else:
            addr = self._operand_address(mode)
            value = self.mem.read(addr)
            self.c = bool(value & 0x80)
            result = (value << 1) & 0xFF
            self.mem.write(addr, result)
        self._set_zn(result)

    def _op_LSR(self, mode):
        if mode == "acc":
            value = self.a
            self.c = bool(value & 0x01)
            result = value >> 1
            self.a = result
        else:
            addr = self._operand_address(mode)
            value = self.mem.read(addr)
            self.c = bool(value & 0x01)
            result = value >> 1
            self.mem.write(addr, result)
        self._set_zn(result)

    def _op_ROL(self, mode):
        old_c = 1 if self.c else 0
        if mode == "acc":
            value = self.a
            self.c = bool(value & 0x80)
            result = ((value << 1) | old_c) & 0xFF
            self.a = result
        else:
            addr = self._operand_address(mode)
            value = self.mem.read(addr)
            self.c = bool(value & 0x80)
            result = ((value << 1) | old_c) & 0xFF
            self.mem.write(addr, result)
        self._set_zn(result)

    def _op_ROR(self, mode):
        old_c = 0x80 if self.c else 0
        if mode == "acc":
            value = self.a
            self.c = bool(value & 0x01)
            result = (value >> 1) | old_c
            self.a = result
        else:
            addr = self._operand_address(mode)
            value = self.mem.read(addr)
            self.c = bool(value & 0x01)
            result = (value >> 1) | old_c
            self.mem.write(addr, result)
        self._set_zn(result)

    # Jumps/calls ------------------------------------------------------------
    def _op_JMP(self, mode):
        addr = self._operand_address(mode)
        self.pc = addr

    def _op_JSR(self, mode):
        # Operand address consumes the two bytes after the opcode; the return
        # address pushed is the address of the last byte of JSR (pc+2-1).
        return_addr = (self.pc + 1) & 0xFFFF
        addr = self._operand_address("abs")
        self._push16(return_addr)
        self.pc = addr

    def _op_RTS(self, mode):
        self.pc = (self._pop16() + 1) & 0xFFFF

    # Branches -----------------------------------------------------------------
    def _branch(self, condition: bool) -> None:
        offset = _to_signed8(self.mem.read(self.pc))
        self.pc = (self.pc + 1) & 0xFFFF
        if condition:
            self.pc = (self.pc + offset) & 0xFFFF

    def _op_BCC(self, mode):
        self._branch(not self.c)

    def _op_BCS(self, mode):
        self._branch(self.c)

    def _op_BEQ(self, mode):
        self._branch(self.z)

    def _op_BNE(self, mode):
        self._branch(not self.z)

    def _op_BMI(self, mode):
        self._branch(self.n)

    def _op_BPL(self, mode):
        self._branch(not self.n)

    def _op_BVC(self, mode):
        self._branch(not self.v)

    def _op_BVS(self, mode):
        self._branch(self.v)

    # Status flags ---------------------------------------------------------------
    def _op_CLC(self, mode):
        self.c = False

    def _op_SEC(self, mode):
        self.c = True

    def _op_CLI(self, mode):
        self.i = False

    def _op_SEI(self, mode):
        self.i = True

    def _op_CLV(self, mode):
        self.v = False

    def _op_CLD(self, mode):
        self.d = False

    def _op_SED(self, mode):
        self.d = True

    # System -----------------------------------------------------------------
    def _op_BRK(self, mode):
        self.halted = True

    def _op_RTI(self, mode):
        self.set_status(self._pop())
        self.pc = self._pop16()

    def _op_NOP(self, mode):
        pass
