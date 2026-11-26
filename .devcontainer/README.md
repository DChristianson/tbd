# tbd dev container

Adapted from snagboat's dev container: the same beads + Claude Code setup and
safety model, with tbd's toolchain in place of Node/VS Code test libraries.

## What a `.devcontainer` folder is

A `.devcontainer/` folder describes a Linux computer, in the form of a Docker
container, that your editor runs your project *inside*. When VS Code (with the
**Dev Containers** extension) opens a folder that contains one, it offers
**"Reopen in Container"**. It then builds an image from `Dockerfile`, starts a
container with the options in `devcontainer.json`, mounts this folder at
`/workspaces/tbd` (the same files, not a copy), runs the setup hooks, and moves
VS Code's backend (terminal, extensions, Claude Code) into the container.

## What's in this one

| File | Job |
|---|---|
| `Dockerfile` | Debian + git, curl, jq, ripgrep; Python 3, build-essential, CMake; **DASM** 2.20.14.1 built from a hash-pinned source archive; **batari Basic** 1.9 built into `/opt/batari-basic` by `build-batari.sh` (`TBD_BATARI`); **Rust** 1.100.0 via a hash-pinned rustup-init; **bd** pinned to a verified release; **Claude Code**; removes `sudo` |
| `devcontainer.json` | GitHub CLI (feature), security options, Claude login volume, env vars (`TBD_STELLA_CORE`, `OLLAMA_HOST`), setup hooks, extensions |
| `post-create.sh` | Runs once at creation: prints tool versions, turns bd metrics off, makes a Python venv at `~/venv`, builds the vendored `racer-check` (the grader) into `vendor/racer/target-linux/`, then, optionally, `stella-core` into `stella-core/build-linux/` via `build-stella.sh` (skipped with `TBD_SKIP_STELLA=1`; a failure only warns) |
| `build-stella.sh` | Builds `stella-core`, the optional reference emulator for cross-checks |
| `build-batari.sh` | Builds batari Basic from a hash-pinned source archive into the folder it is given; run by `Dockerfile`, and by hand on a Mac |
| `init-project.sh` | Run once by hand: `git init` (no-op here) + `bd init --skip-hooks` |
| `check-host-safety.sh` | Runs on every start: warns if git hooks have been configured |

## First use

1. Install Docker Desktop and the VS Code extension **Dev Containers**
   (`ms-vscode-remote.remote-containers`).
2. Open this folder in VS Code, then **Reopen in Container**. The first build
   takes a few minutes (DASM and `stella-core` compile).
3. In the container's terminal:
   ```bash
   claude        # log in once; the login is kept in a Docker volume
   pytest -q
   bd ready      # what's unblocked in the plan
   ```

## Mac and container side by side

The folder is shared, so anything with platform-specific binaries is kept apart:

| | Mac | Container |
|---|---|---|
| Python env | `.venv/` | `/home/vscode/venv` (outside the shared folder) |
| `stella-core` build | `stella-core/build/` | `stella-core/build-linux/`, picked up via `TBD_STELLA_CORE` |
| DASM | Homebrew | built in the image, same version |
| batari Basic | `build/batari-basic/` | `/opt/batari-basic/` in the image, via `TBD_BATARI` |
| `racer-check` | `vendor/racer/target/` | `vendor/racer/target-linux/`, via `TBD_RACER_CHECK` |

## Local models (Ollama)

Models run in Ollama **on the Mac**, where they get the GPU; inside Docker
they would be CPU-only. The container reaches it at
`OLLAMA_HOST=http://host.docker.internal:11434`. Ollama on the Mac listens on
127.0.0.1 by default, which the container can't reach; to allow it, start it
with `OLLAMA_HOST=0.0.0.0 ollama serve` (or set that in the Ollama app's
environment). That exposes Ollama to your local network too, so turn it back
off when you're not running evals.

## Beads

