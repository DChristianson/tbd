# tbd
Neural-guided constraint-based program synthesis

A small neural program-synthesis system that trains a randomly initialized
neural network to synthesize 6502 assembly programs that satisfy executable
specifications, using an exact 6502 emulator as an external oracle. The
policy is trained with reinforcement learning (no ground-truth programs are
given), and its ability to generalize is measured on machine states and
program combinations that are held out of training.

## How it fits together

* **`tbd/emulator/`** -- a from-scratch, cycle-accurate-enough emulator of
  the *official* (documented) 6502 instruction set (`CPU6502`, flat 64KB
  `Memory`). This is the ground-truth oracle: every candidate program is
  actually executed on it, byte for byte, rather than approximated.
* **`tbd/spec.py`** -- executable specifications. Each `Spec` (e.g.
  `increment`, `add`, `copy`, `max`) samples machine states, runs a candidate
  program against the emulator, and checks the resulting machine state
  against a Python predicate. Crucially, each spec exposes *disjoint*
  training and held-out state distributions (`sample_state` vs.
  `sample_holdout_state`), so a program's held-out score measures real
  generalization, not memorization of the training states.
* **`tbd/assembler.py`** -- a small, fixed vocabulary of ~70 tokens (loads,
  stores, arithmetic, logic, and "compare + branch" instructions operating
  over a few zero-page "registers") and an `assemble()` function that
  compiles a sequence of token ids into real, executable 6502 machine code
  (with two-pass branch-offset resolution and a trailing `BRK` so every
  program halts). Restricting the action space to a small vocabulary (rather
  than raw opcode bytes/operands) keeps the search tractable for a tiny
  network.
* **`tbd/model.py`** -- `RecurrentPolicy`, a minimal Elman RNN implemented
  with plain numpy (no autodiff framework), conditioned on a task id and
  autoregressively emitting one token at a time.
* **`tbd/train.py`** -- the training loop. Because "does the emulated
  program satisfy the spec?" is a discrete, non-differentiable reward, the
  policy is trained with REINFORCE (score-function gradient estimator),
  with gradients derived by hand and backpropagated through the RNN. To
  make rare-but-necessary multi-token combinations (e.g. "compare, then
  branch, then store") discoverable, each episode resumes from the current
  best-known program per task up to a random cut point and samples freely
  only from that point onward -- a neural-guided local-search/mutation
  step layered on top of ordinary full-sequence REINFORCE rollouts.

## Usage

```bash
pip install -r requirements.txt
python -m tbd.train --task increment
python -m tbd.train --task add
python -m tbd.train --task copy
python -m tbd.train --task max --generations 800
```

Each run prints periodic training-reward/best-score logs and finishes by
reporting the best training-eval score, the held-out generalization score,
and the synthesized program (as a readable token sequence).

## Tasks and current results

| Task        | Description                                   | Train / holdout score |
|-------------|------------------------------------------------|------------------------|
| `increment` | `mem[0x01] = (mem[0x00] + 1) mod 256`           | ~1.00 / ~1.00          |
| `add`       | `mem[0x02] = (mem[0x00] + mem[0x01]) mod 256`   | ~1.00 / ~1.00          |
| `copy`      | `mem[0x01] = mem[0x00]`                         | ~1.00 / ~1.00          |
| `max`       | `mem[0x02] = max(mem[0x00], mem[0x01])`         | ~0.70 / ~0.55          |

`increment`, `add`, and `copy` reliably converge to programs that generalize
perfectly to held-out machine states. `max` requires an exact
compare-then-branch idiom that is combinatorially rare to stumble upon via
sampling; the current setup consistently improves well past the "always
copy one operand" baseline (~50%) but does not yet reliably discover an
exactly correct, fully generalizing solution within a modest compute
budget. It's left in as an honest example of a harder synthesis target and
a natural place to try smarter search (e.g. more targeted elitism, larger
program-length budgets, or curriculum from `copy`/`add`).

## Testing

```bash
python -m unittest discover -s tbd/tests
```

Covers the emulator (opcode semantics), the executable-spec framework
(including train/holdout disjointness), the assembler (token vocabulary and
machine-code generation), the policy network, and end-to-end training
(verifying that programs both fit the training distribution and generalize
to held-out states).

