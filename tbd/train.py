"""Train a small neural policy to synthesize 6502 programs via REINFORCE.

Because the reward signal (does the emulated program satisfy the spec?) is
discrete and non-differentiable through the CPU emulator, we train the
randomly initialized recurrent policy with REINFORCE (Williams, 1992): each
generation rolls out a batch of programs by sampling tokens from the
policy's own categorical distributions, executes each assembled program on
a batch of training machine states through the exact CPU emulator (the
oracle), and uses the resulting reward (fraction of states for which the
spec holds) to weight the score-function gradient of the log-probability of
the sampled tokens. Gradients are derived analytically and backpropagated
through the small recurrent network by hand (no autodiff framework is used,
matching the project's small, dependency-light design).

Blind full-sequence sampling makes it exponentially unlikely to ever *stumble
upon* a program that needs several specific tokens in a row correct at once
(e.g. "compare, then branch, then load, then store" for a max/clamp-style
task) purely by chance, long before the policy's distribution has been
shaped enough to make that sequence likely. To make such multi-token
combinations discoverable, each episode resumes from the best program found
so far (kept per task) up to a random cut point, and only samples freely
from that point onward -- a neural-guided form of hill-climbing / local
search over programs, in the same spirit as mutation operators in genetic
programming, except the "mutation proposal" at each freed position comes
from the (still-training) policy's own distribution rather than being
uniform random.

Generalization is measured throughout training on a held-out distribution
of machine states that is never used to compute the training reward, and
(for multi-task runs) across different task/spec combinations.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from tbd.assembler import VOCAB, VOCAB_SIZE, AssemblyError, assemble
from tbd.model import PolicyConfig, RecurrentPolicy
from tbd.spec import SPECS, Spec, get_spec

# Base of the exponential weighting used to pick how many trailing token
# positions are freely sampled each episode (see `train()`). A base of 2.0
# means each additional free position is twice as likely as the one before
# it, so most episodes free (nearly) the whole program -- giving ordinary
# REINFORCE the most exploration -- while a shrinking fraction instead
# mutate only a few trailing positions of the current best-known program.
# Chosen empirically: smaller bases made local mutation too rare to help
# escape multi-token local optima; larger bases made mutation episodes too
# rare to meaningfully refine already-decent elites.
FREE_LENGTH_WEIGHT_BASE = 2.0


def evaluate_program(token_ids: List[int], spec: Spec, states: List) -> float:
    """Fraction of `states` for which the assembled program satisfies spec."""
    try:
        program = assemble(token_ids)
    except AssemblyError:
        return 0.0
    passed = 0
    for state in states:
        if spec.evaluate(program, state):
            passed += 1
    return passed / len(states)


def greedy_program(policy: RecurrentPolicy, task_id: int, length: int) -> List[int]:
    """Decode a program deterministically (argmax at each step)."""
    tokens: List[int] = []
    hidden = policy.initial_hidden()
    prev_token: Optional[int] = None
    for _ in range(length):
        probs, hidden = policy.step(task_id, prev_token, hidden)
        token = int(np.argmax(probs))
        tokens.append(token)
        prev_token = token
    return tokens


class AdamOptimizer:
    """A minimal Adam optimizer over a dict of numpy-array parameters."""

    def __init__(self, params, lr=0.01, beta1=0.9, beta2=0.999, eps=1e-8):
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params: Dict[str, np.ndarray], grads: Dict[str, np.ndarray]) -> None:
        self.t += 1
        for key in params:
            g = grads[key]
            self.m[key] = self.beta1 * self.m[key] + (1 - self.beta1) * g
            self.v[key] = self.beta2 * self.v[key] + (1 - self.beta2) * (g * g)
            m_hat = self.m[key] / (1 - self.beta1**self.t)
            v_hat = self.v[key] / (1 - self.beta2**self.t)
            params[key] -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


def _zero_grads(params: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    return {k: np.zeros_like(v) for k, v in params.items()}


def rollout_from_prefix(
    policy: RecurrentPolicy,
    task_id: int,
    length: int,
    prefix: List[int],
    cut: int,
    rng: np.random.RandomState,
) -> Tuple[List[int], List[np.ndarray], List[np.ndarray], int]:
    """Force tokens[:cut] = prefix[:cut] (teacher forcing), then sample
    freely from position `cut` onward.

    Returns (tokens, hiddens, probs_list, cut) where hiddens[t]/probs_list[t]
    are the forward-pass activations actually used to produce tokens[t],
    for the *entire* sequence (including the forced prefix, which is needed
    to keep the recurrent state correct, even though its gradient is not
    used).
    """
    tokens: List[int] = []
    hiddens: List[np.ndarray] = []
    probs_list: List[np.ndarray] = []
    hidden = policy.initial_hidden()
    prev_token: Optional[int] = None
    for t in range(length):
        probs, hidden = policy.step(task_id, prev_token, hidden)
        if t < cut:
            token = prefix[t]
        else:
            token = int(rng.choice(policy.cfg.vocab_size, p=probs))
        tokens.append(token)
        hiddens.append(hidden)
        probs_list.append(probs)
        prev_token = token
    return tokens, hiddens, probs_list, cut


def accumulate_grad(
    policy: RecurrentPolicy,
    task_id: int,
    tokens: List[int],
    hiddens: List[np.ndarray],
    probs_list: List[np.ndarray],
    cut: int,
    advantage: float,
    grads: Dict[str, np.ndarray],
) -> None:
    """Backprop the REINFORCE gradient (scaled by `advantage`) through the
    sampled suffix ``tokens[cut:]`` only; the forced prefix ``tokens[:cut]``
    contributes no gradient since it was not a sampling decision made by
    the current policy this episode.
    """
    p = policy.params
    cfg = policy.cfg
    T = len(tokens)
    hidden_prev_seq = [policy.initial_hidden()] + hiddens[:-1]
    prev_token_seq: List[Optional[int]] = [None] + tokens[:-1]

    dh_next = np.zeros(cfg.hidden_size)
    for t in reversed(range(T)):
        if t >= cut:
            probs = probs_list[t]
            token = tokens[t]
            onehot = np.zeros_like(probs)
            onehot[token] = 1.0
            grad_logits = advantage * (probs - onehot)
        else:
            grad_logits = np.zeros(cfg.vocab_size)

        h_t = hiddens[t]
        grads["Wo"] += np.outer(h_t, grad_logits)
        grads["bo"] += grad_logits

        dh = p["Wo"] @ grad_logits + dh_next
        dz = dh * (1 - h_t**2)
        grads["bh"] += dz

        h_prev = hidden_prev_seq[t]
        grads["Wh"] += np.outer(h_prev, dz)

        prev_token = prev_token_seq[t]
        task_vec = p["task_embed"][task_id]
        if prev_token is None:
            token_vec = np.zeros(cfg.token_embed_size)
        else:
            token_vec = p["token_embed"][prev_token]
        x_t = np.concatenate([task_vec, token_vec])
        grads["Wx"] += np.outer(x_t, dz)

        dx = p["Wx"] @ dz
        te = cfg.task_embed_size
        grads["task_embed"][task_id] += dx[:te]
        if prev_token is not None:
            grads["token_embed"][prev_token] += dx[te:]

        dh_next = p["Wh"] @ dz


@dataclass
class TrainResult:
    policy: RecurrentPolicy
    best_tokens: Dict[str, List[int]]
    best_train_score: float
    best_holdout_score: float
    history: List[Tuple[int, float]] = field(default_factory=list)  # (generation, mean_train_score)


def train(
    task_names: List[str],
    program_length: int = 6,
    generations: int = 300,
    episodes_per_generation: int = 64,
    lr: float = 0.02,
    batch_size: int = 16,
    eval_batch_size: int = 64,
    seed: int = 0,
    hidden_size: int = 32,
    log_every: int = 20,
    verbose: bool = True,
) -> TrainResult:
    rng_py = random.Random(seed)
    np_rng = np.random.RandomState(seed)

    specs = {name: get_spec(name) for name in task_names}
    task_ids = {name: i for i, name in enumerate(task_names)}

    config = PolicyConfig(
        vocab_size=VOCAB_SIZE,
        num_tasks=len(task_names),
        hidden_size=hidden_size,
        seed=seed,
    )
    policy = RecurrentPolicy(config)
    optimizer = AdamOptimizer(policy.params, lr=lr)

    # Best program found so far, per task, along with the (freshly
    # re-evaluated) score used to decide whether a candidate improves on it.
    elite_tokens: Dict[str, List[int]] = {
        name: greedy_program(policy, task_ids[name], program_length) for name in task_names
    }
    best_score = -1.0
    best_tokens: Optional[Dict[str, List[int]]] = None
    history: List[Tuple[int, float]] = []

    for gen in range(generations):
        train_batches = {
            name: [spec.sample_state(rng_py) for _ in range(batch_size)]
            for name, spec in specs.items()
        }

        rewards = np.zeros(episodes_per_generation)
        episode_task = [task_names[i % len(task_names)] for i in range(episodes_per_generation)]
        episodes = [None] * episodes_per_generation  # (tokens, hiddens, probs_list, cut)

        # Precompute a distribution over how many trailing positions to free.
        # Most episodes are (close to) fully free rollouts, so the policy's
        # marginal token probabilities keep improving via ordinary REINFORCE
        # (this is what lets easy, short programs be discovered quickly).
        # A modest fraction of episodes instead only free a few trailing
        # positions of the current best-known program -- a local mutation
        # that can refine/extend an already-partially-correct elite without
        # having to re-discover its working prefix from scratch by chance.
        free_lengths = np.arange(1, program_length + 1)
        free_weights = FREE_LENGTH_WEIGHT_BASE ** (free_lengths - 1).astype(float)
        free_weights /= free_weights.sum()

        for i in range(episodes_per_generation):
            name = episode_task[i]
            k = int(np_rng.choice(free_lengths, p=free_weights))
            cut = program_length - k
            tokens, hiddens, probs_list, cut = rollout_from_prefix(
                policy, task_ids[name], program_length, elite_tokens[name], cut, np_rng
            )
            episodes[i] = (tokens, hiddens, probs_list, cut)
            rewards[i] = evaluate_program(tokens, specs[name], train_batches[name])

        baseline = rewards.mean()
        std = rewards.std()
        advantages = (rewards - baseline) / (std + 1e-8) if std > 0 else (rewards - baseline)

        grads = _zero_grads(policy.params)
        for i in range(episodes_per_generation):
            name = episode_task[i]
            tokens, hiddens, probs_list, cut = episodes[i]
            accumulate_grad(
                policy, task_ids[name], tokens, hiddens, probs_list, cut,
                float(advantages[i]), grads,
            )
        for key in grads:
            grads[key] /= episodes_per_generation
        optimizer.step(policy.params, grads)

        # Update each task's elite program using a fresh, larger evaluation
        # batch so comparisons this generation are apples-to-apples.
        gen_train_scores = []
        for name in task_names:
            eval_states = [specs[name].sample_state(rng_py) for _ in range(eval_batch_size)]
            candidates = [elite_tokens[name]] + [
                episodes[i][0] for i in range(episodes_per_generation) if episode_task[i] == name
            ]
            candidates.append(greedy_program(policy, task_ids[name], program_length))
            scored = [(evaluate_program(c, specs[name], eval_states), c) for c in candidates]
            best_for_task = max(scored, key=lambda sc: sc[0])
            elite_tokens[name] = best_for_task[1]
            gen_train_scores.append(best_for_task[0])
        mean_train_score = float(np.mean(gen_train_scores))

        if mean_train_score > best_score:
            best_score = mean_train_score
            best_tokens = dict(elite_tokens)

        if (gen % log_every == 0 or gen == generations - 1) and verbose:
            print(
                f"gen {gen:4d}  mean_reward={rewards.mean():.3f}  "
                f"best_eval_score={best_score:.3f}"
            )

        history.append((gen, mean_train_score))

    holdout_scores = []
    final_tokens = dict(elite_tokens)
    for name in task_names:
        tokens = final_tokens[name]
        holdout_states = [
            specs[name].sample_holdout_state(rng_py) for _ in range(eval_batch_size)
        ]
        holdout_scores.append(evaluate_program(tokens, specs[name], holdout_states))
    holdout_score = float(np.mean(holdout_scores))

    return TrainResult(
        policy=policy,
        best_tokens=best_tokens or final_tokens,
        best_train_score=best_score,
        best_holdout_score=holdout_score,
        history=history,
    )


def describe_program(token_ids: List[int]) -> str:
    return " ; ".join(VOCAB[i].name for i in token_ids)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        choices=sorted(SPECS),
        help="task(s) to synthesize programs for; repeat for multi-task training",
    )
    parser.add_argument("--program-length", type=int, default=6)
    parser.add_argument("--generations", type=int, default=300)
    parser.add_argument("--episodes-per-generation", type=int, default=64)
    parser.add_argument("--lr", type=float, default=0.02)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    tasks = args.tasks or ["increment"]

    result = train(
        task_names=tasks,
        program_length=args.program_length,
        generations=args.generations,
        episodes_per_generation=args.episodes_per_generation,
        lr=args.lr,
        batch_size=args.batch_size,
        seed=args.seed,
    )

    print()
    print(f"Best training eval score: {result.best_train_score:.3f}")
    print(f"Held-out generalization score: {result.best_holdout_score:.3f}")
    for name, tokens in result.best_tokens.items():
        print(f"[{name}] program: {describe_program(tokens)}")


if __name__ == "__main__":
    main()