The plan of record lives in beads. `.beads/config.yaml`, `metadata.json` and
`README.md` are committed; the Dolt database (`.beads/embeddeddolt/`) is not
(see `.beads/.gitignore`). To share issues across machines, add a Dolt remote
(`bd dolt push`); until then the database exists only in this checkout.

`bd init` also wrote agent instruction files (`AGENTS.md`, `CLAUDE.md`,
`.codex/`, `.cursor/`) and a `bd prime` hook in `.claude/settings.json`.
`.claude/` is gitignored here, as in snagboat, because it also holds
machine-specific hooks.

Usage: `bd ready`, `bd show <id>`, `bd update <id> --claim`, `bd close <id>`.
The experiment grid behind the plan is in
[docs/design/experiment-grid.md](../docs/design/experiment-grid.md).

## Safety model

**Contained:**
- Claude Code, `bd`, and any scripts in `.beads/hooks/` (which `bd` runs
  automatically on issue create/close) all run as an unprivileged user with
  no `sudo`, no Linux capabilities, and no way to gain privileges.
- Claude's login and settings live in a Docker volume, not your Mac's
  `~/.claude`.
- bd usage metrics are off (`BD_DISABLE_METRICS=1`, `DO_NOT_TRACK=1`), and
  their hostname is pointed at 127.0.0.1 as a backup.
- Your Mac's SSH agent isn't passed in (`SSH_AUTH_SOCK` is cleared).

**Not contained:**
- **The project folder.** Anything in the container can read, change or
  delete it. Commit often and push somewhere.
- **The network.** The agent needs it (Anthropic API, package registries),
  so it's open. Code in the container could upload project files. The next
  step up is an allowlist firewall; Anthropic's reference Claude Code dev
  container (github.com/anthropics/claude-code, `.devcontainer/`) has one
  you can adapt.
- **Git hooks.** `.git/` is inside the shared folder. If anything in the
  container sets `core.hooksPath` or writes `.git/hooks/*`, **git on your
  Mac** runs those scripts the next time you commit or pull there.
  `init-project.sh` uses `--skip-hooks`, and `check-host-safety.sh` warns on
  each start. Run it on your Mac too before using git there:
  `bash .devcontainer/check-host-safety.sh`.
- **Agent hook files in the repo.** `bd init` writes `.claude/settings.json`
  (and Codex/Cursor equivalents) that run `bd prime` at session start. If you
  run Claude Code on your Mac in this folder, that command runs on your Mac.
  It's harmless when `bd` isn't installed there, but treat these files as code.

## Pushing to GitHub

VS Code normally forwards your Mac's git credentials into the container, which
would let anything in it push to **every** repo you can access. Choose one:

- **Push from your Mac** (simplest): commit in the container, then
  `git push` in a Mac terminal. Run `check-host-safety.sh` first.
- **Push from the container with a narrow token:** turn off credential
  forwarding in your VS Code *user* settings
  (`"dev.containers.gitCredentialHelperConfigLocation": "none"`), then use a
  GitHub fine-grained token limited to this one repo.

**`gh` (GitHub CLI)** is installed by a feature in `devcontainer.json`, for
opening PRs. It doesn't use the forwarded git credentials; give it the same
narrow token: a fine-grained token for this repo only, with **Contents** and
**Pull requests** set to read and write. Then:

```bash
gh auth login --with-token   # paste the token, then Ctrl-D
gh auth setup-git            # optional: git push uses the same token
```

The login is kept in `~/.config/gh`, which is not in a volume: after a
rebuild, log in again.

## Changing things

- **Add a language or tool:** uncomment a `features` entry in
  `devcontainer.json`, or add an `apt-get` package in `Dockerfile`. Then run
  **Dev Containers: Rebuild Container**. There's no `sudo` at runtime, on purpose.
- **Upgrade bd:** in `Dockerfile`, change `BD_VERSION`, download the two
  linux tarballs, run
  `gh attestation verify <tarball> --repo gastownhall/beads` on each, and
  paste in their `shasum -a 256` values.
- **Reset Claude's login:** delete the `claude-config-*` volume in Docker Desktop.
