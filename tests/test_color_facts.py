"""The color facts (F014-F017) agree with the task checks.

Prompts name colors ("red", "blue"); racer-check compares raw TIA color bytes.
F017 gives the byte for each name on NTSC and PAL. If it names a byte that a
variant's check rejects, the facts would steer the model into a failing
program, so every named color in every prompt must map to an accepted byte.
"""

import re
import tomllib

import pytest

from tbd.eval import facts
from tbd.paths import TASKS

STORE = {f.fact_id: f for f in facts.load()}
NAMES_FACT, HUE_FACTS = "F017", ("F014", "F015")


def _named_bytes() -> dict[str, dict[str, int]]:
    """{"NTSC": {"red": 0x46, ...}, "PAL": {...}} from F017's text."""
    text = STORE[NAMES_FACT].text
    out = {}
    for standard, body in re.findall(r"(NTSC|PAL): ([^.]*)", text):
        out[standard] = {name: int(byte, 16)
                         for name, byte in re.findall(r"(\w+) \$([0-9A-F]{2})", body)}
    return out


NAMED = _named_bytes()


def _toml_colors(node) -> set[int]:
    """Every `color = ..` value and every color in a `runs = [[color, clocks], ..]` list."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "color" and isinstance(value, int):
                found.add(value)
            elif key == "runs":
                found |= {run[0] for run in value}
            else:
                found |= _toml_colors(value)
    elif isinstance(node, list):
        for item in node:
            found |= _toml_colors(item)
    return found


def _accepted(variant_dir) -> set[int]:
    """Color bytes the variant's checks accept: check.toml colors plus the lists
    check.py passes as `accept` (e.g. RED = [0x42, 0x44, 0x46])."""
    accepted = set()
    for toml in variant_dir.glob("*check.toml"):
        accepted |= _toml_colors(tomllib.loads(toml.read_text()))
    check_py = variant_dir / "check.py"
    if check_py.exists():
        for body in re.findall(r"^[A-Z_]+ = \[([0-9a-fx, ]+)\]", check_py.read_text(), re.M):
            accepted |= {int(b, 16) for b in body.split(",")}
    return accepted


def _standard(prompt: str) -> str:
    [standard] = re.findall(r"Target: Atari 2600, (NTSC|PAL)\.", prompt)
    return standard


def _named_in(prompt: str) -> list[str]:
    return [n for n in NAMED["NTSC"] if re.search(rf"\b{n}\b", prompt, re.I)]


VARIANTS = sorted(p.parent for p in TASKS.glob("*/*/prompt.txt"))
COLORED = [v for v in VARIANTS if _named_in((v / "prompt.txt").read_text())]


def test_names_fact_covers_both_standards():
    assert set(NAMED) == {"NTSC", "PAL"}
    assert set(NAMED["NTSC"]) == set(NAMED["PAL"])
    assert {"red", "blue", "white", "black"} <= set(NAMED["NTSC"])


def test_some_prompts_name_colors():
    """Guards the test below against matching nothing."""
    assert len(COLORED) >= 10
    assert any(_standard((v / "prompt.txt").read_text()) == "PAL" for v in COLORED)


@pytest.mark.parametrize("variant", COLORED, ids=lambda v: f"{v.parent.name}/{v.name}")
def test_named_colors_map_to_accepted_bytes(variant):
    prompt = (variant / "prompt.txt").read_text()
    standard = _standard(prompt)
    accepted = _accepted(variant)
    for name in _named_in(prompt):
        byte = NAMED[standard][name]
        assert byte in accepted, f"{name} on {standard} is ${byte:02X}; check accepts " \
            + ", ".join(f"${b:02X}" for b in sorted(accepted))
        # Where the prompt also gives the byte ("red ($46)"), the fact must agree with it.
        for given in re.findall(rf"\b{name} \(\$([0-9A-F]{{2}})\)", prompt, re.I):
            assert int(given, 16) == byte, f"prompt says {name} is ${given}, F017 ${byte:02X}"


def test_prompts_name_no_color_missing_from_the_names_fact():
    """A prompt naming a hue F017 doesn't list (say "orange") would go unchecked."""
    hue_names = set()
    for fid in HUE_FACTS:
        hue_names |= set(re.findall(r"\b[0-9A-F] ([a-z-]+(?: blue)?)(?=[,.;])", STORE[fid].text))
    others = sorted(hue_names - set(NAMED["NTSC"]) - {"grey"})
    assert "orange" in others
    for variant in VARIANTS:
        prompt = (variant / "prompt.txt").read_text()
        for name in others:
            assert not re.search(rf"\b{name}\b", prompt, re.I), f"{variant}: {name}"
