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

**Not modified.** The workspace `Cargo.toml` here is new, listing only these
three crates. To update: copy the same files from a newer racer commit,
update the commit above, and rebuild.

**Build:** `cargo build --release --manifest-path vendor/racer/Cargo.toml`
gives `vendor/racer/target/release/racer-check`. Usage and the check-file
format: [checker.md](checker.md), racer's `docs/checker.md` at the same
commit. In the dev container it is built by `post-create.sh` into
`target-linux/` and found through `TBD_RACER_CHECK`.
