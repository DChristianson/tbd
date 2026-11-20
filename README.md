# tbd

A coding harness that writes Atari 2600 programs from plain-language prompts, and the
evaluation harness that measures it. The harness asks a model for a program,
assembles it with DASM, runs it in a cycle-accurate simulator, and checks the
picture and timing against a spec.

The first task, **Case 0**, is the prompt *"Create a kernel with stable
framerate that sets the screen background to red."* It runs end to end today
against local models (Ollama) and Claude (Anthropic API).

Design: [docs/design/atari-2600-synthesis-agent.md](docs/design/atari-2600-synthesis-agent.md).
Experiment plan: [docs/design/experiment-grid.md](docs/design/experiment-grid.md).
Work items are tracked in beads (`bd ready`).

## What's in the repo

| Path | What it is | License |
|---|---|---|
| `src/tbd/tools/` | `assembler` (wraps DASM) and `emulator` (drives `stella-core`; used only by tests) | MIT |
| `src/tbd/eval/` | `grading` (extract, assemble, racer-check, categorize), `models` / `claude_client` (Ollama / Claude), `tasks` / `checks` (task specs and running their checks), `case0` (the eval CLI), `regrade` | MIT |
| `vendor/racer/` | `racer-check`: the grader. Simulator and property checker, from racer, unmodified (see its `NOTICE.md`) | MIT |
| `stella-core/` | **Optional.** Headless Stella 7.0c core: a reference emulator for cross-checking the grader, not used by the eval | GPL-2.0-or-later |
| `tasks/<task>/<variant>/` | Task specs: prompt, reference program, check files, optional `check.py` ([docs/design/task-spec.md](docs/design/task-spec.md)). `t1/canonical` is Case 0 / Task 1 | MIT |
| `vendor/atari2600/` | DASM's `vcs.h` / `macro.h`, the standard headers every program includes | GPL-2.0-or-later |

## Building

### In the dev container (recommended)

Open the folder in VS Code and choose **Reopen in Container**. The image
installs DASM 2.20.14.1, Rust 1.100.0, Python and CMake, and `post-create.sh`
then builds:

- a Python venv at `~/venv` with `tbd` installed (`.[test]`)
- `racer-check` into `vendor/racer/target-linux/` (found through `TBD_RACER_CHECK`)
- optionally, `stella-core` into `stella-core/build-linux/` (found through
  `TBD_STELLA_CORE`). Set `TBD_SKIP_STELLA=1` to skip it; a failed Stella
  build only prints a warning. Build it later with
  `bash .devcontainer/build-stella.sh`.

For the Claude API, also install the eval extra: `pip install -e '.[eval]'`.
Details, including the safety setup: [.devcontainer/README.md](.devcontainer/README.md).

### On a Mac, without the container

Needs Homebrew, Python 3.10+ and Rust (`rustup`).

```sh
brew install dasm

python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test,eval]'

cargo build --release --manifest-path vendor/racer/Cargo.toml
```

Optional, for the Stella cross-checks (needs CMake and a C++20 compiler, the
Xcode command-line tools):

```sh
brew install cmake
cmake -S stella-core -B stella-core/build -DCMAKE_BUILD_TYPE=Debug
cmake --build stella-core/build -j8
```

The Mac and the container build into different folders (`build/` and
`target/` on the Mac, `build-linux/` and `target-linux/` in the container),
so both can share one checkout.

### Check the build

```sh
pytest -q
```

All tests should pass. Tests that need `stella-core` or `racer-check` are
skipped, with a message, if those aren't built; without Stella, that's the
Stella cross-checks only.

CI runs the same suite on every pull request and push to `main`
(`.github/workflows/tests.yml`), with DASM and racer-check built and without
Stella.

## Running Case 0

```sh
python -m tbd.eval.case0 --model llama3.1 --samples 1 --experiment my-first-run
```

`--task` and `--variant` pick the task (default `t1` / `canonical`, from
`tasks/t1/canonical/`). Each sample sends the variant's `prompt.txt` as a single user message (no system
prompt, no other context unless `--facts` adds reference notes), pulls the program out of the reply, assembles it,
and checks it with `racer-check`.

### Models

