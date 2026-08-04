# amao — Autonomous Multi-Agent Orchestration Engine

An autonomous agent orchestration framework that executes software projects end-to-end. A Planner
agent (OpenAI) breaks a goal into milestones, a Local Executor agent turns each milestone into a
sandboxed unified diff, a Reviewer agent (Anthropic) reviews the resulting `git diff`, and an
Orchestrator loop drives the whole thing with SQLite-backed state, rate-limit resilience, and
human-in-the-loop alerting.

---

## Project Overview

Instead of manually copying prompts and diffs between web UIs and local CLI tools, this engine
acts as a central coordinator: specialized AI agents talk to each other, manage a local git
repository, propose code edits as diffs, review them, and notify a human only when genuinely
stuck.

## Key Features

* **Sandboxed diff-based execution.** The Local Executor never writes files directly — it proposes
  a unified diff, which `GitHelper.apply_diff` validates (no absolute paths, no `..` traversal, no
  symlinks, no binary content, size-capped) and applies via `git apply --check` before committing
  to it. This is the single choke point every code change passes through.
* **Persistent SQLite checkpoints.** Every milestone, audit log entry, and error state is recorded
  in `orchestrator_state.db` inside the target project directory. A crashed or restarted run
  resumes exactly where it left off.
* **Exponential backoff with a ceiling.** Rate-limit/quota errors (detected via the SDK's
  `status_code`, with a string-matching fallback) are retried with jittered exponential backoff,
  capped at `MAX_SLEEP_SECONDS`.
* **Loop safety guard.** After `MAX_REVIEW_ATTEMPTS` failed reviews on the same milestone, the
  engine halts that milestone and notifies a human instead of burning tokens in an infinite
  fix/review cycle.
* **Fail-closed error handling.** Content-level failures (a bad diff, an unparseable review) count
  as a rejected attempt and feed back into the next planning step. Everything else — config
  problems, git/database errors, exhausted rate-limit retries — halts the run and pages a human.
  See `src/amao/exceptions.py`.
* **Multi-channel notifications.** Posts to any HTTPS Slack/Discord-style webhook.

## Repository Structure

```
.
├── src/amao/
│   ├── cli.py             # `amao` console-script entry point
│   ├── config.py          # Env-driven settings + fail-fast validate()
│   ├── exceptions.py       # AmaoError hierarchy (recoverable vs. halting)
│   ├── models.py           # Milestone / ReviewResult / ExecutionResult + MilestoneStatus
│   ├── state_manager.py    # SQLite persistence for milestones + audit log
│   ├── rate_limiter.py     # Retry/backoff decorator
│   ├── notifier.py         # HTTPS webhook alert dispatcher
│   ├── agents.py           # PlannerAgent, LocalExecutorAgent, ReviewerAgent
│   ├── git_helper.py       # Git subprocess wrapper + sandboxed apply_diff
│   └── orchestrator.py     # Main pipeline loop / state machine
├── examples/demo_run.py     # Self-contained demo (CLI Task Manager app)
├── tests/                   # pytest suite (mocked SDKs, real git in tmp dirs)
├── pyproject.toml
├── Dockerfile / .dockerignore
└── .github/workflows/ci.yml
```

## Getting Started

### Prerequisites

* Python 3.10+
* Git, available on `PATH` (required at runtime, not just for development)

### Installation

```bash
pip install -e ".[dev]"
```

### Configuration

Copy `.env.example` to `.env` and fill in real values (never commit `.env`), or export the
variables directly:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export NOTIFIER_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"  # optional, HTTPS only
```

`Config.validate()` is called on every `Orchestrator` construction (CLI, demo, or programmatic
use) and fails fast with a clear error if a required key is missing.

Other tunables (all optional, see `.env.example` for defaults): `MAX_REVIEW_ATTEMPTS`,
`MAX_MILESTONES`, `MAX_DIFF_CHARS`, `REQUEST_TIMEOUT_SECONDS`, `PLANNER_MODEL`, `REVIEWER_MODEL`.

### Running

Via the CLI:

```bash
amao run --dir ./my_project --goal "Build a simple Python CLI Task Manager app"
```

Or programmatically:

```python
from amao.orchestrator import Orchestrator

Orchestrator(project_dir="./my_project", project_goal="Build a ...").run()
```

Re-running `amao run` on the same `--dir` resumes from the existing `orchestrator_state.db` instead
of re-planning.

### Running the Demo

```bash
python examples/demo_run.py
```

Builds a sample CLI Task Manager app end-to-end in `./demo_task_manager_app`.

### Docker

```bash
docker build -t amao .
docker run --rm -it \
  -e OPENAI_API_KEY -e ANTHROPIC_API_KEY \
  -v "$(pwd)/workspace:/workspace" \
  amao run --dir /workspace --goal "Build a ..."
```

The container mounts `/workspace` as a volume so generated code and the SQLite state file persist
outside the container.

## Development

```bash
pip install -e ".[dev]"
ruff check .              # lint (includes bandit-style security rules)
ruff format --check .     # formatting
mypy src                  # type checking
pytest                    # unit tests (mocked OpenAI/Anthropic, real git in tmp dirs)
```

CI (`.github/workflows/ci.yml`) runs all of the above on Python 3.11/3.12, plus a `docker build`
smoke check.

## Security Notes

* **No arbitrary file writes.** The executor only ever proposes a *diff*; `GitHelper.apply_diff` is
  the sole place changes are applied, and it rejects absolute paths, `..` traversal, symlink modes,
  and binary content before ever calling `git apply` — defense in depth alongside git's own
  path-escape protections (`--unsafe-paths` is never passed).
- **Size-capped diffs and plans.** `MAX_DIFF_CHARS` caps what's sent to the reviewer (and what the
  executor may apply); `MAX_MILESTONES` caps the planner's output. Exceeding either halts the run
  for human review rather than silently truncating.
* **HTTPS-only webhooks.** `NOTIFIER_WEBHOOK_URL` must use `https://`; both `Config.validate()` and
  `Notifier.__init__` enforce this.
* **No secrets in version control.** `.gitignore` excludes `.env`, `*.db`, and generated project
  workspaces; `.env.example` documents the expected variables without real values.

## Extending the System

* **Swapping the Local Executor:** replace `LocalExecutorAgent` in `src/amao/agents.py` with a
  wrapper around a real coding CLI (Aider, Cursor CLI, etc.) — it just needs to return a unified
  diff string; `GitHelper.apply_diff` will validate and apply it either way.
* **Adjusting loop guard limits:** change `MAX_REVIEW_ATTEMPTS` via the environment.
* **Adding notification providers:** extend `Notifier` in `src/amao/notifier.py`.
