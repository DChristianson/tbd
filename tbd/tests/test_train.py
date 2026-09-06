"""End-to-end tests for the training loop: does the system actually learn
programs that both fit the training distribution and generalize to a
disjoint held-out distribution of machine states?

These tests intentionally use small, cheap tasks/hyperparameters to keep
CI runtime low while still exercising the full pipeline: random
initialization -> REINFORCE (with elite-guided local mutation) search ->
emulator-checked reward -> convergence -> held-out generalization.
"""

import unittest

import numpy as np

from tbd.assembler import VOCAB_SIZE, assemble
from tbd.model import PolicyConfig, RecurrentPolicy
from tbd.train import (
    AdamOptimizer,
    _zero_grads,
    accumulate_grad,
    describe_program,
    rollout_from_prefix,
    train,
)


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
        self.assertLess(result.best_train_score, 0.9)


class TrainingLoopUnitTests(unittest.TestCase):
    """Focused unit tests for the REINFORCE building blocks in tbd.train,
    which are otherwise only exercised indirectly through the end-to-end
    training tests above.
    """

    def test_adam_optimizer_moves_params_downhill(self):
        params = {"w": np.array([1.0, -2.0, 3.0])}
        grads = {"w": np.array([1.0, -1.0, 0.5])}
        optimizer = AdamOptimizer(params, lr=0.1)
        before = params["w"].copy()
        optimizer.step(params, grads)
        # Adam's first update moves each parameter by exactly -lr in the
        # direction of its gradient's sign (since m_hat/sqrt(v_hat) == sign
        # of the gradient on the very first step).
        np.testing.assert_allclose(params["w"] - before, -0.1 * np.sign(grads["w"]))

    def test_rollout_from_prefix_honors_teacher_forced_cut(self):
        config = PolicyConfig(vocab_size=VOCAB_SIZE, num_tasks=1, hidden_size=8, seed=1)
        policy = RecurrentPolicy(config)
        prefix = [3, 5, 7, 9]
        rng = np.random.RandomState(0)
        tokens, hiddens, probs_list, cut = rollout_from_prefix(
            policy, 0, len(prefix), prefix, cut=2, rng=rng
        )
        self.assertEqual(cut, 2)
        self.assertEqual(tokens[:2], prefix[:2])
        self.assertEqual(len(tokens), len(prefix))
        self.assertEqual(len(hiddens), len(prefix))
        self.assertEqual(len(probs_list), len(prefix))

    def test_accumulate_grad_ignores_forced_prefix(self):
        config = PolicyConfig(vocab_size=VOCAB_SIZE, num_tasks=1, hidden_size=8, seed=2)
        policy = RecurrentPolicy(config)
        prefix = [1, 2, 3, 4]
        rng = np.random.RandomState(0)
        # cut == length means every position is teacher-forced (forced
        # prefix only, nothing freely sampled this episode).
        tokens, hiddens, probs_list, cut = rollout_from_prefix(
            policy, 0, len(prefix), prefix, cut=len(prefix), rng=rng
        )
        grads = _zero_grads(policy.params)
        accumulate_grad(policy, 0, tokens, hiddens, probs_list, cut, advantage=1.0, grads=grads)
        for key, grad in grads.items():
            np.testing.assert_allclose(grad, np.zeros_like(grad), err_msg=f"{key} should be zero")


if __name__ == "__main__":
    unittest.main()
