"""Syntax fixer: rewrite a model's program toward DASM syntax before assembling.

Most programs that local models write fail in DASM before their logic is
tested, on mechanical slips: an instruction or directive in column 0 (DASM
reads a column-0 word as a label), a label indented, another assembler's
directives, a missing `processor 6502` or include. fix() rewrites those and
says which rules fired. It changes nothing else, so a program DASM already
accepts comes out as it went in.

Whether to apply it is an experiment setting (case0 --fix-syntax on); off,
the program is assembled as written. assemble_with_fixer() says what an
attempt keeps, so "assembled as written" and "assembled after the fix" can
both be counted.

Rules (the names recorded per attempt):

  star_comment       a column-0 '*' comment (other assemblers) -> ';'
  processor_line     'CPU 6502', ':p 6502', '.setcpu "6502"' ... -> 'processor 6502'
  foreign_directive  '* = $F000' -> 'org $F000', dw/.dw/.addr -> .word,
                     db/.db/.byt -> .byte, .res/res/.dsb -> ds, .rept -> repeat,
                     .endrepeat/.endr -> repend
  accumulator_A      'asl A' (lsr, rol, ror) -> 'asl'
  indent_col0        an instruction, directive or macro in column 0 is indented
  dedent_label       an indented label ('Start:', or a bare word the program
                     refers to) goes back to column 0
  include_name       include "vcs.asm" / "VCS.H" ... -> include "vcs.h" (same for macro.h)
  add_processor      no processor line: 'processor 6502' added at the top
  add_include_vcs.h  the program uses vcs.h's names, defines none and doesn't include it
  add_include_macro.h  the program uses a macro.h macro and doesn't include it

DASM accepts a leading dot on its own directives ('.include', '.processor',
'.org' assemble when indented), so those are left alone apart from indenting.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from tbd.eval.grading import Grade, assemble_program
from tbd.paths import HEADERS

OPS = frozenset("""
adc and asl bcc bcs beq bit bmi bne bpl brk bvc bvs clc cld cli clv cmp cpx cpy dec dex dey
eor inc inx iny jmp jsr lda ldx ldy lsr nop ora pha php pla plp rol ror rti rts sbc sec sed
sei sta stx sty tax tay tsx txa txs tya
anc ane arr asr dcp isb las lax lxa rla rra sax sbx sha shs shx shy slo sre
""".split())
DIRECTIVES = frozenset("""
processor include incbin incdir seg seg.u org rorg rend align ds ds.b ds.w ds.l dc dc.b dc.w
dc.l dv dv.b dv.w hex byte word long err echo list subroutine end mac macro endm mexit ifconst
ifnconst if else endif eif repeat repend set setstr equ eqm trace
""".split())

WORD = {"dw", ".dw", "defw", "fdb", ".addr", ".dbyt"}
BYTE = {"db", ".db", "defb", "fcb", "byt", ".byt"}
RENAME = {".res": "ds", "res": "ds", ".dsb": "ds", ".rept": "repeat", "rept": "repeat",
          ".endrepeat": "repend", ".endrep": "repend", ".endr": "repend", "endr": "repend"}
PROCESSOR_SPELLINGS = {"cpu", ".cpu", "p", ":p", ".p", ".setcpu", ".p02"}


def _header_names() -> tuple[frozenset[str], frozenset[str]]:
    """(vcs.h's register names, macro.h's macro names), read from the headers."""
    vcs = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)\s+ds\s", (HEADERS / "vcs.h").read_text())
    macros = re.findall(r"(?mi)^\s+mac\s+(\w+)", (HEADERS / "macro.h").read_text())
    return frozenset(vcs), frozenset(m.upper() for m in macros)


VCS_NAMES, HEADER_MACROS = _header_names()

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ORG_STAR = re.compile(r"^\s*\*\s*=\s*(\S.*)$")   # '* = $F000' (ca65, xa)
_PROCESSOR = re.compile(r"^\s+\.?processor\b", re.I)


@dataclass
class Fix:
    text: str
    rules: list[str] = field(default_factory=list)   # rule names that fired, sorted
    # For each line of `text`, the 1-based line of the original it came from
    # (None for an added line), so DASM's errors can name the model's lines.
    line_map: list[int | None] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.rules)


def _split_comment(line: str) -> tuple[str, str]:
    """(code, comment) with the comment from the first ';' outside quotes."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == ";":
            return line[:i], line[i:]
    return line, ""


