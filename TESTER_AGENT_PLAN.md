# Tester Agent — Design & Progress Tracker

**If you're resuming this in a new session: read this whole file first**, then check the
"Current status" line and the phase checklists below for the next unchecked item. Say "resume
tester agent work" and pick up from there.

Current status: **Phases 0-2 + wiring done and verified -- all three Tier-1 strategies
(pytest/npm/go) confirmed against a real, non-mocked Docker daemon, pass and fail cases each.
Phase 3 (web UI / Selenium / Cucumber) and Phase 4 (docs/CI polish) not started.**

## Why this feature exists

The pipeline today is Planner → Executor → Reviewer. The Reviewer only ever reads a `git diff` and
judges it against the milestone spec via an LLM call — it never *runs* the generated code. That's
a real gap: for a backend CLI you can often reason about correctness from the diff, but for a
UI-heavy project (the concrete example that prompted this: a POS system) you fundamentally cannot
verify "is the button in the right place," "does the image render," "does navigation work" by
reading text. Those are runtime/visual questions.

This feature adds a **Tester** role between Executor and Reviewer that actually executes the
project — running its own test suite where one exists, and (in a later phase) driving a real
browser for UI-bearing projects — before the Reviewer ever sees the diff.

## Architecture decisions (settled — don't re-litigate without a reason)