**Local, through Ollama.** Ollama runs on the Mac, where it gets the GPU. The
container reaches it at `OLLAMA_HOST` (`http://host.docker.internal:11434`);
Ollama has to listen beyond localhost for that (`OLLAMA_HOST=0.0.0.0 ollama
serve`). Models over about 10 GB swap badly on a 16 GB Mac.

```sh
python -m tbd.eval.case0 --model qwen3.5 --think off --max-tokens 8192 --samples 2 --experiment qwen-try
```

**Claude, through the Anthropic API.** Set `TBD_ANTHROPIC_API_KEY`, not
`ANTHROPIC_API_KEY`: Claude Code in the container also reads the latter and
would switch to your key. To pass it into the container, export it on the Mac
and reopen the container.

```sh
python -m tbd.eval.case0 --provider anthropic --model claude-opus-5-5 --samples 5 --experiment claude-try
```

Claude takes no temperature or seed; thinking is on, with depth set by
`--effort` (default `high`). Each attempt records its cost.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--samples N` | 10 | samples (independent requests) |
| `--facts` | `none` | `contributed`, `discovered` or `both`: put that condition's facts from `facts/` before the prompt as a "Reference notes" block (see facts/README.md) |
| `--mode` | `one-shot` | or `tool-feedback`: on failure, the assembler or checker message goes back to the model, up to `--retries` (3) more attempts |
| `--max-tokens` | 4096 (Ollama), 64000 (Claude) | output cap, thinking included |
| `--timeout` | 600 | wall-clock seconds per request; output so far is kept |
| `--temperature`, `--seed` | 0.7, 42 | Ollama only; sample *n* uses seed + *n* |
| `--think on/off` | model default | Ollama reasoning models |
| `--effort` | `high` | Claude only |
| `--experiment` | timestamped | output folder name; must be new |

### Output

Runs are never overwritten or deleted. An existing experiment name is an
error.

```
experiments/<experiment>/manifest.json            git commit the run started from
experiments/<experiment>/jobs/<model>-t1-canonical-none-<mode>/
    config.json          every setting, the prompt, the model's digest or ID
    metrics.json         status (running/completed/interrupted/failed), pass rate, near misses
    attempts.jsonl       one record per attempt
    sample-NN/attempt-N/
        request.json     exact request sent
        stream.jsonl     every streamed chunk as it arrived
        response.json    the raw reply; response.md / thinking.md: its text
        prog.asm         the program extracted from the reply
        assemble.json    DASM's result
        check.*          the strict check file run and racer-check's JSON
                         (check-<property>-0xNN.*: re-run at an accepted color)
        relaxed.check.*  the relaxed tier, when the strict one failed
        attempt.json     the attempt record and its grade
```

### Grading

Each attempt gets one category: `pass`, `timing` or `output_mismatch` (ran
but failed a property), `assemble_error`, `no_code`, `truncated`, `timeout`,
`refusal`, `crash`, or `unsupported_opcode`.

- **Strict check**
  ([check.toml](tasks/t1/canonical/check.toml)):
  262-line frames; VSYNC starting on line 0 for 2 to 4 lines; nothing shown
  outside lines 40-231; every visible clock on those lines red. Red means
  `$42` to `$46`. A VSYNC of exactly 3 lines from the start of line 0 is also
  checked, but only recorded.
- **Relaxed tier**
  ([relaxed.check.toml](tasks/t1/canonical/relaxed.check.toml)):
  run when the strict check fails. 260-264 lines, and the 192-line red picture
  may start on lines 38-42. Failing strict and passing this is a **near
  miss**.

Every check runs from a zeroed power-on state and from 100 random ones.

### Re-grading saved attempts

When the grader changes, saved replies can be graded again without asking
the model again:

```sh
python -m tbd.eval.regrade experiments/<experiment>/jobs/<job>/sample-00/attempt-1
```

The new grade goes in a new `regrade-<time>/` folder inside the attempt,
with the grader's git commit. The original record isn't changed.

## License

MIT (see [LICENSE](LICENSE)), except `stella-core/` (GPL-2.0-or-later) and
the vendored `vcs.h` / `macro.h` (GPL-2.0-or-later); see the `NOTICE.md`
files in those folders.
