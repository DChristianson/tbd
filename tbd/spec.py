"""Executable specifications for synthesized 6502 programs.

A :class:`Spec` describes *what* a synthesized program must compute, not
*how*. It exposes:

* ``sample_state(rng)`` -- produce a random initial machine state (register
  and memory values) for training.
* ``sample_holdout_state(rng)`` -- produce initial states drawn from a
  distribution disjoint from the training distribution, used to measure
  generalization to machine states unseen during search/training.
* ``check(initial, final)`` -- an executable predicate that returns True iff
  the machine state ``final`` (produced by running a candidate program on
  ``initial`` through the CPU oracle) satisfies the specification.

This keeps the specification purely behavioural: it never inspects the
candidate program's source, only the emulator's input/output state, so
specs can be satisfied by any program, not just the "intended" one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from tbd.emulator.cpu6502 import CPU6502, Memory


@dataclass
class MachineState:
    """A snapshot of the parts of machine state a spec cares about."""

    a: int = 0
    x: int = 0
    y: int = 0
    zero_page: Dict[int, int] = field(default_factory=dict)

    def apply_to(self, cpu: CPU6502) -> None:
        cpu.a = self.a & 0xFF
        cpu.x = self.x & 0xFF
        cpu.y = self.y & 0xFF
        for addr, value in self.zero_page.items():
            cpu.mem.write(addr, value & 0xFF)

    @classmethod
    def read_from(cls, cpu: CPU6502, addrs: List[int]) -> "MachineState":
        return cls(
            a=cpu.a,
            x=cpu.x,
            y=cpu.y,
            zero_page={addr: cpu.mem.read(addr) for addr in addrs},
        )


@dataclass
class Spec:
    """An executable specification for a program-synthesis task."""

    name: str
    # Zero-page addresses that inputs/outputs live in; used to build/read
    # MachineState snapshots.
    input_addrs: List[int]
    output_addrs: List[int]
    sample_state: Callable[[random.Random], MachineState]
    check: Callable[[MachineState, MachineState], bool]
    sample_holdout_state: Callable[[random.Random], MachineState]
    max_steps: int = 200

    def evaluate(self, program: bytes, state: MachineState, org: int = 0x0600) -> bool:
        """Run ``program`` on ``state`` and check the spec.

        Returns False (rather than raising) if the program is malformed,
        never halts within ``max_steps``, or hits an illegal opcode -- all
        of these count as "does not satisfy the spec" during search.
        """
        mem = Memory()
        mem.load(org, program)
        cpu = CPU6502(mem, pc=org)
        state.apply_to(cpu)
        try:
            cpu.run(max_steps=self.max_steps)
        except Exception:
            return False
        if not cpu.halted:
            return False
        final = MachineState.read_from(cpu, self.output_addrs)
        return self.check(state, final)


def _train_byte(rng: random.Random, holdout_min: int) -> int:
    """Sample a byte from the training-only range [0, holdout_min)."""
    return rng.randint(0, holdout_min - 1)


# Byte value at/above which single-operand specs (increment, copy) hold
# out their inputs from training; below it is used for training samples.
SINGLE_OPERAND_HOLDOUT_MIN = 200

# Byte value at/above which two-operand specs (add, max) hold out *both*
# operands from training. Lower than SINGLE_OPERAND_HOLDOUT_MIN so that
# two-operand held-out states remain a reasonably sized, easy-to-sample
# range even though both operands must fall in it simultaneously.
PAIR_OPERAND_HOLDOUT_MIN = 180


def make_increment_spec() -> Spec:
    """out[$01] = (in[$00] + 1) mod 256."""

    def sample(rng: random.Random) -> MachineState:
        return MachineState(zero_page={0x00: _train_byte(rng, SINGLE_OPERAND_HOLDOUT_MIN)})

    def sample_holdout(rng: random.Random) -> MachineState:
        # Hold out the high fifth of the input byte range from training.
        return MachineState(zero_page={0x00: rng.randint(SINGLE_OPERAND_HOLDOUT_MIN, 255)})

    def check(initial: MachineState, final: MachineState) -> bool:
        expected = (initial.zero_page[0x00] + 1) & 0xFF
        return final.zero_page.get(0x01) == expected

    return Spec(
        name="increment",
        input_addrs=[0x00],
        output_addrs=[0x01],
        sample_state=sample,
        sample_holdout_state=sample_holdout,
        check=check,
    )


def make_add_spec() -> Spec:
    """out[$02] = (in[$00] + in[$01]) mod 256."""

    def sample(rng: random.Random) -> MachineState:
        return MachineState(
            zero_page={
                0x00: _train_byte(rng, PAIR_OPERAND_HOLDOUT_MIN),
                0x01: _train_byte(rng, PAIR_OPERAND_HOLDOUT_MIN),
            }
        )

    def sample_holdout(rng: random.Random) -> MachineState:
        # Hold out combinations where both operands are large (more likely
        # to exercise carry behaviour not seen during training).
        return MachineState(
            zero_page={
                0x00: rng.randint(PAIR_OPERAND_HOLDOUT_MIN, 255),
                0x01: rng.randint(PAIR_OPERAND_HOLDOUT_MIN, 255),
            }
        )

    def check(initial: MachineState, final: MachineState) -> bool:
        expected = (initial.zero_page[0x00] + initial.zero_page[0x01]) & 0xFF
        return final.zero_page.get(0x02) == expected

    return Spec(
        name="add",
        input_addrs=[0x00, 0x01],
        output_addrs=[0x02],
        sample_state=sample,
        sample_holdout_state=sample_holdout,
        check=check,
    )


def make_copy_spec() -> Spec:
    """out[$01] = in[$00]."""

    def sample(rng: random.Random) -> MachineState:
        return MachineState(zero_page={0x00: _train_byte(rng, SINGLE_OPERAND_HOLDOUT_MIN)})

    def sample_holdout(rng: random.Random) -> MachineState:
        return MachineState(zero_page={0x00: rng.randint(SINGLE_OPERAND_HOLDOUT_MIN, 255)})

    def check(initial: MachineState, final: MachineState) -> bool:
        return final.zero_page.get(0x01) == initial.zero_page[0x00]

    return Spec(
        name="copy",
        input_addrs=[0x00],
        output_addrs=[0x01],
        sample_state=sample,
        sample_holdout_state=sample_holdout,
        check=check,
    )


def make_max_spec() -> Spec:
    """out[$02] = max(in[$00], in[$01])."""

    def sample(rng: random.Random) -> MachineState:
        return MachineState(
            zero_page={
                0x00: _train_byte(rng, PAIR_OPERAND_HOLDOUT_MIN),
                0x01: _train_byte(rng, PAIR_OPERAND_HOLDOUT_MIN),
            }
        )

    def sample_holdout(rng: random.Random) -> MachineState:
        return MachineState(
            zero_page={
                0x00: rng.randint(PAIR_OPERAND_HOLDOUT_MIN, 255),
                0x01: rng.randint(PAIR_OPERAND_HOLDOUT_MIN, 255),
            }
        )

    def check(initial: MachineState, final: MachineState) -> bool:
        expected = max(initial.zero_page[0x00], initial.zero_page[0x01])
        return final.zero_page.get(0x02) == expected

    return Spec(
        name="max",
        input_addrs=[0x00, 0x01],
        output_addrs=[0x02],
        sample_state=sample,
        sample_holdout_state=sample_holdout,
        check=check,
    )


SPECS: Dict[str, Callable[[], Spec]] = {
    "increment": make_increment_spec,
    "add": make_add_spec,
    "copy": make_copy_spec,
    "max": make_max_spec,
}


def get_spec(name: str) -> Spec:
    if name not in SPECS:
        raise KeyError(f"unknown spec {name!r}; available: {sorted(SPECS)}")
    return SPECS[name]()
