# Vendored files — not covered by this project's LICENSE

`vcs.h` and `macro.h` in this directory are copied **verbatim, unmodified**
from DASM's own Atari 2600 support package:

- **Source:** DASM (<https://dasm-assembler.github.io/>,
  <https://github.com/dasm-assembler/dasm>), `machines/atari2600/`
- **Version copied from:** the local Homebrew install, `dasm` 2.20.14.1
  (file headers: `vcs.h` v1.06, `macro.h` v1.09)
- **License:** GPL-2.0-or-later — the same license as the DASM assembler
  itself. Neither file states a separate, more permissive license, despite
  being distributed explicitly to be `include`d wholesale into every user
  ROM. This project's root `LICENSE` (MIT) does **not** apply to these two
  files; they remain GPL-2.0-or-later as received.
- **Why vendoring is fine:** GPL §1 permits verbatim copying/redistribution
  of unmodified source. These files are used only as assembler input
  (included by `tasks/t1/canonical/reference.asm` and by the programs the eval
  grades), not linked into any shipped artifact of this project.

Do not edit these two files in place — if DASM's upstream version changes,
re-copy from the installed package rather than hand-patching.

## `state.h`

The original fixture draft `include`d a `state.h`. DASM does not ship one for
the Atari 2600 — checked both the local install and the upstream repo. The
macros the fixture actually needs (`CLEAN_START`, `VERTICAL_SYNC`) already
live in `macro.h`, so that include was simply dropped rather than vendored.
