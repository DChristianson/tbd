"""End-to-end tests for the training loop: does the system actually learn
programs that both fit the training distribution and generalize to a
disjoint held-out distribution of machine states?

These tests intentionally use small, cheap tasks/hyperparameters to keep
CI runtime low while still exercising the full pipeline: random
initialization -> REINFORCE (with elite-guided local mutation) search ->
emulator-checked reward -> convergence -> held-out generalization.
"""

import unittest

from tbd.assembler import assemble
from tbd.spec import get_spec
from tbd.train import describe_program, train


class EndToEndTrainingTests(unittest.TestCase):
    def test_synthesizes_correct_and_generalizing_copy_program(self):
        result = train(
            task_names=["copy"],
            program_length=4,
            generations=80,
            episodes_per_generation=48,
            lr=0.03,
            batch_size=16,
            eval_batch_size=64,
            seed=123,
            verbose=False,
        )
        self.assertGreaterEqual(result.best_train_score, 0.95)
        self.assertGreaterEqual(result.best_holdout_score, 0.95)

    def test_synthesizes_correct_and_generalizing_increment_program(self):
        result = train(
            task_names=["increment"],
            program_length=6,
            generations=250,
            episodes_per_generation=64,
            lr=0.03,
            batch_size=16,
            eval_batch_size=64,
            seed=7,
            verbose=False,
        )
        self.assertGreaterEqual(result.best_train_score, 0.9)
        self.assertGreaterEqual(result.best_holdout_score, 0.9)

    def test_final_program_is_valid_machine_code(self):
        result = train(
            task_names=["copy"],
            program_length=4,
            generations=40,
            episodes_per_generation=32,
            lr=0.03,
            seed=5,
            verbose=False,
        )
        tokens = result.best_tokens["copy"]
        program = assemble(tokens)
        self.assertTrue(program)
        self.assertIsInstance(describe_program(tokens), str)

    def test_random_untrained_policy_rarely_solves_the_task(self):
        # Sanity check that training is actually doing something: a
        # single-generation "policy" (i.e. essentially the random init)
        # should not already satisfy the spec at a high rate.
        result = train(
            task_names=["add"],
            program_length=6,
            generations=1,
            episodes_per_generation=8,
            seed=99,
            verbose=False,
        )
        spec = get_spec("add")
        self.assertLess(result.best_train_score, 0.9)


if __name__ == "__main__":
    unittest.main()
