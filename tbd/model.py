"""A tiny, randomly initialized neural policy network (numpy, no autodiff).

The policy is an autoregressive token predictor: given an embedding of the
task (which spec we're synthesizing for) and the tokens emitted so far, it
outputs a categorical distribution over the next token in the program. It
is intentionally small (a two-layer MLP over a fixed-size recurrent
hidden state) since the goal is to demonstrate that even a small randomly
initialized network can be trained, via a black-box reward signal from the
CPU-emulator oracle, to synthesize correct and generalizing programs -- not
to build a large language model.

Training uses REINFORCE (a vanilla policy-gradient estimator), which only
needs forward passes plus manually-derived gradients for this tiny
architecture; no autodiff framework is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x, axis=-1, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=-1, keepdims=True)


@dataclass
class PolicyConfig:
    vocab_size: int
    num_tasks: int
    hidden_size: int = 32
    task_embed_size: int = 8
    token_embed_size: int = 8
    seed: int = 0


class RecurrentPolicy:
    """A minimal Elman-RNN policy: h_t = tanh(W_h h_{t-1} + W_x x_t + b_h).

    The input x_t at each step is the concatenation of the task embedding
    and the embedding of the previously emitted token (a zero vector for
    the first step). The output distribution over the next token is
    softmax(W_o h_t + b_o).

    All parameters are stored in a flat dict of numpy arrays so the whole
    policy can be perturbed/updated generically.
    """

    def __init__(self, config: PolicyConfig):
        self.cfg = config
        rng = np.random.RandomState(config.seed)
        v, h = config.vocab_size, config.hidden_size
        te, ke = config.task_embed_size, config.token_embed_size
        in_size = te + ke

        def randn(*shape, scale=0.1):
            return (rng.randn(*shape) * scale).astype(np.float64)

        self.params = {
            "task_embed": randn(config.num_tasks, te),
            "token_embed": randn(v, ke),
            "Wx": randn(in_size, h),
            "Wh": randn(h, h),
            "bh": np.zeros(h),
            "Wo": randn(h, v),
            "bo": np.zeros(v),
        }

    def initial_hidden(self) -> np.ndarray:
        return np.zeros(self.cfg.hidden_size)

    def step(
        self, task_id: int, prev_token: int, hidden: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """One recurrent step. Returns (action_probs, new_hidden)."""
        p = self.params
        task_vec = p["task_embed"][task_id]
        if prev_token is None:
            token_vec = np.zeros(self.cfg.token_embed_size)
        else:
            token_vec = p["token_embed"][prev_token]
        x = np.concatenate([task_vec, token_vec])
        new_hidden = np.tanh(x @ p["Wx"] + hidden @ p["Wh"] + p["bh"])
        logits = new_hidden @ p["Wo"] + p["bo"]
        probs = _softmax(logits)
        return probs, new_hidden

    def sample_program(
        self, task_id: int, length: int, rng: np.random.RandomState
    ) -> Tuple[List[int], List[np.ndarray], List[np.ndarray]]:
        """Sample a length-`length` token sequence.

        Returns (tokens, hiddens, probs_list) where hiddens[t]/probs_list[t]
        are the recurrent state/action distribution used to sample tokens[t]
        (needed later to compute policy-gradient updates).
        """
        tokens: List[int] = []
        hiddens: List[np.ndarray] = []
        probs_list: List[np.ndarray] = []
        hidden = self.initial_hidden()
        prev_token = None
        for _ in range(length):
            probs, hidden = self.step(task_id, prev_token, hidden)
            token = rng.choice(self.cfg.vocab_size, p=probs)
            tokens.append(int(token))
            hiddens.append(hidden)
            probs_list.append(probs)
            prev_token = token
        return tokens, hiddens, probs_list
