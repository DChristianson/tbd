# Vendored: racer-check

From [racer](https://github.com/DChristianson/racer) (private), commit
`9cc7b1d8550427d224f52958ca83abaadacee4fb` (`main`, 2026-11-17). MIT
licensed, same author as tbd; see `LICENSE`.

**What's here:** the three crates `racer-check` needs, each crate's
`Cargo.toml` and `src/` only, plus racer's `Cargo.lock`, `rustfmt.toml`,
`.cargo/config.toml` and `docs/checker.md` (as `checker.md`):

| Crate | What it is |
|---|---|
| `crates/sim` | cycle-accurate 6502 + TIA + RIOT simulator |
| `crates/checkfile` | the check-file format (TOML) |
| `crates/checker` | runs a ROM in the simulator and checks a check file's properties; binary `racer-check` |

**Left out:** the compiler and superoptimizer crates; every crate's `tests/`
and `examples/` (they need racer's reference ROMs and ~600 MB of CPU test
data); the `racer-crosscheck` binary (needs the other emulators). The
`crosscheck` module stays in the checker library, unchanged.

**Unmodified.** tbd's copy used to carry four local changes: the
undocumented `LXA #imm` opcode, the property kinds `signal_span`,
`frame_length_range` and `picture_block`, and `racer-check --frames`. Racer
took all four in its PR #17, so they are now upstream and this copy matches
racer at the commit above (beads tbd-818.15, tbd-818.19). Their documentation
is in [checker.md](checker.md).

The racer commit above also includes its milestone 10 work since the
previously vendored commit (`b54543d`): paddles (`[[input]]` knob positions, the
`paddle` property kind, `INPT0`-`INPT3`), missile 1, and the playfield's
collisions. A random power-on state now also randomizes missile 1's
position, drawn before player 1's, so the random values given to player 1,
the ball and the audio counters differ from `b54543d`'s for the same seed.

**Checked (2026-11-18):** the `racer-check` built from this commit and the
one built from the previous, modified copy give byte-identical `--json` and
`--frames` output for every check file in `tasks/` against every task's
reference program and each of its `broken/` programs (33 runs in each mode).

The workspace `Cargo.toml` here is tbd's own, listing only these three
crates. To update: copy the same files from a newer racer commit, update the
commit here and in `Cargo.toml`, and rebuild. `cargo build` prunes the
compiler's and superoptimizer's entries from the copied `Cargo.lock`.

**Build:** `cargo build --release --manifest-path vendor/racer/Cargo.toml`
gives `vendor/racer/target/release/racer-check`. Usage and the check-file
format: [checker.md](checker.md), racer's `docs/checker.md` at the same
commit. In the dev container it is built by `post-create.sh` into
`target-linux/` and found through `TBD_RACER_CHECK`.
