"""A small, fixed instruction-token vocabulary and assembler.

Instead of letting the neural policy emit raw machine code (an enormous and
mostly-invalid search space), it emits a sequence of *tokens* from a small
fixed vocabulary of real 6502 instructions restricted to a handful of
zero-page "registers" ($00-$03). This keeps the action space tractable for
a tiny randomly initialized network while still compiling to exact,
unrestricted 6502 machine code that is executed by the real CPU emulator --
there is no separate "simulated" instruction semantics, so anything the
assembler produces is exactly what real 6502 hardware would do.

Most tokens correspond to a single 6502 instruction, but a few "compound"
tokens fuse a compare with an immediately following conditional branch
(e.g. "compare with $01, then skip the next instruction if >=") into a
single action. This does not change what is expressible (the plain CMP
and branch tokens are also in the vocabulary), it just gives the search
a coarser-grained, easier-to-discover action for the extremely common
compare-and-branch idiom, similar to how program-synthesis DSLs often
offer higher-level combinators over a Turing-complete primitive core.

Conditional branch tokens use a "skip forward N tokens" encoding (rather
than explicit labels) so that assembling a token sequence never requires a
symbol table: the relative branch offset is resolved purely from the
lengths of the following N tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from tbd.emulator.cpu6502 import OPCODE_TABLE

# Reverse lookup: (mnemonic, mode) -> opcode byte.
_ENCODE = {(op.mnemonic, op.mode): code for code, op in OPCODE_TABLE.items()}


class AssemblyError(Exception):
    """Raised when a token sequence cannot be assembled (e.g. branch out of range)."""


@dataclass(frozen=True)
class Token:
    name: str
    # Straight-line sub-instructions emitted before any branch, each as
    # (mnemonic, mode, operand). mode is 'imp', 'imm', or 'zp'.
    ops: Tuple[Tuple[str, str, Optional[int]], ...] = ()
    # If set, a conditional branch is emitted after `ops`, skipping forward
    # `skip` tokens (of the *enclosing* program) when taken.
    branch_mnemonic: Optional[str] = None
    skip: int = 0

    @property
    def is_branch(self) -> bool:
        return self.branch_mnemonic is not None

    def fixed_length(self) -> int:
        length = 0
        for _, mode, _ in self.ops:
            length += 1 if mode == "imp" else 2
        if self.is_branch:
            length += 2
        return length

    def encode_ops(self) -> bytes:
        out = bytearray()
        for mnemonic, mode, operand in self.ops:
            opcode = _ENCODE[(mnemonic, mode)]
            if mode == "imp":
                out += bytes([opcode])
            else:
                out += bytes([opcode, operand & 0xFF])
        return bytes(out)


def _op(name, mnemonic, mode, operand=None) -> Token:
    return Token(name=name, ops=((mnemonic, mode, operand),))


def _branch(name, mnemonic, skip) -> Token:
    return Token(name=name, branch_mnemonic=mnemonic, skip=skip)


def _cmp_branch(name, cmp_addr, branch_mnemonic, skip) -> Token:
    return Token(
        name=name,
        ops=(("CMP", "zp", cmp_addr),),
        branch_mnemonic=branch_mnemonic,
        skip=skip,
    )


# Zero-page "registers" available to synthesized programs.
ZP = [0x00, 0x01, 0x02, 0x03]

VOCAB: List[Token] = [
    _op("NOP", "NOP", "imp"),
    *[_op(f"LDA_ZP{i}", "LDA", "zp", ZP[i]) for i in range(4)],
    _op("LDA_IMM0", "LDA", "imm", 0x00),
    _op("LDA_IMM1", "LDA", "imm", 0x01),
    _op("LDA_IMM_FF", "LDA", "imm", 0xFF),
    *[_op(f"STA_ZP{i}", "STA", "zp", ZP[i]) for i in range(4)],
    *[_op(f"ADC_ZP{i}", "ADC", "zp", ZP[i]) for i in range(4)],
    _op("ADC_IMM1", "ADC", "imm", 0x01),
    *[_op(f"SBC_ZP{i}", "SBC", "zp", ZP[i]) for i in range(4)],
    _op("SBC_IMM1", "SBC", "imm", 0x01),
    *[_op(f"AND_ZP{i}", "AND", "zp", ZP[i]) for i in range(4)],
    *[_op(f"ORA_ZP{i}", "ORA", "zp", ZP[i]) for i in range(4)],
    *[_op(f"EOR_ZP{i}", "EOR", "zp", ZP[i]) for i in range(4)],
    *[_op(f"CMP_ZP{i}", "CMP", "zp", ZP[i]) for i in range(4)],
    _op("CLC", "CLC", "imp"),
    _op("SEC", "SEC", "imp"),
    _op("TAX", "TAX", "imp"),
    _op("TAY", "TAY", "imp"),
    _op("TXA", "TXA", "imp"),
    _op("TYA", "TYA", "imp"),
    _op("INX", "INX", "imp"),
    _op("INY", "INY", "imp"),
    _op("DEX", "DEX", "imp"),
    _op("DEY", "DEY", "imp"),
    # Plain conditional branches (skip forward N tokens if taken).
    *[_branch(f"BCC_SKIP{n}", "BCC", n) for n in (1, 2)],
    *[_branch(f"BCS_SKIP{n}", "BCS", n) for n in (1, 2)],
    *[_branch(f"BEQ_SKIP{n}", "BEQ", n) for n in (1, 2)],
    *[_branch(f"BNE_SKIP{n}", "BNE", n) for n in (1, 2)],
    # Fused "compare accumulator with zero-page register, then branch"
    # tokens: these make the very common compare-and-branch idiom (e.g.
    # used to implement max/min/clamp) reachable as a single action.
    *[
        _cmp_branch(f"CMPB_ZP{i}_BCS_SKIP{n}", ZP[i], "BCS", n)
        for i in range(4)
        for n in (1, 2)
    ],
    *[
        _cmp_branch(f"CMPB_ZP{i}_BCC_SKIP{n}", ZP[i], "BCC", n)
        for i in range(4)
        for n in (1, 2)
    ],
]

VOCAB_SIZE = len(VOCAB)
NAME_TO_INDEX = {tok.name: i for i, tok in enumerate(VOCAB)}

HALT_OPCODE = 0x00  # BRK, always appended to the end of every assembled program


def assemble(token_ids: List[int], org: int = 0x0600, vocab: Optional[List[Token]] = None) -> bytes:
    """Assemble a sequence of token ids into 6502 machine code.

    A trailing BRK is always appended so every synthesized program halts.
    Raises :class:`AssemblyError` if a branch token's target falls outside
    the representable signed 8-bit relative range.

    `vocab` defaults to the module-level :data:`VOCAB`; an alternate list
    of tokens may be supplied (e.g. in tests) without mutating the shared
    global vocabulary.
    """
    if vocab is None:
        vocab = VOCAB
    tokens = [vocab[i] for i in token_ids]

    lengths = [tok.fixed_length() for tok in tokens]
    lengths.append(1)  # trailing BRK
    offsets = [0] * (len(lengths) + 1)
    for i, length in enumerate(lengths):
        offsets[i + 1] = offsets[i] + length

    out = bytearray()
    for idx, tok in enumerate(tokens):
        out += tok.encode_ops()
        if not tok.is_branch:
            continue
        opcode = _ENCODE[(tok.branch_mnemonic, "rel")]
        target_index = idx + 1 + tok.skip
        target_index = min(target_index, len(tokens))  # clamp to trailing BRK
        this_instr_end_offset = org + offsets[idx + 1]
        target_offset = org + offsets[target_index]
        rel = target_offset - this_instr_end_offset
        if not (-128 <= rel <= 127):
            raise AssemblyError(
                f"branch {tok.name} at token {idx} out of range: offset {rel}"
            )
        out += bytes([opcode, rel & 0xFF])
    out += bytes([HALT_OPCODE])
    return bytes(out)
