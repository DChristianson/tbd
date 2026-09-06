"""Unit tests for the 6502 CPU emulator."""

import unittest

from tbd.emulator.cpu6502 import CPU6502, IllegalOpcodeError, Memory


def run(program, org=0x0600, pc_setup=None, max_steps=1000):
    mem = Memory()
    mem.load(org, bytes(program))
    cpu = CPU6502(mem, pc=org)
    if pc_setup:
        pc_setup(cpu)
    cpu.run(max_steps=max_steps)
    return cpu


class LoadStoreTests(unittest.TestCase):
    def test_lda_immediate_sets_registers_and_flags(self):
        cpu = run([0xA9, 0x00, 0x00])  # LDA #0; BRK
        self.assertEqual(cpu.a, 0)
        self.assertTrue(cpu.z)
        self.assertFalse(cpu.n)

    def test_lda_negative_sets_n_flag(self):
        cpu = run([0xA9, 0x80, 0x00])  # LDA #$80; BRK
        self.assertEqual(cpu.a, 0x80)
        self.assertFalse(cpu.z)
        self.assertTrue(cpu.n)

    def test_sta_zero_page(self):
        cpu = run([0xA9, 0x42, 0x85, 0x10, 0x00])  # LDA #$42; STA $10; BRK
        self.assertEqual(cpu.mem.read(0x10), 0x42)

    def test_ldx_ldy_and_transfers(self):
        cpu = run([
            0xA2, 0x05,  # LDX #5
            0xA0, 0x07,  # LDY #7
            0x8A,        # TXA
            0x00,        # BRK
        ])
        self.assertEqual(cpu.a, 5)
        self.assertEqual(cpu.x, 5)
        self.assertEqual(cpu.y, 7)


class ArithmeticTests(unittest.TestCase):
    def test_adc_simple(self):
        cpu = run([0xA9, 0x05, 0x18, 0x69, 0x03, 0x00])  # LDA#5;CLC;ADC#3;BRK
        self.assertEqual(cpu.a, 8)
        self.assertFalse(cpu.c)

    def test_adc_carry_out(self):
        cpu = run([0xA9, 0xFF, 0x18, 0x69, 0x02, 0x00])  # 255 + 2
        self.assertEqual(cpu.a, 1)
        self.assertTrue(cpu.c)

    def test_adc_overflow_flag(self):
        # 0x50 + 0x50 = 0xA0: signed overflow (127+... -> negative)
        cpu = run([0xA9, 0x50, 0x18, 0x69, 0x50, 0x00])
        self.assertEqual(cpu.a, 0xA0)
        self.assertTrue(cpu.v)

    def test_sbc_simple(self):
        cpu = run([0xA9, 0x05, 0x38, 0xE9, 0x03, 0x00])  # LDA#5;SEC;SBC#3;BRK
        self.assertEqual(cpu.a, 2)
        self.assertTrue(cpu.c)

    def test_sbc_borrow(self):
        cpu = run([0xA9, 0x03, 0x38, 0xE9, 0x05, 0x00])  # 3 - 5 -> borrow
        self.assertEqual(cpu.a, 0xFE)
        self.assertFalse(cpu.c)

    def test_cmp_sets_carry_when_greater_or_equal(self):
        cpu = run([0xA9, 0x05, 0xC9, 0x03, 0x00])  # LDA#5; CMP#3
        self.assertTrue(cpu.c)
        self.assertFalse(cpu.z)

    def test_cmp_sets_zero_when_equal(self):
        cpu = run([0xA9, 0x05, 0xC9, 0x05, 0x00])
        self.assertTrue(cpu.c)
        self.assertTrue(cpu.z)


class IncDecTests(unittest.TestCase):
    def test_inx_wraps(self):
        cpu = run([0xA2, 0xFF, 0xE8, 0x00])  # LDX #$FF; INX; BRK
        self.assertEqual(cpu.x, 0x00)
        self.assertTrue(cpu.z)

    def test_dec_zero_page(self):
        mem = Memory()
        mem.load(0x0600, bytes([0xC6, 0x10, 0x00]))  # DEC $10; BRK
        mem.write(0x10, 0x01)
        cpu = CPU6502(mem, pc=0x0600)
        cpu.run(100)
        self.assertEqual(mem.read(0x10), 0x00)
        self.assertTrue(cpu.z)


class BranchTests(unittest.TestCase):
    def test_bne_taken(self):
        # LDX #1; DEX; BNE +2 (skips STA); LDA #$AA; STA $10; BRK
        prog = [
            0xA2, 0x02,        # LDX #2
            0xCA,              # DEX -> X=1, Z=0
            0xD0, 0x02,        # BNE +2 (skip next 2 bytes: LDA #$AA)
            0xA9, 0xAA,        # LDA #$AA (skipped)
            0x85, 0x10,        # STA $10
            0x00,              # BRK
        ]
        cpu = run(prog)
        self.assertEqual(cpu.mem.read(0x10), 0x00)  # A was never $AA

    def test_beq_not_taken_falls_through(self):
        prog = [
            0xA9, 0x01,   # LDA #1
            0xF0, 0x02,   # BEQ +2 (not taken since A!=0)
            0xA9, 0x99,   # LDA #$99
            0x00,         # BRK
        ]
        cpu = run(prog)
        self.assertEqual(cpu.a, 0x99)


class JumpSubroutineTests(unittest.TestCase):
    def test_jsr_rts(self):
        prog = [
            0x20, 0x06, 0x06,  # JSR $0606
            0x00,              # BRK  (0x0603)
            0xEA, 0xEA,        # padding (0x0604-0605)
            0xA9, 0x42,        # $0606: LDA #$42
            0x60,              # RTS
        ]
        cpu = run(prog)
        self.assertEqual(cpu.a, 0x42)

    def test_jmp_absolute(self):
        prog = [
            0x4C, 0x05, 0x06,  # JMP $0605
            0xA9, 0x00,        # (skipped) LDA #0
            0xA9, 0x7B,        # $0605: LDA #$7B
            0x00,
        ]
        cpu = run(prog)
        self.assertEqual(cpu.a, 0x7B)


class StackTests(unittest.TestCase):
    def test_pha_pla_roundtrip(self):
        prog = [
            0xA9, 0x11,  # LDA #$11
            0x48,        # PHA
            0xA9, 0x22,  # LDA #$22
            0x68,        # PLA
            0x00,
        ]
        cpu = run(prog)
        self.assertEqual(cpu.a, 0x11)


class IllegalOpcodeTests(unittest.TestCase):
    def test_illegal_opcode_raises(self):
        mem = Memory()
        mem.load(0x0600, bytes([0xFF]))  # not a supported opcode
        cpu = CPU6502(mem, pc=0x0600)
        with self.assertRaises(IllegalOpcodeError):
            cpu.run(10)


if __name__ == "__main__":
    unittest.main()
