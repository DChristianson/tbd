"""tbd: neural-guided program synthesis for the 6502.

This package implements a small system that trains a randomly initialized
neural network to synthesize 6502 assembly programs satisfying executable
specifications. An exact 6502 CPU emulator is used as an oracle to execute
candidate programs, and the resulting behaviour is checked against a spec
(a Python predicate over machine states) to compute a training reward.
"""

__all__ = []
