"""tbd.tools.batari: compile batari Basic source to a 2600 ROM, or say why
not. Skipped when bB isn't installed (.devcontainer/build-batari.sh)."""

import pytest

from tbd.paths import BATARI
from tbd.tools.batari import available, compile_bas

needs_bb = pytest.mark.skipif(
    not available(BATARI),
    reason=f"batari Basic not built in {BATARI} -- see .devcontainer/build-batari.sh",
)

# bB statements are indented; a word in column 0 is a label.
GOOD = """\
 rem a red player 0 that follows the joystick
 player0x = 50 : player0y = 50
 player0:
 %00111100
 %01111110
 %11111111
 %01111110
 %00111100
end
main
 COLUP0 = $44
 if joy0left then player0x = player0x - 1
 if joy0right then player0x = player0x + 1
 drawscreen
 goto main
"""


@needs_bb
@pytest.mark.parametrize("optimize", [False, True])
def test_known_good_program_compiles_to_4k(optimize):
    result = compile_bas(GOOD, optimize=optimize)
    assert result.ok, result.errors
    assert len(result.rom) == 4096
    # The reset vector points into the cartridge ($F000-$FFFF).
    assert result.rom[0xFFD] >= 0xF0
    assert "bytes of ROM space left" in result.output


@needs_bb
def test_unknown_keyword_is_a_compile_error():
    result = compile_bas(" frobnicate 3\n")
    assert not result.ok
    assert result.rom is None
    assert result.errors == ["line 1: Error: Unknown keyword: frobnicate"]


@needs_bb
def test_statement_in_column_zero_is_reported_on_its_line():
    """Column 0 holds labels, so "rem" there makes the comment text a keyword."""
    result = compile_bas("rem hello\n drawscreen\n")
    assert not result.ok
    assert result.errors[0].startswith("line 1: Error: Unknown keyword")


@needs_bb
def test_undefined_label_names_the_bb_line_that_uses_it():
    """DASM only lists the label among bB's many optional symbols; the
    wrapper reports just the program's own, with the line that used it."""
    result = compile_bas(" x = 1\n goto nowhere\n")
    assert not result.ok
    assert result.errors == ["unresolved symbol '.nowhere' [bB line 2: goto nowhere]"]
    assert "Unresolved Symbol List" not in result.output


@needs_bb
def test_assembly_error_names_the_bb_line():
    """An error in the assembly bB wrote points back at the bB statement
    it came from, and is reported once although DASM repeats it per pass."""
    result = compile_bas(" x = 1\n asm\n foo\nend\n")
    assert not result.ok
    assert len(result.errors) == 1
    assert result.errors[0].endswith("error: Unknown Mnemonic 'foo'. [bB line 2: asm]")


def test_missing_install_is_an_error_not_a_failed_compile(tmp_path):
    with pytest.raises(RuntimeError, match="batari Basic not found"):
        compile_bas(GOOD, bb_dir=tmp_path)
