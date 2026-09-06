"""Unit tests for the tiny recurrent policy network."""

import unittest

import numpy as np

from tbd.model import PolicyConfig, RecurrentPolicy


class PolicyTests(unittest.TestCase):
    def _make_policy(self, seed=0):
        config = PolicyConfig(vocab_size=10, num_tasks=2, hidden_size=8, seed=seed)
        return RecurrentPolicy(config)

    def test_step_returns_valid_probability_distribution(self):
        policy = self._make_policy()
        hidden = policy.initial_hidden()
        probs, new_hidden = policy.step(task_id=0, prev_token=None, hidden=hidden)
        self.assertEqual(probs.shape, (10,))
        self.assertTrue(np.all(probs >= 0))
        self.assertAlmostEqual(probs.sum(), 1.0, places=6)
        self.assertEqual(new_hidden.shape, (8,))

    def test_same_seed_gives_identical_initial_weights(self):
        p1 = self._make_policy(seed=42)
        p2 = self._make_policy(seed=42)
        for key in p1.params:
            np.testing.assert_array_equal(p1.params[key], p2.params[key])

    def test_different_seeds_give_different_weights(self):
        p1 = self._make_policy(seed=1)
        p2 = self._make_policy(seed=2)
        self.assertFalse(np.array_equal(p1.params["Wo"], p2.params["Wo"]))

    def test_sample_program_has_requested_length(self):
        policy = self._make_policy()
        rng = np.random.RandomState(0)
        tokens, hiddens, probs_list = policy.sample_program(
            task_id=0, length=5, rng=rng
        )
        self.assertEqual(len(tokens), 5)
        self.assertEqual(len(hiddens), 5)
        self.assertEqual(len(probs_list), 5)
        self.assertTrue(all(0 <= t < 10 for t in tokens))

    def test_task_conditioning_changes_distribution(self):
        policy = self._make_policy()
        hidden = policy.initial_hidden()
        probs_task0, _ = policy.step(task_id=0, prev_token=None, hidden=hidden)
        probs_task1, _ = policy.step(task_id=1, prev_token=None, hidden=hidden)
        self.assertFalse(np.allclose(probs_task0, probs_task1))


if __name__ == "__main__":
    unittest.main()
