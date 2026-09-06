"""Unit tests for executable specifications."""

import random
import unittest

from tbd.spec import SPECS, MachineState, get_spec


class SpecTests(unittest.TestCase):
    def test_all_specs_registered_and_constructible(self):
        for name in SPECS:
            spec = get_spec(name)
            self.assertEqual(spec.name, name)

    def test_unknown_spec_raises_key_error(self):
        with self.assertRaises(KeyError):
            get_spec("does-not-exist")

    def test_increment_spec_check(self):
        spec = get_spec("increment")
        initial = MachineState(zero_page={0x00: 10})
        good_final = MachineState(zero_page={0x01: 11})
        bad_final = MachineState(zero_page={0x01: 10})
        self.assertTrue(spec.check(initial, good_final))
        self.assertFalse(spec.check(initial, bad_final))

    def test_increment_spec_wraps_at_256(self):
        spec = get_spec("increment")
        initial = MachineState(zero_page={0x00: 255})
        good_final = MachineState(zero_page={0x01: 0})
        self.assertTrue(spec.check(initial, good_final))

    def test_holdout_distribution_disjoint_from_training(self):
        rng = random.Random(0)
        for name in SPECS:
            spec = get_spec(name)
            train_vals = {
                tuple(sorted(spec.sample_state(rng).zero_page.items()))
                for _ in range(500)
            }
            holdout_vals = {
                tuple(sorted(spec.sample_holdout_state(rng).zero_page.items()))
                for _ in range(500)
            }
            overlap = train_vals & holdout_vals
            self.assertEqual(
                overlap, set(), f"{name}: train/holdout states should not overlap"
            )

    def test_evaluate_correct_program_via_emulator(self):
        # LDA $00; CLC; ADC #1; STA $01; BRK
        program = bytes([0xA5, 0x00, 0x18, 0x69, 0x01, 0x85, 0x01, 0x00])
        spec = get_spec("increment")
        rng = random.Random(1)
        for _ in range(50):
            state = spec.sample_state(rng)
            self.assertTrue(spec.evaluate(program, state))

    def test_evaluate_incorrect_program_fails(self):
        # LDA $00; STA $01; BRK  (forgets to increment)
        program = bytes([0xA5, 0x00, 0x85, 0x01, 0x00])
        spec = get_spec("increment")
        rng = random.Random(2)
        state = spec.sample_state(rng)
        self.assertFalse(spec.evaluate(program, state))

    def test_evaluate_handles_illegal_opcode_gracefully(self):
        program = bytes([0xFF])  # illegal opcode
        spec = get_spec("increment")
        rng = random.Random(3)
        state = spec.sample_state(rng)
        self.assertFalse(spec.evaluate(program, state))

    def test_evaluate_handles_program_that_never_halts(self):
        # An infinite loop: JMP $0600 (jumps to itself forever).
        program = bytes([0x4C, 0x00, 0x06])
        spec = get_spec("increment")
        rng = random.Random(4)
        state = spec.sample_state(rng)
        self.assertFalse(spec.evaluate(program, state, org=0x0600))


if __name__ == "__main__":
    unittest.main()
