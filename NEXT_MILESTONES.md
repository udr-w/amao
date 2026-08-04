# Next Milestones — Design & Progress Tracker

**If you're resuming this in a new session: read this whole file first.** It indexes work that was
scoped/decided but deliberately not started, so a future session (or a future `amao` self-hosted
run) can pick it up without re-deriving the reasoning. Say "resume next milestones" and check each
section's status line.

## 1. Read-only web dashboard for status/logs

Status: **not started.** Deferred deliberately, not for lack of scope.

`amao status`/`amao logs`/`amao add-milestone` (added 2026-08-04) expose progress, audit logs, and
mid-flight milestone injection via the CLI, reading `orchestrator_state.db` directly with no LLM
key required. A web dashboard is the natural next step now that the underlying data is exposed —
but it wasn't built in the same pass because it introduces a new attack surface (a local HTTP
server) that deserves its own explicit decision, not something to bundle into an unrelated CLI
change.

Open questions to resolve **before** writing code, not while writing it:
* Bind to `localhost` only by default, or support remote access? If remote, what auth (none is not
  acceptable for anything beyond loopback)?
* Read-only (status/logs, mirroring the CLI) for a first version, or does it also carry
  `add-milestone`-equivalent write capability? Recommend read-only first — writes through a web
  form are a bigger surface (CSRF, input validation) for a first cut.
* Serve via a small stdlib `http.server` subclass (zero new dependency, consistent with this
  project's "no new SDK dep unless clearly worth it" posture — see `DockerSandbox` shelling out to
  the `docker` CLI instead of a Docker SDK) or accept a real dependency (Flask/FastAPI) for
  maintainability? Recommend stdlib first; a project this size doesn't need a framework for one
  read-only page + a JSON endpoint.
* Auto-refresh via polling (simple, `<meta refresh>` or a few lines of JS) vs. SSE/WebSockets
  (real-time, more code, another thing to sandbox-test). Recommend polling first.

Suggested shape for a first version: `amao serve --dir <path> --host 127.0.0.1 --port 8765`,
serving one HTML page (progress summary + recent logs, polling `/api/status` and `/api/logs`
every few seconds) built on `StateManager.get_progress_summary()` / `get_audit_logs()` — both
already exist, no state-layer work needed, this is purely a CLI + presentation layer addition.

## 2. Multi-tier model dispatch for sub-agent work

Status: **not started — this is a new idea, captured here to design properly before building.**

### The idea, as given

> Add an improvement so the agent developing can span out more agents with different model levels
> for different tasks. It will use low token usage models to mechanical tasks that couldn't go
> wrong easily and use intelligent models for sub agents for intelligent tasks. Same for mid level.

In short: not every unit of work amao's agents do needs the same model. A mechanical, low-risk
subtask (e.g. "add an import," "rename a variable," "write a getter") doesn't need the same model
as a subtask requiring real judgment (e.g. "design the auth flow," "review whether this diff
satisfies an ambiguous spec"). Today amao picks one model **per role** (`PLANNER_MODEL`,
`EXECUTOR_MODEL`, `REVIEWER_MODEL`) and that model handles every call for that role, regardless of
how hard any individual milestone/subtask actually is. This idea proposes tiering **within** a
role's work, not just across roles.

### Why this doesn't exist yet, architecturally

Right now there's no sub-task fan-out inside amao's own pipeline at all: Planner produces a flat
list of milestones once; Executor takes one milestone and makes one LLM call to produce one diff;
Reviewer takes one diff and makes one LLM call to judge it. There is no point today where amao
decomposes a milestone into smaller pieces and dispatches each independently — "span out more
agents" is a genuinely new capability, not a config tweak on top of an existing one.

### Design sketch (for whoever builds this)

1. **Add a tier concept to config**, alongside the existing per-role provider/model settings:
   ```
   LOW_TIER_PROVIDER / LOW_TIER_MODEL     (default: cheap/fast — e.g. gpt-4o-mini or a Haiku-class model)
   MID_TIER_PROVIDER / MID_TIER_MODEL     (default: same as today's per-role default, e.g. gpt-4o)
   HIGH_TIER_PROVIDER / HIGH_TIER_MODEL   (default: today's Reviewer-class model, e.g. claude-3-7-sonnet)
   ```
   This composes cleanly with the existing `PROVIDERS` registry and `build_backend()` — no new
   abstraction needed there, just more config-driven `LLMBackend` instances.

