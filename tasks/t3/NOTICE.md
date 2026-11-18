# Task 3 references

Task 3 asks for a picture whose color changes across the screen and down it.
`canonical/` is a checkerboard grid written for tbd. The other variants are
racer's playfield images, from
[racer](https://github.com/DChristianson/racer) (private, same author),
commit `9cc7b1d8550427d224f52958ca83abaadacee4fb`:

| Variant | From |
|---|---|
| `canonical/reference.asm` | written for tbd |
| `gate/reference.asm` | `reference/playfield-3a.asm` |
| `tiles/reference.asm` | `reference/playfield-3b.asm` |
| `word/reference.asm` | `reference/playfield-3c.asm` |
| `two-words/reference.asm` | `reference/playfield-3d.asm`, as assembled with no `-DK` (K = 0) |
| `flag/reference.asm` | `reference/playfield-3e.asm` |
| `stripes/reference.asm` | `reference/playfield-3e-narrow.asm` |

The imported code is unchanged. The first paragraph of each header was
rewritten, and lines that named racer's check file now name `check.toml`;
the rest of the comments are racer's. They refer to racer's files
(`frame-0a` is its stable-frame reference) and to its design sketches,
milestones and target-model facts (`TIA-PF-5` and the like), which are not
in tbd.

Each check file starts from racer's `.check.toml` for the reference
(`playfield-3d-k0.check.toml` for `two-words`). It uses Task 1's run
settings (100 frames, from a zeroed power-on state and 100 random ones) and
Task 2's VSYNC properties. The picture is graded. Racer's properties for
when the reference writes each register are kept but reported, not graded,
so a program that draws the same picture another way still passes.

Racer's references were written in Claude Code sessions with racer's checker
available and a person steering. `canonical/reference.asm` was written by a
Claude Code agent with the vendored racer-check available, and a person
reviews it with the pull request that adds it. All of them show the task can
be passed; none of them is eval data.

Racer has more playfield references than these. `playfield-3d.asm` is also
assembled for K = 1, 2, 4 and 8 (each a slightly different picture), and
`playfield-3d-write.asm` draws those pictures without score mode. They were
left out because each would be another variant with a near-identical prompt.
