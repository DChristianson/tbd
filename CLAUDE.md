# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:1105d646 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Build & Test

See [README.md](README.md) for building DASM, racer-check and (optionally)
stella-core, in the dev container or on a Mac.

```bash
pip install -e '.[test]'      # '.[test,eval]' to run evals against Claude
python -m pytest              # racer-check / stella-core tests skip if not built
```

## Architecture Overview

- `src/tbd/tools/` — `assembler` (DASM) and `emulator` (stella-core, tests only).
- `src/tbd/eval/grading.py` — task-agnostic: extract code, assemble, run a
  racer-check check file, categorize. `models.py` / `claude_client.py` — the
  model clients. `tasks.py` / `checks.py` — task specs and running their
  checks. `facts.py` — the fact store (`facts/*.toml`, see facts/README.md). `case0.py` — the eval CLI (`--task`, `--variant`). `regrade.py` — re-grade
  saved attempts. `report.py` — grid report over saved experiments. `experiment.py` —
  expand a YAML definition (`experiments.d/*.yaml`) into case0 jobs, run them,
  resume.
- `tasks/<task>/<variant>/` — prompt, reference program, check files, optional
  `check.py`; see docs/design/task-spec.md.
- `vendor/` — racer-check (the grader) and DASM's headers. `stella-core/` —
  optional reference emulator (GPL).
- `experiments/` — eval output, gitignored. Runs are never deleted or overwritten.

## Conventions & Patterns

- New work goes on a branch; PRs target `main`.
- Task-specific code and data stay out of `grading.py` / `models.py`; a new
  task or variant adds a `tasks/<task>/<variant>/` folder rather than code.
- Paths to the checkout and built tools come from `tbd.paths`.

## Writing for Readers

Applies to PR descriptions, plans, design docs, bead descriptions and commit
messages. The reader has not seen the session that produced the work, so write
for them, not for yourself.

- **Start with why.** Say what problem the work solves and what the reader
  needs to decide, before describing what was built.
- **Don't refer to things the reader hasn't seen.** That includes
  alternatives you considered but never introduced ("instead of a suffixed
  name"), internal names or formulas ("seed + 0"), and shorthand for events
  ("#26 had it open"). Explain each one, or leave it out.
- **Give one reason per sentence.** If a sentence joins two arguments with
  "and", split it.
- **Write full sentences**, with articles and connecting words. Avoid
  note-style fragments and chains of clauses.
- **Argue the decisions and link to the inventory.** Spend the words on the
  choices a reviewer should check and why each one was made. Don't repeat lists
  that the code, tests or docs already hold (config keys, every test case,
  full command output); point to them.
- **Check that every reason still holds** when you write it down. For example,
  a reason that depended on another PR being open stops holding once that PR
  merges.
- **Before posting, have a fresh reader check it.** Give an agent with no
  session context only the text (and the diff, for a PR). Ask it to quote
  every sentence it can't follow and every claim the code contradicts.