1. **Pipeline order**: Executor → **Tester** → Reviewer.
   - If applicable tests run and **fail**: short-circuit straight to REJECTED with the test output
     as feedback, **skip the Reviewer LLM call entirely** (same pattern already used for an empty
     diff in `ReviewerAgent.review_code` — an unambiguous signal doesn't need an LLM opinion).
   - If applicable tests run and **pass**, or **no applicable strategy was found at all**: proceed
     to the Reviewer as before, but now with a test-evidence summary attached to its prompt so its
     judgment is grounded in something more than diff-reading.
   - A **sandbox/infra failure** (Docker missing, image pull failure) is NOT a test failure — it's
     an infra problem and should halt+notify like every other infra failure in this codebase, not
     consume a milestone's attempt budget.

2. **Tool selection must be strategy-gated, never blanket-applied.** Each `TestStrategy` declares
   its own `detect(project_dir) -> bool`. The Tester only runs strategies that detect as
   applicable — a pure-Python CLI project must never spin up Node or Go tooling, and vice versa.
   This was an explicit, repeated requirement from the person who commissioned this feature:
   *"testers must apply only correct tools for the project. Unrelevant tools must not be used even
   though they are supported."*

3. **Sandboxing: Docker, one disposable container per test run, one container per applicable
   strategy.** Confirmed by the repo owner over the alternative of running on the host — executing
   LLM-generated code directly on the host machine would be a real regression in the security
   posture this project has otherwise been careful about (see `git_helper.py`'s diff sandboxing).
   Implementation detail: shell out to the `docker` CLI via `subprocess` (matching the existing
   `GitHelper` pattern of shelling out to `git` rather than adding an SDK dependency) — no new
   PyPI dependency needed for this part.

4. **No monolithic "amao-tester" image.** Each strategy owns its own minimal, official upstream
   image (`python:3.12-slim`, `node:20-slim`, `golang:1.24-bookworm`, ...) rather than one bloated
   image bundling every language's toolchain. This is both leaner and a more literal expression of
   decision #2 — a Go project's container never even has Python installed, let alone pytest.

5. **`ENABLE_TESTER` defaults to `false` for now.** This is a deliberate, temporary rollout
   choice, not a design endpoint — flag it for review once Phase 2 is solid. Reasoning: this
   feature adds a new hard runtime prerequisite (Docker must be installed and reachable) and a new
   pipeline stage that didn't exist before. Flipping the default on for every existing user without
   Docker available would break them. Once this has real mileage, reconsider defaulting to `true`.

6. **Test failures are data, not exceptions.** `TesterAgent.test_project()` always returns a
   `TestOutcome` — it does not raise for "the tests ran and failed," exactly the same way
   `ReviewerAgent.review_code()` returns a `ReviewResult` rather than raising for "REJECTED." It
   only raises `TesterInfraError` (not a `RecoverableExecutionError` — this halts, it doesn't
   consume attempt budget) when the sandbox itself couldn't run at all.

## Known limitations (be upfront about these, don't quietly paper over them)

- Distinguishing "Docker couldn't even start the container" from "the container ran and the test
  command inside it failed" is done by pattern-matching known Docker-CLI error strings
  (`Cannot connect to the Docker daemon`, `No such image`, `pull access denied`, ...) in stderr
  when the `docker run` subprocess itself exits nonzero. This is a heuristic, not exhaustive —
  revisit if a real infra failure ever gets misclassified as a test failure (or vice versa).
- Setup steps (installing dependencies before running tests) currently need network access inside
  the sandbox container (`pip install`, `npm install`), so the container is **not** run with
  `--network none`. This is a deliberate, narrower tradeoff than full isolation — bounded to
  fetching packages from public registries, analogous to what any CI system already does — but is
  a real reduction from "fully network-isolated" and should be named as such, not hidden.
- Tier 2 (below) only covers **web** UIs. Native desktop/mobile UI testing (the literal POS
  scenario that prompted this) needs different tooling per platform (Appium, XCUITest,
  WinAppDriver, ...) that this plan does not yet cover — see "Open question" below.

## Real bugs a live Docker run caught that mocked unit tests couldn't (fixed, worth knowing why)

A real, non-mocked `TesterAgent.test_project()` run against a throwaway pytest project surfaced
two issues neither the mocked `subprocess.run` unit tests nor mypy/ruff could have caught, because
they're about the *actual behavior of official Docker images*, not amao's own logic:

1. **Root-owned files left in the user's project directory.** Official images
   (`python:3.12-slim`, etc.) run as root by default, so anything the test command writes into the
   mounted project dir (`.pytest_cache`, `__pycache__`, `node_modules`) came out owned by root on
   the host -- the user running amao couldn't `rm -rf` their own project dir afterward without
   `sudo`. Fixed in `sandbox.py` by adding `-u {uid}:{gid}` (host UID/GID) to the `docker run`
   invocation via `_docker_user_args()`. Skipped on platforms without `os.getuid()` (Windows).

2. **`pip install` then failing to find the installed package.** Once running as a non-root,
   passwd-less UID, `pip install` silently falls back to `--user` mode (installs under
   `$HOME/.local`), and that bin directory isn't on `$PATH` by default -- so `pytest` immediately
   after came back "not found" (exit 127). Fixed two ways together: `sandbox.py` sets
   `-e HOME=/tmp` (an arbitrary UID has no passwd entry, so `$HOME` is otherwise unset/unwritable,
   which also breaks npm's cache dir the same way) and `PytestStrategy.shell_command()` now starts
   with `export PATH="$HOME/.local/bin:$PATH"`.

**Takeaway for future strategies (Phase 3 and beyond): always smoke-test a new strategy against a
real Docker daemon before considering it done, not just the mocked unit tests.** The mocked tests
prove amao constructs the right command; they cannot prove that command actually works inside the
target image as a non-root user. This project has real Docker available -- use it.

3. **`apt-get install chromium chromium-driver` took ~14 minutes** on a bare `python:3.12-slim`
   in this environment -- chromium pulls in a large transitive dependency tree (X11/GTK libs
   etc.) that a "slim" base image has almost none of. Redoing that on every single test run (a
   milestone can be retried `MAX_REVIEW_ATTEMPTS` times) would have made `PythonWebUIStrategy`
   completely impractical -- this is not a "wait a bit longer" problem, it's a design-breaking one.
   **Fixed by abandoning the "no custom image" default for this one strategy specifically**:
   `src/amao/testing/docker/webui-tester.Dockerfile` pre-bakes Chromium + selenium into a
   locally-tagged `amao-webui-tester:local` image; `image_builder.py` builds it once (checking
   `docker image inspect` first) and every run after that is a normal `docker run` against an
   already-built image -- confirmed end-to-end at **~32 seconds** total (including a separate
   PytestStrategy run in the same pass) once the image existed, vs. 14+ minutes before. This is a
   deliberate, narrow exception to decision #4, not a reversal of it -- Tier-1 strategies still use
   plain official images with nothing to pre-build; only Selenium-based UI strategies get this
   treatment, and only because the setup-cost asymmetry specifically demands it.

## Phase checklist

### Phase 0 — Design (this document)
- [x] Written and captures the settled decisions above.

### Phase 1 — Core infrastructure
- [x] `src/amao/testing/models.py` — `TestOutcome` dataclass
- [x] `src/amao/exceptions.py` — add `TesterInfraError(AmaoError)` (halts, not recoverable)
- [x] `src/amao/testing/sandbox.py` — `DockerSandbox`: runs a shell command in a disposable
      container with the project dir mounted, captures output + exit code, enforces a timeout,
      classifies Docker-CLI-level failures as `TesterInfraError` vs. a normal nonzero test exit.
      Also runs as the host UID/GID with `HOME=/tmp` -- see "Real bugs" section above.
- [x] `src/amao/testing/strategies.py` — `TestStrategy` ABC (`name`, `docker_image`, `detect()`,
      `shell_command()`) + a strategy registry/`detect_strategies()` helper
- [x] `src/amao/testing/agent.py` — `TesterAgent.test_project(project_dir) -> TestOutcome`,
      running every applicable strategy and aggregating results
- [x] `src/amao/config.py` — `ENABLE_TESTER` (default `false`), `TESTER_TIMEOUT_SECONDS`,
      `MAX_TEST_OUTPUT_CHARS`
- [x] Unit tests: mock `subprocess.run`, don't require a real Docker daemon in CI
      (`tests/test_testing_sandbox.py`)

### Phase 2 — Tier-1 strategies (run the project's own tests; any backend stack)
- [x] `PytestStrategy` (image `python:3.12-slim`) -- verified against a real Docker run, both a
      passing and a failing case
- [x] `NpmTestStrategy` (image `node:20-slim`, detects via `package.json`'s `scripts.test`) --
      verified against a real Docker run (pass + fail cases). No PATH/permission issues: `npm
      install` (no `-g`) installs into the mounted project dir itself, which the host UID already
      owns, so the root-UID problem that hit pip didn't recur here.
- [x] `GoTestStrategy` (image `golang:1.24-bookworm`, detects via `go.mod`) -- verified against a
      real Docker run (pass + fail cases). No ownership/PATH issues: the Go toolchain in this
      image writes its build cache under the invoking user's own `$HOME` (which is `/tmp`, and
      writable), and `go test` needs no separate install step the way pip/npm do.
- [x] Unit tests per strategy (detection logic + command construction) --
      `tests/test_testing_strategies.py`

### Wiring (done alongside Phase 2)
- [x] `ReviewerAgent.review_code()` gained an optional `test_evidence: str | None = None` param,
      folded into the user prompt when present; default keeps existing callers unaffected
- [x] `Orchestrator._process_milestone()`: calls the Tester after the Executor, before the
      Reviewer, when `config.ENABLE_TESTER`; implements the short-circuit-on-failure /
      attach-evidence-on-pass behavior from decision #1
- [x] `Orchestrator.__init__`: constructs `self.tester` (DI-overridable like the other agents)
- [x] Orchestrator-level tests for: tests pass → reviewer called with evidence; tests fail →
      short-circuit REJECTED, reviewer never called; no applicable strategy → falls through to
      reviewer as before; `TesterInfraError` → halts+notifies like other infra failures
      (`tests/test_orchestrator.py`, the `tester_enabled` fixture and tests around it)

### Verification + commit (repeat after every phase, not just at the end)
- [x] `ruff check .` / `ruff format --check .` / `mypy src` / `pytest` all green (131 tests,
      96% coverage as of this checkpoint)
- [x] Update this file's checkboxes to match reality
- [ ] Commit and push — don't let multiple phases pile up uncommitted (in progress; do this next)

### Phase 3 — Tier-2 web UI strategies (IN PROGRESS -- web UI done, BDD not started)
- [x] Detect a web app (Django via `manage.py`, Flask/FastAPI via dependency-manifest text search,
      or a bare static `index.html`) as a distinct signal from "has tests" -- `_detect_python_web_kind()`
      in `strategies.py`. Node web apps not covered yet, see the note below.
- [x] `PythonWebUIStrategy`: starts the detected app in the background, drives headless Chromium
      via Selenium's Python bindings, verifies the page renders actual HTML, captures a screenshot.
      **Not** the originally-planned `selenium/standalone-chromium` image -- see finding #3 above
      for why a custom pre-built image was necessary instead. Verified end-to-end against a real
      Flask app: homepage load + correct page title + valid screenshot file (pass case, ~32s warm),
      and a crashing app correctly detected as a failure (~26s, bounded by the port-wait timeout).
- [x] Screenshot fed to the Reviewer as vision evidence -- see the "Add vision support" work above
      (`LLMBackend.complete(images=...)`, `ReviewerAgent.review_code(screenshots=...)`,
      `Orchestrator` threading `outcome.screenshots` through). Done ahead of this phase since it
      was a clean prerequisite to build and verify in isolation first.
- [ ] **Node web apps** (Express/Next/etc., or built SPA output): not yet implemented. Would need
      its own `NodeWebUIStrategy` using Node's `selenium-webdriver` bindings (not Python's) to
      avoid mixing runtimes in one image, per decision #4 -- and its own pre-built image, per
      finding #3, for the same chromium-install-cost reason. Structurally the same shape as
      `PythonWebUIStrategy`; nobody has written or verified it yet.
- [ ] BDD option, per the repo owner's explicit ask ("cucumber"): let the Tester (via an LLM call,
      same pattern as the other agents) generate a Gherkin `.feature` file from the milestone
      description, executed by `behave` for Python-target projects or `cucumber-js` for
      Node-target projects — scope to those two ecosystems first, don't attempt every Cucumber
      flavor (Ruby/JVM) in one pass. **Not started.**
- [x] Unit tests for the web UI strategy (mocked -- detection logic, shell command construction,
      `ensure_ready()` calling the image builder). Real-Docker verification was done directly via
      Python one-liners during development (see finding #3), not yet turned into an automated,
      opt-in "real Docker" test file the way Tier-1 strategies eventually should be too (Phase 4
      CI task covers this).

### Phase 4 — Polish, docs, CI (NOT STARTED)
- [ ] README: document `ENABLE_TESTER`, the strategy list, and the Docker prerequisite this adds
- [ ] CONTRIBUTING.md: "Adding a new test strategy" section, mirroring the existing
      "Adding a new LLM provider" one
- [ ] Revisit decision #5 (`ENABLE_TESTER` default) once Phase 2/3 have real mileage
- [ ] CI: consider a job that exercises at least one strategy against a real Docker daemon
      (GitHub Actions `ubuntu-latest` runners have Docker available) as an integration check,
      separate from the mocked unit tests

## Open question (not yet decided — flag before starting native UI work)

Native desktop/mobile UI testing (Appium, XCUITest, WinAppDriver) is a much bigger, more
platform-fragmented undertaking than the web case, and isn't scoped into any phase above yet.
Don't start it without deciding, explicitly, how far amao should generically go here versus
clearly reporting "this project has a UI but amao doesn't know how to test this platform" and
leaving it to human review — that may be the more honest answer for some platforms indefinitely.

## File map (update as files are added)

```
src/amao/testing/
  __init__.py
  models.py       # TestOutcome
  sandbox.py      # DockerSandbox
  strategies.py   # TestStrategy ABC + Tier-1 strategies + registry
  agent.py        # TesterAgent
tests/
  test_testing_sandbox.py
  test_testing_strategies.py
  test_testing_agent.py
```
