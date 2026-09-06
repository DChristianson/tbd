"""Unit tests for the token assembler."""

import unittest

from tbd.assembler import VOCAB, VOCAB_SIZE, NAME_TO_INDEX, AssemblyError, assemble
from tbd.emulator.cpu6502 import CPU6502, Memory


def ids_for(*names):
    return [NAME_TO_INDEX[n] for n in names]


class AssemblerTests(unittest.TestCase):
    def test_vocab_names_unique(self):
        names = [tok.name for tok in VOCAB]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), VOCAB_SIZE)

    def test_simple_program_assembles_and_runs(self):
        program = assemble(ids_for("LDA_ZP0", "CLC", "ADC_ZP1", "STA_ZP2"))
        mem = Memory()
        mem.load(0x0600, program)
        mem.write(0x00, 10)
        mem.write(0x01, 5)
        cpu = CPU6502(mem, pc=0x0600)
        cpu.run(100)
        self.assertTrue(cpu.halted)
        self.assertEqual(mem.read(0x02), 15)

    def test_every_program_ends_with_brk(self):
        program = assemble(ids_for("NOP", "NOP"))
        self.assertEqual(program[-1], 0x00)

    def test_branch_skip_forward(self):
        # LDA #1; CMP with itself semantics aside: exercise BCS_SKIP1 raw.
        program = assemble(
            ids_for("LDA_IMM1", "SEC", "BCS_SKIP1", "LDA_IMM0", "STA_ZP0")
        )
        mem = Memory()
        mem.load(0x0600, program)
        cpu = CPU6502(mem, pc=0x0600)
        cpu.run(100)
        # BCS should be taken (carry set by SEC), skipping the LDA_IMM0,
        # so A remains 1 and is stored.
        self.assertEqual(mem.read(0x00), 1)

    def test_branch_skip_to_end_of_program(self):
        # A branch that skips past the last token should target the
        # trailing BRK rather than raising an index error.
        program = assemble(ids_for("SEC", "BCS_SKIP2", "LDA_IMM1"))
        mem = Memory()
        mem.load(0x0600, program)
        cpu = CPU6502(mem, pc=0x0600)
        cpu.run(100)
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.a, 0)  # LDA_IMM1 was skipped

    def test_max_via_compare_and_branch(self):
        program = assemble(
            ids_for("LDA_ZP0", "CMP_ZP1", "BCS_SKIP1", "LDA_ZP1", "STA_ZP2")
        )
        for a, b in [(3, 9), (9, 3), (5, 5), (0, 255), (255, 0)]:
            mem = Memory()
            mem.load(0x0600, program)
            mem.write(0x00, a)
            mem.write(0x01, b)
            cpu = CPU6502(mem, pc=0x0600)
            cpu.run(100)
            self.assertEqual(mem.read(0x02), max(a, b), f"a={a} b={b}")

    def test_max_via_fused_compare_branch_token(self):
        # Same program as above, but using the single fused CMPB token
        # instead of separate CMP_ZP1 + BCS_SKIP1 tokens.
        program = assemble(
            ids_for("LDA_ZP0", "CMPB_ZP1_BCS_SKIP1", "LDA_ZP1", "STA_ZP2")
        )
        for a, b in [(3, 9), (9, 3), (5, 5), (0, 255), (255, 0)]:
            mem = Memory()
            mem.load(0x0600, program)
            mem.write(0x00, a)
            mem.write(0x01, b)
            cpu = CPU6502(mem, pc=0x0600)
            cpu.run(100)
            self.assertEqual(mem.read(0x02), max(a, b), f"a={a} b={b}")


    def test_out_of_range_branch_raises(self):
        # The built-in vocabulary only offers small forward skips, so no
        # real token sequence can overflow a signed-8-bit relative branch.
        # Exercise the range check directly by temporarily registering a
        # token with an enormous skip count.
        from tbd.assembler import Token

        huge_skip_token = Token(
            name="BCS_SKIP_HUGE", branch_mnemonic="BCS", skip=200
        )
        VOCAB.append(huge_skip_token)
        try:
            huge_index = len(VOCAB) - 1
            tokens = [huge_index] + ids_for("LDA_IMM1") * 200
            with self.assertRaises(AssemblyError):
                assemble(tokens)
        finally:
            VOCAB.pop()


if __name__ == "__main__":
    unittest.main()