def _mnemonic(tok: str) -> str:
    """'LDA.w' -> 'lda', '.include' -> 'include': the word DASM looks up."""
    t = tok.lower()
    if t in DIRECTIVES:
        return t
    t = t.lstrip(".")
    base = t.split(".")[0]
    return base if base in OPS else t


def _is_op_or_directive(tok: str) -> bool:
    m = _mnemonic(tok)
    return m in OPS or m in DIRECTIVES


def fix(code: str) -> Fix:
    lines = code.splitlines()
    fired: set[str] = set()
    own_macros = {m.upper() for m in re.findall(r"(?mi)^\s*\.?(?:mac|macro)\s+(\w+)", code)}
    macros = HEADER_MACROS | own_macros

    def is_macro(tok: str) -> bool:
        return tok.upper() in macros

    def known(tok: str) -> bool:
        return _is_op_or_directive(tok) or is_macro(tok)

    # Words used as operands anywhere: a bare indented word among them is a label.
    referenced = set()
    for line in lines:
        toks = _split_comment(line)[0].split(None, 1)
        if line[:1].isspace() and toks and known(toks[0]) and len(toks) > 1:
            referenced.update(w.lower() for w in _WORD_RE.findall(re.sub(r"\"[^\"]*\"|'[^']*'",
                                                                         "", toks[1])))

    out: list[str] = []
    line_map: list[int | None] = []
    for n, line in enumerate(lines, 1):
        line_map.append(n)
        body, comment = _split_comment(line)
        col0 = bool(body) and not body[0].isspace()
        toks = body.split()
        if not toks:
            out.append(line)
            continue
        if m := _ORG_STAR.match(body):
            out.append(f"    org {m.group(1).strip()}" + (" " + comment if comment else ""))
            fired.add("foreign_directive")
            continue
        if col0 and toks[0].startswith("*"):
            out.append("; " + line.lstrip("*").lstrip())
            fired.add("star_comment")
            continue
        first = toks[0].lower()
        if first in PROCESSOR_SPELLINGS and len(toks) > 1 and "6502" in toks[1]:
            out.append("    processor 6502" + (" " + comment if comment else ""))
            fired.add("processor_line")
            continue

        # The token in mnemonic position: the first, unless a column-0 label precedes it.
        defines = len(toks) > 1 and toks[1].lower() in ("=", "equ", "eqm", "set")
        foreign = WORD | BYTE | set(RENAME)
        label_first = col0 and not defines and not (known(toks[0]) or first in foreign) \
            and len(toks) > 1
        idx = 1 if label_first else 0
        if not defines:
            t = toks[idx].lower()
            rep = ".word" if t in WORD else ".byte" if t in BYTE else RENAME.get(t)
            if rep:
                toks[idx] = rep
                fired.add("foreign_directive")
            if len(toks) > idx + 1 and _mnemonic(toks[idx]) in ("asl", "lsr", "rol", "ror") \
                    and toks[idx + 1].lower() == "a":
                del toks[idx + 1]
                fired.add("accumulator_A")
            if _mnemonic(toks[idx]) == "include" and len(toks) > idx + 1:
                name = toks[idx + 1].strip("\"'<>")
                for h in ("vcs.h", "macro.h"):
                    stem = h.split(".")[0]
                    if name != h and re.fullmatch(rf"{stem}(\.\w+)?", name, re.I):
                        toks[idx + 1:] = [f'"{h}"']
                        fired.add("include_name")

        indent = re.match(r"\s*", body).group()
        if col0 and not defines and known(toks[0]):
            indent = "    "
            fired.add("indent_col0")
        elif not col0 and not known(toks[0]) and not defines and _is_label(toks, referenced, known):
            indent = ""
            fired.add("dedent_label")
        new_body = indent + _rejoin(body, toks) + body[len(body.rstrip()):]
        if new_body.rstrip() == body.rstrip():
            new_body = body
        out.append(new_body + comment)

    if not any(_PROCESSOR.match(_split_comment(l)[0]) for l in out):
        out.insert(0, "    processor 6502")
        line_map.insert(0, None)
        fired.add("add_processor")
    _add_includes(out, line_map, fired, own_macros)
    if not fired:
        return Fix(code, [], line_map)
    return Fix("\n".join(out) + "\n", sorted(fired), line_map)


def _is_label(toks: list[str], referenced: set[str], known) -> bool:
    """Whether an indented line starts with a label DASM would read as a mnemonic."""
    head = toks[0]
    if head.endswith(":") and _WORD_RE.fullmatch(head[:-1]) and not known(head[:-1]):
        return True
    if not _WORD_RE.fullmatch(head):
        return False
    if len(toks) == 1:
        return head.lower() in referenced
    return known(toks[1])   # 'Start  lda #0' indented


