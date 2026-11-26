"""batari Basic (bB) compiler wrapper: compile a .bas program to a 2600 ROM.

bB is installed by .devcontainer/build-batari.sh into one folder, found
through tbd.paths.BATARI (TBD_BATARI). This module reproduces upstream's
build script (2600basic.native.sh in bB 1.9) step by step, in a temp folder:

  1. preprocess < prog.bas            tokenises the source
  2. 2600basic -i <bB>                 compiles it to bB.asm, and writes
                                       includes.bB and
                                       2600basic_variable_redefs.h to the cwd
  3. postprocess -i <bB>               joins bB.asm with bB's header, kernel
                                       and footer (named in includes.bB)
  4. optimize (only with optimize=True, upstream's -O flag)
  5. dasm prog.asm -I<bB>/includes -f3 with the same DASM the assembler uses

Each step runs only if the one before it succeeded; 2600basic's exit status
decides whether compilation succeeded, as in upstream's script. Not
reproduced: relocateBB (a separately distributed tool that upstream runs on
bank-switched ROMs only), and bbfilter, whose two jobs are done here: drop
DASM's "Unresolved Symbol List" (bB's includes test dozens of optional
symbols, so that list is always long), and point DASM errors back at the bB
source line they came from. bbfilter does the second only for "Unknown
Mnemonic" errors; this module does it for every error in the generated
assembly, quotes the line an error in an include names, and reports which
undefined symbols the program itself used.

Confirmed by hand with bB 1.9 and DASM 2.20.14.1:
  - 2600basic reports problems on stderr as "line <n>: <message>" (some
    other formats exist), along with a version banner and a "compilation
    complete" line. Some problems exit nonzero; others, such as "extraneous
    end statement found", don't, and the build continues.
  - Line numbers in 2600basic's messages and in the ";;line <n>;;" tags it
    writes into the assembly are lines of the original .bas file
    (preprocess keeps one output line per input line).
  - A failed DASM run on a bB program prints the unresolved symbol list
    even when the real error is elsewhere (an EQU mismatch, a branch out of
    range). When the cause is a symbol the program uses but never defines,
    such as "goto nowhere", that list is the only place it appears.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tbd.paths import BATARI
from tbd.tools.assembler import DASM_BIN

PROGRAMS = ("preprocess", "2600basic", "postprocess", "optimize")

# e.g. "prog.asm (2359): error: Branch out of range (-163 bytes)."
_DIAGNOSTIC_RE = re.compile(r"^(?P<file>.+?) \((?P<line>\d+)\):\s+(?P<kind>error|warning):\s+.*$")
# e.g. " ;;line 176;; skiplane" -- bB's tag on the assembly for one source line
_TAG_RE = re.compile(r";;line (\d+);;(.*)$")
# DASM's unresolved-symbol list entry, e.g. "0.nowhere   0000 ????   (R )"
_UNRESOLVED_RE = re.compile(r"^(\S+)\s+[0-9a-fA-F]{4} \?\?\?\?")
# e.g. "    ifconst pfres" -- a test for one of bB's optional symbols
_IFCONST_RE = re.compile(r"^\s*ifn?const\s+([\w.]+)", re.IGNORECASE)
# How far after a ";;line" tag an assembly line still counts as that bB
# line's code. bbfilter uses the same window.
_TAG_WINDOW = 8

_BANNER_RE = re.compile(r"^batari Basic v|^2600 Basic compilation complete")


@dataclass
class CompileResult:
    rom: bytes | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Everything the tools printed, in order, without DASM's unresolved
    # symbol list. What a bB user would see from upstream's script.
    output: str = ""
    # The assembly handed to DASM, when compilation got that far.
    asm: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.rom) and not self.errors


def available(bb_dir: Path = BATARI) -> bool:
    """Whether bB is installed in `bb_dir`."""
    bb_dir = Path(bb_dir)
    return all((bb_dir / p).is_file() for p in PROGRAMS) and (bb_dir / "includes").is_dir()


def compile_bas(source: str, *, optimize: bool = False, bb_dir: Path = BATARI) -> CompileResult:
    """Compile bB source text to an Atari 2600 ROM."""
    bb_dir = Path(bb_dir).resolve()
    if not available(bb_dir):
        raise RuntimeError(f"batari Basic not found in {bb_dir} (see .devcontainer/build-batari.sh)")
    if shutil.which(DASM_BIN) is None:
        raise RuntimeError(f"'{DASM_BIN}' not found on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        log: list[str] = []

        def run(prog: str, args: list[str], stdin: str) -> subprocess.CompletedProcess:
            proc = subprocess.run([str(bb_dir / prog), *args], input=stdin,
                                  capture_output=True, text=True, errors="replace", cwd=tmp)
            log.extend(line for line in proc.stderr.splitlines() if line.strip())
            return proc

        pre = run("preprocess", [], source)
        if pre.returncode != 0:
            return _failed(log, "preprocess", pre.returncode)

        bas = run("2600basic", ["-i", str(bb_dir)], pre.stdout)
        messages = [line for line in bas.stderr.splitlines()
                    if line.strip() and not _BANNER_RE.match(line)]
        if bas.returncode != 0:
            return _failed(log, "2600basic", bas.returncode, messages)
        (tmp / "bB.asm").write_text(bas.stdout)

        post = run("postprocess", ["-i", str(bb_dir)], "")
        if post.returncode != 0:
            return _failed(log, "postprocess", post.returncode, messages)
        asm = post.stdout
        if optimize:
            opt = run("optimize", [], asm)
            if opt.returncode != 0:
                return _failed(log, "optimize", opt.returncode, messages)
            asm = opt.stdout

        asm_path = tmp / "prog.asm"
        asm_path.write_text(asm)
        rom_path = tmp / "prog.bin"
        proc = subprocess.run(
            [DASM_BIN, asm_path.name, f"-I{bb_dir / 'includes'}", "-f3",
             f"-o{rom_path.name}", "-R"],
            capture_output=True, text=True, errors="replace", cwd=tmp,
        )
        dasm_lines, unresolved = _split_unresolved(proc.stdout.splitlines())
        log.extend(line for line in dasm_lines if line.strip())
        rom = rom_path.read_bytes() if rom_path.exists() else None

        errors: list[str] = []
        warnings = list(messages)
        asm_lines = asm.splitlines()
        for line in dasm_lines:
            m = _DIAGNOSTIC_RE.match(line.strip())
            if not m:
                continue
            text = line.strip()
            if m["file"] == asm_path.name:
                origin = _bas_line(asm_lines, int(m["line"]))
                if origin:
                    text += f" [bB line {origin[0]}: {origin[1]}]"
            else:
                # An include, e.g. bB's 2600basic.h or the program's own
                # 2600basic_variable_redefs.h: quote the line it names.
                quoted = _line_of(int(m["line"]), tmp / m["file"], bb_dir / "includes" / m["file"])
                if quoted:
                    text += f" [{quoted}]"
            (errors if m["kind"] == "error" else warnings).append(text)

        if not rom:
            # Symbols the program itself uses but never defines. The rest of
            # the list is bB's optional features, which the assembly tests
            # with ifconst / ifnconst and which are expected to be missing.
            optional = {m.group(1) for m in map(_IFCONST_RE.match, asm_lines) if m}
            for symbol in unresolved:
                if symbol in optional:
                    continue
                for line_no, code in _uses(asm_lines, symbol):
                    errors.append(f"unresolved symbol '{symbol}' [bB line {line_no}: {code}]")
            if not errors:
                fatal = [l.strip() for l in dasm_lines if "error" in l.lower()]
                errors.extend(fatal or [f"dasm exited {proc.returncode} without a ROM"])

        # DASM repeats an error on each pass; report it once.
        return CompileResult(rom=rom, errors=list(dict.fromkeys(errors)),
                             warnings=list(dict.fromkeys(warnings)),
                             output="\n".join(log), asm=asm)


def _failed(log: list[str], step: str, code: int, messages: list[str] = ()) -> CompileResult:
    errors = list(messages) or [f"{step} exited {code}"]
    return CompileResult(rom=None, errors=errors, output="\n".join(log))


def _split_unresolved(lines: list[str]) -> tuple[list[str], list[str]]:
    """DASM's output without its unresolved-symbol list, and the symbols in
    it. bB's local labels (".nowhere") are listed with a scope prefix
    ("0.nowhere"); that prefix is dropped."""
    kept: list[str] = []
    symbols: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("--- Unresolved Symbol List"):
            inside = True
        elif inside and line.startswith("--- ") and "Unresolved Symbol" in line:
            inside = False
        elif inside:
            m = _UNRESOLVED_RE.match(line)
            if m:
                symbols.append(re.sub(r"^\d+\.", ".", m.group(1)))
        else:
            kept.append(line)
    return kept, symbols


def _bas_line(asm_lines: list[str], number: int) -> tuple[int, str] | None:
    """The bB source line that assembly line `number` (1-based) came from,
    if it is within _TAG_WINDOW lines after that line's tag."""
    for n in range(number, max(0, number - _TAG_WINDOW), -1):
        m = _TAG_RE.search(asm_lines[n - 1]) if n <= len(asm_lines) else None
        if m:
            return int(m.group(1)), m.group(2).strip()
    return None


def _line_of(number: int, *candidates: Path) -> str | None:
    """Line `number` (1-based) of the first candidate file that exists."""
    for path in candidates:
        if path.is_file():
            lines = path.read_text(errors="replace").splitlines()
            return lines[number - 1].strip() if 0 < number <= len(lines) else None
    return None


def _uses(asm_lines: list[str], symbol: str) -> list[tuple[int, str]]:
    """bB source lines whose generated code mentions `symbol`."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(symbol)}(?![\w.])")
    found: list[tuple[int, str]] = []
    for i, line in enumerate(asm_lines, start=1):
        code = line.split(";", 1)[0]
        if pattern.search(code):
            origin = _bas_line(asm_lines, i)
            if origin and origin not in found:
                found.append(origin)
    return found