2. **Classification is the hard part, and needs a decision, not an assumption.** Two candidate
   approaches, not mutually exclusive:
   * **Planner-time tagging.** When the Planner decomposes a goal into milestones, have it also
     emit a suggested tier per milestone (`"complexity": "low" | "mid" | "high"`) as part of its
     existing structured output — it already has the most context on relative difficulty at that
     point, and this costs nothing extra (same call, one more field).
   * **A cheap classifier call.** Before dispatching a subtask, a low-tier model call asks "is this
     mechanical or does it need judgment" — adds latency/cost of one extra cheap call per subtask,
     but works even for milestones the Planner didn't pre-tag (e.g. ones added later via
     `amao add-milestone`, which bypass the Planner entirely).

   Recommend starting with Planner-time tagging (cheaper, no extra round-trip) and treating
   milestones added via `amao add-milestone` (which have no Planner-assigned tier) as `mid` by
   default until/unless a classifier call is added later.

3. **Where sub-task fan-out actually happens is the real open design question.** Tiering config is
   easy; deciding *what gets decomposed into sub-agents* is not. Two candidate entry points:
   * **Inside the Executor**, for a single milestone: break "implement X" into smaller ordered
     steps (e.g. "add the data model," "add the route," "add the test") and dispatch each at its
     own tier, then merge the resulting diffs. This raises a real correctness problem worth
     flagging up front: amao's sandboxed diff-apply path (`GitHelper.apply_diff`) validates and
     applies **one diff at a time**; merging multiple sub-agent diffs into one that still applies
     cleanly (no overlapping hunks, consistent base state between sub-steps) is nontrivial and is
     the crux of this whole feature — don't underestimate it.
   * **Inside the Planner**, at the milestone-decomposition level: this already exists in spirit
     (milestones ARE the decomposition), so "tiering" here mostly reduces to #1 above (tag each
     milestone, then have `Orchestrator._process_milestone` pick the Executor backend per-milestone
     based on that tag) — no diff-merging problem, since each milestone already produces one diff
     via the existing single-call path. **This is the lower-risk, more incremental version of this
     feature** — recommend building this first, and only reaching for true intra-milestone
     sub-agent fan-out (previous bullet) if milestone-level tiering proves insufficient.

4. **Cost/latency tradeoff to measure, not assume.** More tiers means more distinct cached prompts
   (see the existing prompt-caching design in `src/amao/llm.py` — a different model per call means
   a different cache entry). Verify empirically that tiering actually nets out cheaper once
   cache-hit rates are accounted for, rather than assuming "cheaper model per call" trivially means
   "cheaper overall."

### Suggested phasing (mirroring how the Tester agent was scoped in `TESTER_AGENT_PLAN.md`)

* **Phase 0**: Add `LOW_TIER_PROVIDER`/`MID_TIER_PROVIDER`/`HIGH_TIER_PROVIDER` (+ `_MODEL` pairs)
  to `Config`, wired through `build_backend()` — no behavior change yet, just the config surface.
* **Phase 1**: Planner emits a per-milestone complexity tag in its structured output; default `mid`
  for anything without one (including `amao add-milestone`-injected milestones).
  `Orchestrator._process_milestone` picks the Executor's `LLMBackend` per-milestone based on that
  tag instead of one fixed `EXECUTOR_PROVIDER`/`EXECUTOR_MODEL` for the whole run.
* **Phase 2** (only if Phase 1 proves insufficient in practice): intra-milestone sub-agent
  decomposition inside the Executor, plus solving the diff-merge problem called out above — this is
  the expensive, uncertain part; don't start it speculatively.

## Related, already-tracked open items

Not new — flagging here so this file is a single index of "what's next," but the authoritative
detail lives in `TESTER_AGENT_PLAN.md`:
* Node-target web UI/BDD test strategy (`cucumber-js`) — not implemented, Python-only today.
* An actual LLM-generated BDD scenario has never been verified end-to-end against real Docker —
  only a hand-crafted one matching the fixed step vocabulary has been.
* Native desktop/mobile UI testing — explicitly out of scope, still an open question if it ever
  comes up again.
