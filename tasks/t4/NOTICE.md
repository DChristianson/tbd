# Task 4 references

All of these come from [racer](https://github.com/DChristianson/racer)
(private, same author), commit `9cc7b1d8550427d224f52958ca83abaadacee4fb`.
All but `four-objects/` are racer's object series, in which each step adds
one thing to the one before:

| Variant | From | What it draws |
|---|---|---|
| `canonical/reference.asm` | `reference/objects-2a.asm` | one player, fixed |
| `vertical/reference.asm` | `reference/objects-2b.asm` | the player moving down |
| `diagonal/reference.asm` | `reference/objects-2c.asm` | the player moving down and across |
| `missile/reference.asm` | `reference/objects-2d.asm` | a player and a missile, moving |
| `ball/reference.asm` | `reference/objects-2e.asm` | the ball, bouncing off the edges |
| `paddles/reference.asm` | `reference/objects-2f.asm` | two paddles that follow a bouncing ball |
| `zones/reference.asm` | `reference/objects-2g.asm` | three objects that never share a line |
| `flicker/reference.asm` | `reference/objects-2h.asm` | three objects that overlap, shown with flicker |
| `two-colors-apart/reference.asm` | `reference/objects-2i-zones.asm` | a player and a missile in different colors, in separate bands |
| `two-colors-flicker/reference.asm` | `reference/objects-2i-flicker.asm` | the same, free to meet, each shown on alternate frames |
| `two-colors-sides/reference.asm` | `reference/objects-2j.asm` | the same, on the same lines but on opposite sides |
| `four-objects/reference.asm` | `reference/kernel-4obj-claude.asm` | two players and two missiles, all moving |

The code is unchanged. Only the first paragraph of each header was
rewritten; the rest of the comments are racer's. They refer to racer's files
by racer's names (the table above maps them, and `frame-0a` is its
stable-frame reference) and to its design sketches, milestones and
target-model facts (`TIA-OBJ-3` and the like), which are not in tbd.

Each check file starts from racer's `.check.toml` for the reference, with
Task 2's VSYNC properties. `canonical/` uses Task 1's run settings (100
frames, from a zeroed power-on state and 100 random ones). The moving
variants keep racer's frame counts, which are long enough for every motion
to wrap or turn back, and racer's number of power-on states, to keep the
test suite's time down.

Each object's position on every frame is graded. Racer's properties for when
the reference writes each register are kept but reported, not graded. tbd
adds one property, `background`, which racer's files do not have: every
clock that no object covers must be black. Without it a program could draw
stray pixels and still pass. It skips the first 8 visible clocks of each
line, which an HMOVE blanks; the comment above it in each `check.toml` says
why.

All of them were written in Claude Code sessions with racer's checker
available and a person steering. They show the task can be passed; they are
not eval data.

`four-objects/` is the one variant outside the object series. Racer wrote
`kernel-4obj-claude.asm` to measure whether two players and two missiles, all
moving, fit in a kernel that changes every object on every line. It was
imported and its check file adapted in the same way as the others.
