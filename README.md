# amao — Autonomous Multi-Agent Orchestration Engine

[![CI](https://github.com/udr-w/amao/actions/workflows/ci.yml/badge.svg)](https://github.com/udr-w/amao/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**Describe what you want built. amao plans it, writes it, reviews it, and commits it — and only
interrupts you when it's genuinely stuck.**

A Planner agent breaks your goal into milestones. A Local Executor agent turns each milestone into
a sandboxed unified diff. A Reviewer agent checks the resulting `git diff` against what was asked
for. An Orchestrator loop drives all three with persistent state, rate-limit resilience, and
human-in-the-loop alerting — so you can kick off a build and walk away, not babysit a chat window.

Every one of those three roles is swappable between six providers (OpenAI, Anthropic, DeepSeek,
Moonshot/Kimi, xAI/Grok, Gemini) with a single environment variable — see
[Rewiring the agents](#rewiring-the-agents).

---

## Why this exists

Before automating this, the workflow was: paste a plan from one chat window, paste the resulting
code into another tool, paste the diff into a third tool for review, paste the feedback back into
the first window, repeat. amao replaces that manual relay with one process that:

* **Never writes files directly from LLM output.** The executor proposes a diff; a dedicated
  validator is the only thing that ever touches your filesystem, and it actively rejects anything
  that tries to escape the project directory.
* **Remembers where it left off.** Kill the process, restart the machine, come back tomorrow —
  `amao run` on the same directory resumes exactly where it stopped.
* **Knows when to stop and ask for help.** A milestone that keeps failing review halts and pings
  you, instead of quietly burning API credits in a loop forever.
* **Doesn't lock you into one AI vendor.** Point any of the three roles at OpenAI, Anthropic,
  DeepSeek, Moonshot (Kimi), xAI (Grok), or Gemini — mix and match per role.

---

## Try it on your own idea in under 5 minutes

This isn't limited to the sample task-manager demo shipped in `examples/` — that's just one
illustration. amao takes **any** natural-language goal. Here's the shape of using it for something
of your own:

```bash
# 1. Install (see "Installation" below if this fails on your system, or you're on Windows)
source install.sh

# 2. Set at least one provider's key in .env (install.sh created it from .env.example)
#    -- see "Rewiring the agents" if you only want one vendor
$EDITOR .env

# 3. Point it at a fresh directory and describe what you want -- in your own words
amao run --dir ./my-idea --goal "Build a CLI habit tracker in Python: log a habit as done for
today, show a streak count per habit, and store everything in a local SQLite file. Include a
--report flag that prints a weekly summary table."
```

What happens next, with no further input from you:

1. **Planning** — the goal above gets broken into discrete milestones (e.g. "set up CLI arg
   parsing and SQLite schema", "implement `log` command", "implement streak calculation",
   "implement `--report`").
2. **Execution** — each milestone becomes a task prompt, which the executor turns into a real git
   diff inside `./my-idea`.
3. **Review** — the diff gets checked against that milestone's spec. Approved diffs get committed
   (`feat: completed <milestone>`); rejected ones get retried with the reviewer's feedback folded
   in, up to `MAX_REVIEW_ATTEMPTS` times.
4. **You get paged only if it's stuck** — a milestone that can't pass review after enough attempts
   halts and notifies you (stdout always; a webhook too, if configured) with exactly why.

Watch it work with `git log` and `git diff` inside `./my-idea` as it runs, or inspect
`./my-idea/orchestrator_state.db` for the full per-milestone audit trail. Try a goal that's
deliberately underspecified and see how the planner interprets it, or one with a strict constraint
("no external dependencies beyond the standard library") and see the reviewer enforce it. Because
nothing here is hardcoded to the task-manager example, the interesting part is seeing what it does
with a goal you actually care about.

---

## Key Features

* **Sandboxed diff-based execution.** The Local Executor never writes files directly — it proposes
  a unified diff, which `GitHelper.apply_diff` validates (no absolute paths, no `..` traversal, no
  symlinks, no binary content, size-capped) and applies via `git apply --check` before committing
  to it. This is the single choke point every code change passes through.
* **Rewireable agents, six providers to pick from.** Planner, Executor, and Reviewer each talk to
  an `LLMBackend` interface, not a hardcoded SDK — flip `REVIEWER_PROVIDER=openai` to make GPT the
  reviewer instead of Claude, try `moonshot` (Kimi) or `deepseek` as the executor, or point every
  role at one vendor. See [Rewiring the agents](#rewiring-the-agents).
* **Prompt caching, wired in by default.** Static instructions are split from per-call data so
  OpenAI's automatic prefix caching and Anthropic's explicit `cache_control` breakpoints both have
  something to cache — cutting redundant token cost and latency on repeated calls (most notably
  `generate_task_prompt`, invoked once per milestone attempt). See
  [Prompt caching](#prompt-caching).
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
* **Optional Tester agent: runs the project's own tests, not just an LLM's opinion of the diff.**
  Sits between Executor and Reviewer, in a disposable Docker sandbox — pytest/npm/go test for
  backend code, a headless-Chromium Selenium check plus a screenshot for web UIs, and an
  LLM-generated `behave`/Gherkin scenario for click-through interactions. See
  [Automated testing](#automated-testing-the-tester-agent).
* **Progress visibility and mid-flight milestones, without touching the database directly.**
  `amao status` and `amao logs` read the same `orchestrator_state.db` a running `amao run` writes
  to, and `amao add-milestone` appends a new one a running (or future) `amao run` picks up on its
  next iteration. See [Monitoring and mid-flight changes](#monitoring-and-mid-flight-changes).
* **Goal-grounded review.** The Reviewer's system prompt includes the project's original goal, not
  just the current milestone's own spec — a cheap check against a milestone that technically
  satisfies its own description but drifts from what the project is actually supposed to build.

## Repository Structure

```
.
├── src/amao/
│   ├── cli.py             # `amao` console-script entry point
│   ├── config.py          # Env-driven settings, per-role provider/model resolution, validate()
│   ├── exceptions.py       # AmaoError hierarchy (recoverable vs. halting)
│   ├── llm.py              # LLMBackend abstraction: OpenAIBackend, AnthropicBackend, build_backend()
│   ├── models.py           # Milestone / ReviewResult / ExecutionResult + MilestoneStatus
│   ├── state_manager.py    # SQLite persistence for milestones + audit log
│   ├── rate_limiter.py     # Retry/backoff decorator
│   ├── notifier.py         # HTTPS webhook alert dispatcher
│   ├── agents.py           # PlannerAgent, LocalExecutorAgent, ReviewerAgent (provider-agnostic)
│   ├── git_helper.py       # Git subprocess wrapper + sandboxed apply_diff
│   ├── orchestrator.py     # Main pipeline loop / state machine
│   └── testing/            # Optional Tester agent (see "Automated testing" below)
│       ├── agent.py            # TesterAgent: runs applicable strategies, aggregates a TestOutcome
│       ├── strategies.py       # TestStrategy ABC + pytest/npm/go/web-UI strategies + registry
│       ├── bdd.py              # LLM-generated Gherkin scenario + behave execution
│       ├── sandbox.py          # DockerSandbox: runs a strategy's command in a disposable container
│       ├── image_builder.py    # Builds the pre-baked Chromium/selenium/behave image, once
│       └── docker/webui-tester.Dockerfile
├── examples/demo_run.py     # Self-contained demo (CLI Task Manager app) -- one example, not the only use
├── tests/                   # pytest suite (mocked SDKs, real git in tmp dirs)
├── install.sh / uninstall.sh     # venv + install + .env setup, and its teardown (Linux/macOS)
├── install.ps1 / uninstall.ps1   # same, for Windows -- see Installation's caveat
├── pyproject.toml
├── Dockerfile / .dockerignore
└── .github/workflows/ci.yml
```

## Getting Started

### Prerequisites

* Python 3.10+
* Git, available on `PATH` (required at runtime, not just for development)
* Docker, available and reachable — only if you turn on `ENABLE_TESTER`
  (see [Automated testing](#automated-testing-the-tester-agent))

### Installation

Quickest path — one script handles the venv (including the two failure modes below), installs
amao, and seeds `.env` from `.env.example` if you don't have one yet. Safe to re-run, and
uninstalls just as easily (`source uninstall.sh` / `.\uninstall.ps1`, see below).

**Linux/macOS:**

```bash
source install.sh   # `source`, not `./` -- this is what lets it activate .venv for you too,
                     # a script run as ./install.sh can't change its parent shell's environment
                     # (a shell/OS-level rule, not an amao limitation). ./install.sh still works,
                     # you'd just run `source .venv/bin/activate` yourself right after.
```

**Windows (PowerShell):**

```powershell
.\install.ps1
```

Run directly (not dot-sourced) — PowerShell's `$env:` variables aren't scoped the way bash's are,
so this one does activate the venv in your session either way. If you get "running scripts is
disabled on this system", that's Windows' script execution policy, unrelated to amao — run once:
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then retry.
**Caveat, stated plainly: the PowerShell scripts are written from correct knowledge of how
venvs/`Activate.ps1`/`$env:` scoping work on Windows, but this project has no Windows machine to
verify them against — unlike `install.sh`/`uninstall.sh`, which were tested end-to-end (happy path,
the `uv` fallback, sourced vs. executed, idempotent re-runs) before being documented here. If
something doesn't work as described, please open an issue.**

Or by hand, if you'd rather see each step:

```bash
pip install -e ".[dev]"
```

If that's all you needed, skip ahead. If not, you'll hit one of two errors, in order, depending on
your system:

**1. `error: externally-managed-environment`** — Debian/Ubuntu-family systems (PEP 668) refuse
`pip install` outside a virtual environment, by design. Create one first:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**2. `ensurepip is not available`** (while running the `python3 -m venv` command above) — the
`python3-venv` package itself isn't installed, which is common on minimal/server Debian/Ubuntu
images, some containers, and WSL. Pick one:

```bash
# Option A: install the missing system package, then retry python3 -m venv above
sudo apt install python3.12-venv   # match your `python3 --version`

# Option B: skip apt entirely and use uv instead (no sudo needed)
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
```

(No `uv`? Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`, or via `pipx install
uv` — see [astral.sh/uv](https://astral.sh/uv) for other methods.)

**Uninstalling:** `source uninstall.sh` (Linux/macOS) or `.\uninstall.ps1` (Windows) removes
`.venv` — that's the entirety of what gets installed, nothing touches system Python or anything
outside this repo directory. `.env` is left in place by default since it may hold real API keys;
pass `--with-env` (`-WithEnv` on Windows) to remove it too.

### Configuration

Copy `.env.example` to `.env` and fill in real values (`install.sh` already did this for you if you
used it) — amao loads `.env` from the directory you run it in automatically at startup, no
`export` required:

```bash
OPENAI_API_KEY=your-openai-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key
NOTIFIER_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL  # optional, HTTPS only
```

Never commit `.env` (it's already gitignored). Exporting real environment variables works too and
always takes precedence over whatever's in `.env`, so CI/Docker/`-e VAR` setups are unaffected.

`Config.validate()` is called on every `Orchestrator` construction (CLI, demo, or programmatic
use) and fails fast with a clear error if a key required by your chosen providers is missing —
see [Rewiring the agents](#rewiring-the-agents) below, you only need the key(s) for the
provider(s) you actually select.

Other tunables (all optional, see `.env.example` for defaults): `MAX_REVIEW_ATTEMPTS`,
`MAX_MILESTONES`, `MAX_DIFF_CHARS`, `MAX_GOAL_CHARS`, `REQUEST_TIMEOUT_SECONDS`.

### Rewiring the agents

Planner, Executor, and Reviewer each run against an `LLMBackend` (`src/amao/llm.py`), not a
hardcoded SDK client. Which provider backs each role is pure config:

| Role | Provider env var | Model env var | Default provider | Default model |
|---|---|---|---|---|
| Planner | `PLANNER_PROVIDER` | `PLANNER_MODEL` | `openai` | `gpt-4o` |
| Executor | `EXECUTOR_PROVIDER` | `EXECUTOR_MODEL` | `openai` | `gpt-4o` |
| Reviewer | `REVIEWER_PROVIDER` | `REVIEWER_MODEL` | `anthropic` | `claude-3-7-sonnet-20250219` |

Each provider env var accepts any of:

| Provider | API key env var | Default model | Notes |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4o` | Native OpenAI endpoint |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-7-sonnet-20250219` | Native Anthropic endpoint |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` | OpenAI-compatible |
| `moonshot` | `MOONSHOT_API_KEY` | `kimi-k3` | OpenAI-compatible (Kimi) |
| `xai` | `XAI_API_KEY` | `grok-4.3` | OpenAI-compatible (Grok) |
| `gemini` | `GEMINI_API_KEY` | `gemini-3.5-flash` | OpenAI-compatible |

DeepSeek, Moonshot, xAI, and Gemini are all reached through the same `OpenAIBackend` — each just
exposes an OpenAI-Chat-Completions-compatible endpoint, so plugging one in is a `base_url` swap,
not a new SDK. **Model names for these move fast** (DeepSeek deprecated its old `deepseek-chat` /
`deepseek-reasoner` names in favor of versioned ones in July 2026, for example) — if a default
here 404s, check that provider's current docs and override it with the matching `*_MODEL` env var.

Leave a `*_MODEL` blank and it resolves to that provider's default automatically — so switching
`REVIEWER_PROVIDER` doesn't leave you pointed at the wrong vendor's model name by accident.

**The defaults above are unchanged from amao's original design** — you don't need to set anything
to get the original OpenAI-plans-and-executes, Anthropic-reviews behavior. Rewiring is opt-in:

```bash
# Make GPT the reviewer instead of Claude
export REVIEWER_PROVIDER=openai

# Try Kimi K3 as the executor
export EXECUTOR_PROVIDER=moonshot
export MOONSHOT_API_KEY="sk-..."

# Go all-in on one vendor -- only DEEPSEEK_API_KEY is required in this case
export PLANNER_PROVIDER=deepseek
export EXECUTOR_PROVIDER=deepseek
export REVIEWER_PROVIDER=deepseek

# Use a specific model instead of the provider's default
export PLANNER_MODEL=gpt-4o-mini
```

`Config.validate()` only requires the API key for provider(s) actually selected above — rewire
everything to one vendor and every other `*_API_KEY` can stay blank.

**Adding another provider** is one registry entry in `src/amao/llm.py` if it's OpenAI-compatible
(most are) — see [CONTRIBUTING.md](./CONTRIBUTING.md#adding-a-new-llm-provider).

### Prompt caching

Static system instructions are kept separate from per-call data (milestone descriptions, diffs) in
every agent call, specifically so caching has something stable to key off:

* **The native OpenAI role(s)** get automatic prefix caching (no code changes needed on your end)
  plus a `prompt_cache_key` per call site to improve cache-hit routing.
* **The native Anthropic role(s)** get an explicit `cache_control: {"type": "ephemeral"}`
  breakpoint on the system prompt.
* **OpenAI-compatible third-party providers** (DeepSeek, Moonshot, xAI, Gemini) don't get
  `prompt_cache_key` — it's an OpenAI-specific routing hint, not part of the common wire format
  these providers implement, so amao doesn't send it to them. Whether *they* cache your requests
  at all is up to their own infrastructure, not something amao controls either way.

One honest caveat on the Anthropic side: it requires at least 1024 tokens (4096 on some newer
model generations) in a cache breakpoint before it actually caches anything — below that, it's a
no-op, not an error. amao's built-in system prompts are short, so this engages automatically and
for free the moment you (or a future version of this project) grow that prompt further — e.g. a
fuller review rubric — with zero additional code.

### Automated testing (the Tester agent)

By default, the Reviewer only ever reads a `git diff` — it never runs the code it's judging. That
works reasonably well for backend logic you can reason about from a diff, but it fundamentally
can't answer UI questions ("is the button in the right place," "does this render") from text
alone. The optional **Tester** agent sits between Executor and Reviewer and actually executes the
project before anyone judges it:

| Env var | Default | Effect |
|---|---|---|
| `ENABLE_TESTER` | `false` | Turns the whole Tester stage on. Off by default: it's a new hard runtime dependency (Docker) and pipeline stage that didn't exist before. |
| `ENABLE_BDD` | `false` | Adds an LLM-generated `behave`/Gherkin UI scenario on top. Inert unless `ENABLE_TESTER` is also on. Off by default: it's the least-proven layer — see the caveat below. |
| `TESTER_TIMEOUT_SECONDS` | `300` | Per-strategy timeout for the sandbox container. |
| `MAX_TEST_OUTPUT_CHARS` | `20000` | Caps test output fed back as milestone feedback / Reviewer evidence. |

**How it decides what to run** — `src/amao/testing/strategies.py`'s `TestStrategy.detect()` gates
everything; a project only ever gets tooling relevant to it:

| Strategy | Detects via | Runs in |
|---|---|---|
| `pytest` | `pyproject.toml`/`requirements.txt`/`setup.py`/`setup.cfg`, or loose `test_*.py` files | `python:3.12-slim` |
| `npm-test` | `package.json`'s `scripts.test` | `node:20-slim` |
| `go-test` | `go.mod` | `golang:1.24-bookworm` |
| `web-ui-python` | Django (`manage.py`), Flask/FastAPI (dependency manifest), or a bare `index.html` | `amao-webui-tester:local` (pre-built, see below) |
| `bdd-behave` | Same as `web-ui-python`, plus a scenario the Tester generated for this milestone | `amao-webui-tester:local` |

A project with both `go.mod` and a `package.json` test script gets both `go-test` and `npm-test`;
a pure-Go project never gets Node or Python tooling, and vice versa.

**What happens with the result:**

* A hard test failure short-circuits straight to REJECTED, with the test output as feedback —
  the Reviewer is never even called, the same way an empty diff already skips it.
* A pass (or "nothing applicable to test") proceeds to the Reviewer as before, now with a
  test-evidence summary — and, for `web-ui-python`, a screenshot — attached to its prompt.
* A sandbox/infra failure (Docker missing, image build failure) halts the run and notifies you,
  the same as any other infra failure — it does **not** count against a milestone's retry budget.

**The Chromium-based strategies use a different image strategy than pytest/npm/go on purpose.**
Installing Chromium fresh via `apt-get` on every run was measured at **~14 minutes** (it pulls in
a large X11/GTK dependency tree a slim image has none of) — completely impractical for a pipeline
that can retry a milestone several times. `image_builder.py` instead builds a small local-only
`amao-webui-tester:local` image (Chromium + selenium + behave pre-installed) once, checking
`docker image inspect` first; every run after that is a plain `docker run` against the already-built
image — confirmed at **~32 seconds** end-to-end once built.

**Honest limitations, not hidden ones:**
* Only Python web apps are covered (Django/Flask/FastAPI/static). A Node-target UI/BDD strategy
  is planned but not built — see `TESTER_AGENT_PLAN.md`.
* `bdd-behave`'s Gherkin generation is constrained to a small fixed step vocabulary (visit the
  homepage / click "x" / fill in "field" with "value" / should see "x" / title contains "x") —
  not free-form Gherkin. An LLM inventing its own step phrasing couldn't be matched against step
  definitions shipped ahead of time, so this trades generality for something that actually runs.
* This whole layer got real, non-mocked Docker verification during development (see
  `TESTER_AGENT_PLAN.md` for specifics) — but an actual LLM-generated BDD scenario, end-to-end,
  has not been — only the prompt design and the execution engine were verified directly.

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

### Monitoring and mid-flight changes

Three commands read or write the same `orchestrator_state.db` a running or finished `amao run`
uses, without needing any LLM API key — they never construct an `Orchestrator`:

```bash
amao status --dir ./my_project
```

Prints milestone counts by status, the current in-progress milestone and its attempt count,
average time per completed milestone and an estimated time remaining once there's enough
completed-milestone data to base one on, and an explicit callout if any milestone is `HALTED`
(stuck, needs a human — see `amao logs` to see why).

```bash
amao logs --dir ./my_project [--milestone 3] [--limit 20]
```

Prints audit log entries (planner output, review verdicts, test results) — the same data that's
sitting in `orchestrator_state.db`'s `audit_logs` table, made readable without opening `sqlite3`
directly. Filter to one milestone with `--milestone`, cap the number of entries with `--limit`
(default 20).

```bash
amao add-milestone --dir ./my_project --title "Add CSV export" --description "..."
```

Appends a new `PENDING` milestone. If `amao run` is actively running against the same `--dir` in
another process, it picks the new milestone up on its next iteration — milestones are processed in
insertion order, and SQLite's WAL mode (already enabled) is what makes this safe across two
processes touching the same file. This is the current answer to "how do I add a new idea without
restarting the whole run": append it here rather than re-invoking `amao run --goal` (a new `--goal`
on an existing `--dir` is otherwise ignored — planning only happens once, when the milestone table
is empty).

### Running the Demo

```bash
python examples/demo_run.py
```

Builds a sample CLI Task Manager app end-to-end in `./demo_task_manager_app`. This is one example
goal, not a fixed template — see [Try it on your own idea](#try-it-on-your-own-idea-in-under-5-minutes)
above for pointing amao at something of your own.

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

`-e OPENAI_API_KEY -e ANTHROPIC_API_KEY` above is just the default configuration's two keys, the
same way the default `PLANNER_PROVIDER`/`EXECUTOR_PROVIDER`/`REVIEWER_PROVIDER` are `openai`/
`openai`/`anthropic` — Docker doesn't limit which providers you can use, `docker run -e VAR` only
forwards a host environment variable through if it's set. All six providers from
[Rewiring the agents](#rewiring-the-agents) work the same way in the container; pass through
whichever `*_PROVIDER`/`*_MODEL`/`*_API_KEY` variables your configuration actually needs, e.g.:

```bash
docker run --rm -it \
  -e PLANNER_PROVIDER=deepseek -e EXECUTOR_PROVIDER=deepseek -e REVIEWER_PROVIDER=deepseek \
  -e DEEPSEEK_API_KEY \
  -v "$(pwd)/workspace:/workspace" \
  amao run --dir /workspace --goal "Build a ..."
```

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
* **Size-capped diffs, plans, and goals.** `MAX_DIFF_CHARS` caps what's sent to the reviewer (and
  what the executor may apply); `MAX_MILESTONES` caps the planner's output; `MAX_GOAL_CHARS` caps
  the input goal itself. Exceeding any of these halts the run for human review rather than
  silently truncating.
* **HTTPS-only webhooks.** `NOTIFIER_WEBHOOK_URL` must use `https://`; both `Config.validate()` and
  `Notifier.__init__` enforce this.
* **Only the credentials you actually use are required.** `Config.validate()` checks the API
  key(s) for whichever provider(s) your role config selects — see
  [Rewiring the agents](#rewiring-the-agents) — not a hardcoded pair.
* **No secrets in version control.** `.gitignore` excludes `.env`, `*.db`, and generated project
  workspaces; `.env.example` documents the expected variables without real values.
* **The Tester agent (`ENABLE_TESTER`) runs LLM-generated code, unlike everything else in amao,
  which only ever validates and applies diffs.** That's a materially larger security surface, so
  it's sandboxed accordingly: every strategy runs in a disposable, `--rm` Docker container as the
  *host's* UID/GID (never root, even though the base images default to it), scoped to a mounted
  copy of the project directory only. It is **not** run with `--network none` — setup steps
  (`pip install`, `npm install`) need to reach public package registries, a deliberate and narrower
  tradeoff than full isolation, not full network access to arbitrary code. See
  [Automated testing](#automated-testing-the-tester-agent) and `TESTER_AGENT_PLAN.md` for the full
  reasoning and its limits.

Found a security issue? See [SECURITY.md](./SECURITY.md) for how to report it privately.

## Extending the System

* **Adding a new LLM provider:** implement `LLMBackend` in `src/amao/llm.py` (one method,
  `complete(system, user, cache_key, json_mode) -> str`) and wire it into `build_backend()` — every
  agent role picks it up automatically via config, with no changes to `agents.py`.
* **Swapping the Local Executor:** replace `LocalExecutorAgent` in `src/amao/agents.py` with a
  wrapper around a real coding CLI (Aider, Cursor CLI, etc.) — it just needs to return a unified
  diff string; `GitHelper.apply_diff` will validate and apply it either way.
* **Adjusting loop guard limits:** change `MAX_REVIEW_ATTEMPTS` via the environment.
* **Adding notification providers:** extend `Notifier` in `src/amao/notifier.py`.
* **Adding a new test strategy:** implement `TestStrategy` in `src/amao/testing/strategies.py`
  (`detect()`, `shell_command()`, and a `docker_image`) and add it to `DEFAULT_STRATEGIES` — see
  [CONTRIBUTING.md](./CONTRIBUTING.md#adding-a-new-test-strategy).

Scoped-but-not-started work (a web dashboard, per-subtask model tiering, and other deferred items)
is tracked in [`NEXT_MILESTONES.md`](./NEXT_MILESTONES.md) — read it before starting any of those
rather than re-deriving the design from scratch.

## Contributing

Bug reports, feature requests, and pull requests are welcome — see
[CONTRIBUTING.md](./CONTRIBUTING.md) for how to get set up, and
[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) for the ground rules. Dependency updates are tracked
automatically via [Dependabot](./.github/dependabot.yml).