def _rejoin(body: str, toks: list[str]) -> str:
    """The line's code (without its indent) with `toks` in place of its tokens.
    The spacing between tokens is kept unless a token was dropped."""
    parts = re.findall(r"\S+|\s+", body.strip())
    if sum(not p.isspace() for p in parts) != len(toks):
        return " ".join(toks)
    words = iter(toks)
    return "".join(p if p.isspace() else next(words) for p in parts)


def _add_includes(out: list[str], line_map: list, fired: set, own_macros: set[str]) -> None:
    """include "vcs.h" / "macro.h" after the processor line, when the program
    needs one and doesn't include it."""
    included = " ".join(_split_comment(l)[0].lower() for l in out
                        if re.match(r"\s+\.?include\b", l, re.I))
    words = set()
    defined = set()
    for line in out:
        body = _split_comment(line)[0]
        words.update(_WORD_RE.findall(re.sub(r"\"[^\"]*\"|'[^']*'", "", body)))
        if body[:1] and not body[0].isspace():
            defined.add(body.split()[0].rstrip(":"))
    at = next(i for i, l in enumerate(out) if _PROCESSOR.match(_split_comment(l)[0])) + 1
    needs = []
    if "macro.h" not in included and {w.upper() for w in words} & (HEADER_MACROS - own_macros):
        needs.append("macro.h")
    if "vcs.h" not in included and words & VCS_NAMES and not defined & VCS_NAMES:
        needs.append("vcs.h")
    # vcs.h first, then macro.h (macro.h's macros use vcs.h's names).
    for h in sorted(needs, key=lambda h: h != "vcs.h"):
        out.insert(at, f'    include "{h}"')
        line_map.insert(at, None)
        at += 1
        fired.add(f"add_include_{h}")


_ERR_LINE = re.compile(r"^prog\.asm \((\d+)\)")


def map_errors(errors: list[str], line_map: list[int | None]) -> list[str]:
    """DASM's 'prog.asm (N): ...' messages renumbered to the original program's
    lines; an added line is named as such."""
    def renumber(m: re.Match) -> str:
        n = int(m.group(1))
        orig = line_map[n - 1] if 0 < n <= len(line_map) else n
        return f"prog.asm ({orig})" if orig is not None else "prog.asm (line added by the fixer)"
    return [_ERR_LINE.sub(renumber, e) for e in errors]


ORIGINAL_SOURCE = "prog.orig.asm"
ORIGINAL_ASSEMBLE = "assemble.orig.json"


def assemble_with_fixer(code: str, workdir: Path) -> tuple[dict, Grade | None, dict]:
    """Assemble the program as written; if DASM rejects it and fix() changes
    it, assemble the fixed program instead. Returns (assemble record, an
    assemble_error Grade or None, fix record).

    A program that assembles as written is left alone. Otherwise the model's
    program is kept as prog.orig.asm with DASM's verdict in
    assemble.orig.json, and prog.asm, assemble.json and build/prog.bin are the
    fixed program's. The assemble record returned (what the model is shown on
    a retry) has the fixed program's errors, with line numbers mapped back to
    the model's lines. The fix record:

        assembled_as_written   whether DASM took the model's program
        assembled_after_fix    whether DASM took the program that was graded
        rules                  the rules that fired ([] when the program
                               assembled as written or fix() changed nothing)
        errors_as_written      DASM's errors for the model's program, when fixed
    """
    asm_rec, failed = assemble_program(code, workdir)
    record = {"assembled_as_written": asm_rec["ok"], "assembled_after_fix": asm_rec["ok"],
              "rules": []}
    if not failed:
        return asm_rec, None, record
    fixed = fix(code)
    if not fixed.changed:
        return asm_rec, failed, record
    os.replace(workdir / "prog.asm", workdir / ORIGINAL_SOURCE)
    os.replace(workdir / "assemble.json", workdir / ORIGINAL_ASSEMBLE)
    fixed_rec, fixed_failed = assemble_program(fixed.text, workdir)
    shown = {**fixed_rec, "errors": map_errors(fixed_rec["errors"], fixed.line_map),
             "warnings": map_errors(fixed_rec["warnings"], fixed.line_map)}
    record.update(assembled_after_fix=fixed_rec["ok"], rules=fixed.rules,
                  errors_as_written=asm_rec["errors"])
    if fixed_failed:
        fixed_failed = Grade(False, "assemble_error", detail="; ".join(shown["errors"][:5]))
    return shown, fixed_failed, record
