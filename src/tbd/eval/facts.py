"""The fact store (v0): load, validate, select by condition, snapshot, render.

Facts live in facts/*.toml as [[fact]] tables with the provenance schema from
docs/design/atari-2600-synthesis-agent.md (Layer 1); see facts/README.md. The
grid's fact conditions (docs/design/experiment-grid.md) select by source_type:

    none         no facts
    contributed  official_doc + human_post
    discovered   self_discovered
    both         all

v0 retrieval is "all facts in the condition, in context": render() makes the
"Reference notes" block that case0 puts before the task prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from tbd.paths import FACTS

SOURCE_TYPES = ("official_doc", "human_post", "self_discovered")
CONDITIONS = {
    "none": (),
    "contributed": ("official_doc", "human_post"),
    "discovered": ("self_discovered",),
    "both": SOURCE_TYPES,
}
FIELDS = ("fact_id", "text", "source_type", "source_ref", "license", "confidence")
FACT_ID = re.compile(r"^F\d{3,}$")
UNVERIFIED = "unverified"


class FactError(ValueError):
    """A fact file that doesn't follow facts/README.md."""


@dataclass(frozen=True)
class Fact:
    fact_id: str
    text: str
    source_type: str
    source_ref: str
    license: str
    confidence: float

    @property
    def verified(self) -> bool:
        """Its source_ref names a source someone checked (not 'unverified: ...')."""
        return not self.source_ref.lower().startswith(UNVERIFIED)


def _check(raw: dict, where: str) -> Fact:
    missing = [f for f in FIELDS if f not in raw]
    extra = sorted(set(raw) - set(FIELDS))
    if missing or extra:
        raise FactError(f"{where}: missing {missing}, unknown {extra}")
    for f in FIELDS[:-1]:
        if not isinstance(raw[f], str) or not raw[f].strip():
            raise FactError(f"{where}: {f} must be a non-empty string")
    if not FACT_ID.match(raw["fact_id"]):
        raise FactError(f"{where}: fact_id {raw['fact_id']!r} isn't F followed by digits")
    if raw["source_type"] not in SOURCE_TYPES:
        raise FactError(f"{where}: source_type {raw['source_type']!r} not in {SOURCE_TYPES}")
    c = raw["confidence"]
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
        raise FactError(f"{where}: confidence must be a number in [0, 1]")
    if "\n" in raw["text"]:
        raise FactError(f"{where}: text must be one line")
    return Fact(**{**raw, "confidence": float(c)})


def load(root: Path = FACTS) -> list[Fact]:
    """Every fact in root/*.toml, validated, sorted by fact_id."""
    facts: list[Fact] = []
    for path in sorted(root.glob("*.toml")):
        with open(path, "rb") as f:
            data = tomllib.load(f)
        if set(data) - {"fact"}:
            raise FactError(f"{path.name}: only [[fact]] tables are allowed")
        for i, raw in enumerate(data.get("fact", [])):
            facts.append(_check(raw, f"{path.name} fact #{i + 1}"))
    seen: set[str] = set()
    for f in facts:
        if f.fact_id in seen:
            raise FactError(f"duplicate fact_id {f.fact_id}")
        seen.add(f.fact_id)
    return sorted(facts, key=lambda f: (int(f.fact_id[1:]), f.fact_id))


def select(facts: list[Fact], condition: str) -> list[Fact]:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown fact condition {condition!r}; one of {list(CONDITIONS)}")
    return [f for f in facts if f.source_type in CONDITIONS[condition]]


def snapshot(facts: list[Fact]) -> str | None:
    """sha256 over the selected facts' full records (text and provenance).

    Editing any field of a selected fact, or adding/removing one, changes it;
    file layout, comments and facts outside the selection don't. None for no facts.
    """
    if not facts:
        return None
    records = [asdict(f) for f in sorted(facts, key=lambda f: f.fact_id)]
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def render(facts: list[Fact]) -> str:
    """The "Reference notes" block, one tagged line per fact. Empty for no facts."""
    if not facts:
        return ""
    return "Reference notes:\n" + "\n".join(f"[{f.fact_id}] {f.text}" for f in facts)


def with_facts(prompt: str, facts: list[Fact]) -> str:
    """The first user message: the notes block, a blank line, then the task prompt.
    With no facts, the prompt unchanged."""
    block = render(facts)
    return f"{block}\n\n{prompt}" if block else prompt


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Print the facts a condition selects.")
    p.add_argument("condition", nargs="?", default="contributed", choices=list(CONDITIONS))
    a = p.parse_args()
    chosen = select(load(), a.condition)
    print(f"# snapshot {snapshot(chosen)}  ids {[f.fact_id for f in chosen]}")
    print(render(chosen))
