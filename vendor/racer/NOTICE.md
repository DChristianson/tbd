# Vendored: racer-check

From [racer](https://github.com/DChristianson/racer) (private), commit
`b54543df61765b81d5e3fb5acf0e5eca3d2fad90` (`main`, 2026-11-04). MIT
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

**Modified, three changes (2026-11-15).** Upstream racer has neither yet
(beads tbd-818.15). Otherwise unmodified.

1. `crates/sim/src/cpu.rs` adds the undocumented `LXA #imm` (`$AB`),
   `A = X = (A | $EE) & imm` as Stella does it, because model-written
   programs may use undocumented opcodes and the standard `CLEAN_START`
   macro uses `lxa #0` (beads tbd-818.11).
2. A new property kind, `signal_span` (`crates/checkfile/src/property.rs`,
   `write.rs`, `crates/checker/src/check.rs`, documented in `checker.md`):
   `signal_on_lines` with a tolerance on how long the signal lasts, so a
   VSYNC that starts a few cycles into line 0 can pass.
3. Two more property kinds for a relaxed tier of checks, in the same files:
   `frame_length_range` (`min_lines`..`max_lines`) and `picture_block` (a
   block of `lines` lines of one color starting within `start_window`,
   blank elsewhere).

**Checked (2026-11-15):** at commit `59edc95`, the vendored `racer-check` and
unmodified upstream `racer-check` (racer `b54543d`) give byte-identical
`--json` reports on all 40 of racer's own reference check files, all passing
(racer's `reference/` built with `DASM_INCLUDE` pointed at
tbd's `vcs.h`/`macro.h`, now `vendor/atari2600`). The changes add capability without
changing an existing result. The
workspace `Cargo.toml` here is new, listing only these three crates. To update: copy the same files from a newer racer commit,
update the commit above, and rebuild.

**Build:** `cargo build --release --manifest-path vendor/racer/Cargo.toml`
gives `vendor/racer/target/release/racer-check`. Usage and the check-file
format: [checker.md](checker.md), racer's `docs/checker.md` at the same
commit. In the dev container it is built by `post-create.sh` into
`target-linux/` and found through `TBD_RACER_CHECK`.
