"""Task specs: one folder per variant, tasks/<task>/<variant>/
(docs/design/task-spec.md).

    prompt.txt            the exact text sent to the model
    reference.asm         hand-written solution
    check.toml            strict tier
    relaxed.check.toml    optional near-miss tier
    check.py              optional: check(racer), relaxed(racer), INFORMATIONAL
    broken/*.asm          optional: programs that must fail the strict check
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from tbd.paths import TASKS


@dataclass
class TaskSpec:
    task: str
    variant: str
    dir: Path
    module: ModuleType | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return f"{self.task}/{self.variant}"

    @property
    def prompt(self) -> str:
        """prompt.txt, byte for byte, without its final newline."""
        return (self.dir / "prompt.txt").read_text().removesuffix("\n")

    @property
    def reference(self) -> Path:
        return self.dir / "reference.asm"

    @property
    def broken(self) -> list[Path]:
        return sorted((self.dir / "broken").glob("*.asm"))

    @property
    def check_files(self) -> list[str]:
        return sorted(p.name for p in self.dir.glob("*.toml"))

    @property
    def informational(self) -> frozenset[str]:
        return frozenset(getattr(self.module, "INFORMATIONAL", ()))

    def check(self, racer) -> dict:
        if self.module and hasattr(self.module, "check"):
            return self.module.check(racer)
        return racer.run("check.toml")

    def relaxed(self, racer) -> dict | None:
        if self.module and hasattr(self.module, "relaxed"):
            return self.module.relaxed(racer)
        if (self.dir / "relaxed.check.toml").exists():
            return racer.run("relaxed.check.toml")
        return None


def _load_module(task: str, variant: str, path: Path) -> ModuleType:
    name = f"tbd_task_{task}_{variant}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(task: str, variant: str = "canonical", root: Path = TASKS) -> TaskSpec:
    d = root / task / variant
    if not (d / "prompt.txt").exists():
        raise FileNotFoundError(f"no task spec at {d} (needs prompt.txt)")
    py = d / "check.py"
    return TaskSpec(task, variant, d, _load_module(task, variant, py) if py.exists() else None)


def all_specs(root: Path = TASKS) -> list[TaskSpec]:
    """Every folder tasks/<task>/<variant>/ with a prompt.txt."""
    return [load(p.parent.parent.name, p.parent.name, root)
            for p in sorted(root.glob("*/*/prompt.txt"))]
