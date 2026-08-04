# Native (C/C++) Extensions — an exploration, not a performance initiative

**If you're resuming this: read this whole file first.** This started as "let's use this repo
for something fun — convert code to C/C++, see how Python talks to native libs, find what's
actually worth converting." The honest headline finding: **almost nothing in amao is.** amao is
I/O-bound top to bottom (LLM API calls, subprocess, SQLite, HTTP) — there's very little pure
computation to speed up. This file documents three real, working conversions built anyway, what
each one actually taught, and — most importantly — the real numbers, including the two cases
where the native version turned out to be **no faster, or outright slower**, and the one case
where a rigorous fuzz test caught a genuine security-relevant behavioral divergence.

Nothing here is required to run amao. Every native call site falls back to a pure-Python
implementation automatically if the corresponding `.so` hasn't been built — confirmed by actually
removing the built binaries and re-running the full test suite (210 passed, 1 skipped) before
writing this up, not just by reasoning about the `try/except`.

## What's here

| Directory | Binding mechanism | Wired into amao? | Verdict |
|---|---|---|---|
| `native/duration/` | `ctypes` (plain C, C ABI) | Yes — `amao.cli._format_duration` | Correct (20,000/20,000 random inputs match), but **slower** than pure Python (FFI call overhead dominates a function this small) |
| `native/progress_stats/` | `pybind11` (real C++ classes/STL) | Yes — `StateManager.get_progress_summary` | Correct (200/200 random row-sets match), but **not faster even at 100,000 rows** — marshaling cost dominates |
| `native/diff_validator/` | `pybind11` | **No — deliberately not wired in.** Case study only | Found **1 real divergence** in 2,004 differential-fuzzed cases against the actual production Python validator |

## 1. `native/duration/` — ctypes, the "raw" binding mechanism

Ports `amao.cli._format_duration` (seconds → `"1h2m3s"`-style string) to plain C
(`duration.c`), built with a `Makefile` into `libduration.so`, loaded via
`ctypes.CDLL(...)` in `src/amao/_native_duration.py`. This is the simplest possible
Python↔native mechanism: no build-system magic, no Python-specific compilation — any
shared library exposing a C-ABI function can be called this way, from any language.

`amao.cli._format_duration` now tries the native path first, falling back to
`_format_duration_python` (the original implementation, kept as-is) if the `.so` isn't
built or loadable.

**Correctness**: compared against 20,000 random float inputs — 0 mismatches. (One
known, documented, and irrelevant-in-practice divergence: the C port rounds
half-up, Python's `round()` rounds half-to-even; they can only differ on an exact
`.5`-second boundary, which a value derived from averaging real wall-clock durations
essentially never hits.)

**Performance — the honest number**: at 200,000 calls, pure Python averaged 0.266µs/call;
the ctypes version averaged 0.727µs/call — **the native version is 2.7x slower.** The
function does so little work (a few integer divisions) that the cost of crossing the
ctypes FFI boundary (argument marshaling, buffer allocation) is larger than the work
itself. This is the first real lesson: **converting a tiny function to native code doesn't
make it faster just because it's compiled** — the win only shows up when there's enough
actual work per call to amortize the crossing cost.

## 2. `native/progress_stats/` — pybind11, the "idiomatic modern C++" mechanism

Ports the aggregation half of `StateManager.get_progress_summary()` (counting milestones
by status, finding the current in-progress one, averaging completed durations, estimating
remaining time) to real C++ (`progress_stats.cpp`) — a proper class (`MilestoneRow`), a
result struct (`ProgressStats`), and a function operating on a `std::vector<MilestoneRow>`,
exposed to Python via [pybind11](https://pybind11.readthedocs.io/) as a genuine importable
extension module. This is the mechanism most real high-performance Python libraries use to
expose C++ today (in contrast to ctypes' C-ABI-only, manual-marshaling world) — it handles
classes, `std::optional`, and STL containers converting to/from Python natively.

Deliberately scoped to *only* the aggregation math: SQLite row fetching and
`datetime.strptime` timestamp parsing stay in Python in `state_manager.py` — there's
nothing to gain moving string-to-time parsing into C++, and doing so would just add
complexity without a real payoff, exactly the kind of unnecessary conversion this
exploration is trying to identify and avoid.

`StateManager.get_progress_summary()` parses SQLite rows into a plain tuple list, then
dispatches to `_aggregate_progress_native` (native path) or `_aggregate_progress_python`
(the original logic, extracted as-is) depending on whether
`amao._native_progress_stats.compute_progress_stats` imported successfully.

**Correctness**: a differential test (`tests/test_state_manager.py::test_native_and_python_aggregation_agree_on_random_inputs`)
compares both implementations against 200 randomly generated synthetic milestone lists (0–15
rows each, random statuses/durations) — 0 disagreements on any field.

**Performance — the honest number, and it's the more interesting one**:

| Row count | Pure Python | Native (pybind11) | Speedup |
|---|---|---|---|
| 10 | 0.0087ms | 0.0089ms | 0.98x (no real difference) |
| 1,000 | 0.6751ms | 0.6345ms | 1.06x (negligible) |
| 100,000 | 102.4ms | 116.9ms | **0.88x — native is *slower*** |

Even at 100,000 rows, the native version isn't faster. Why: the current design builds one
Python-constructed `MilestoneRow` pybind11 object *per row* before calling into C++ — that
per-row Python object construction is itself pure Python-object-creation overhead, and it
dominates the cost of the (trivial) aggregation math, regardless of how fast the C++ loop
itself is. **The real lesson**: passing a *list of individually-wrapped Python objects*
into a native extension doesn't buy you much — a genuine speedup at this row count would
need bulk primitive data crossing the boundary once (e.g. parallel NumPy arrays, or a raw
packed C struct buffer via the buffer protocol), not N individually-constructed wrapper
objects. Left as a documented "if you wanted to actually chase a speedup here, this is
where it would come from," not built — amao's real milestone counts (tens, not thousands)
would never benefit from this in practice anyway, so it wasn't worth the complexity here.

## 3. `native/diff_validator/` — case study, deliberately NOT wired into production

Ports `src/amao/git_helper.py`'s `_validate_diff`/`_validate_path` — **the single most
security-critical function in amao** (the only thing standing between an LLM-authored diff
and a path-traversal/symlink/absolute-path escape out of the target project directory) — to
C++, using `std::regex` for the line-pattern matching and `std::filesystem::weakly_canonical`
in place of Python's `Path.resolve(strict=False)`.

**This is explicitly a case study, not a candidate for production use.** `git_helper.py`
itself is untouched. The point of building this was to test a specific claim: *is porting
security-critical validation logic to a second language actually as risky as it sounds, or
just theoretically risky?*

**The answer, found by actually running a differential fuzz test
(`native/diff_validator/tests/fuzz_compare.py`) against the real, unmodified Python
validator — 2,004 cases (2,000 randomized adversarial diffs: path traversal, absolute
paths, symlink-mode markers, binary markers, `/dev/null`, empty diffs, plus 4 targeted
real-filesystem-symlink cases) — found exactly 1 real disagreement:**

> A **dangling symlink** (the link exists, its target does not) inside the repo, pointing
> outside the repo directory. Python's real validator correctly flags this as unsafe
> (`escapes_directory`). The C++ port does not.

**Why they diverge** (this is the actual, mechanism-level reason, not a guess): Python's
`Path.resolve(strict=False)` substitutes a symlink's target text as soon as it encounters
the symlink, regardless of whether that target itself exists — so it always follows
`dangling → /nonexistent/target/xyz` and correctly notices `/nonexistent/target/xyz` is
outside the repo. C++'s `std::filesystem::weakly_canonical` works differently: it
determines how much of the path "exists" (via `status()`, which follows symlinks), then
only calls `canonical()` — the part that actually resolves symlinks — on that existing
prefix, appending the rest lexically. A dangling symlink's target doesn't exist, so
`status()` reports the symlink itself as not existing, its text is *never substituted*, and
`dangling/f.txt` gets lexically appended onto the repo root, looking safe when it isn't.

A real, non-obvious, security-relevant bug in the C++ port, found by testing, not by
inspection. **This is exactly the outcome that justifies keeping this a case study and
never wiring it into `git_helper.py`**: a rigorous-feeling 2,000+ case fuzz session in one
sitting still found a genuine gap. Passing that bar is not the same as a real security
audit, and a security validator deserves the latter, not the former, before anyone would
trust a reimplementation of it.

## What this exploration actually taught (the real summary)

1. **Python talks to native code via (at least) two real mechanisms**, demonstrated here
   with genuine working examples: `ctypes` (any C-ABI shared library, zero
   Python-specific build tooling, manual scalar/buffer marshaling) and `pybind11` (a real
   C++ library, compiles a proper CPython extension module, idiomatic class/STL/`optional`
   conversion — the mechanism most real numeric/ML Python libraries use for their C++ cores).
2. **Converting code to C/C++ doesn't automatically make it faster.** Both real
   conversions built here — a tiny scalar function and a small-scale aggregation — were
   *not* faster than pure Python, in one case measurably slower. The FFI/marshaling cost
   has to be smaller than the work being amortized across it; neither case here had enough
   real work per call to clear that bar.
3. **amao specifically has very little to gain from this**, because it's I/O-bound: LLM API
   calls, subprocess invocations (git, Docker, Codex/Claude CLIs elsewhere in this repo's
   history), SQLite queries, HTTP webhooks. None of that is CPU-bound work C/C++ would
   speed up — the bottleneck is always waiting on an external process or network call. The
   two genuine pure-compute functions found (`_format_duration`, the progress-stats
   aggregation) were the *entire* realistic candidate list.
4. **Porting security-critical code to a second language is riskier than it looks, even
   when you're being careful.** One real divergence surfaced in ~2,000 fuzzed cases,
   traceable to a genuinely subtle difference in how two languages' "resolve this path"
   primitives handle a dangling symlink. If this exercise had stopped at "the C++ looks
   right" instead of differentially fuzzing against the real Python implementation, that
   bug would never have been found.

## Building

```bash
source .venv/bin/activate
pip install -e ".[native]"       # installs pybind11 (only needed to build/rebuild)

make -C native/duration          # -> native/duration/libduration.so
native/progress_stats/build.sh    # -> native/progress_stats/progress_stats<suffix>.so
native/diff_validator/build.sh    # -> native/diff_validator/diff_validator<suffix>.so (case study)

python3 native/diff_validator/tests/fuzz_compare.py   # re-run the differential fuzz test
```

Compiled `.so` files are gitignored (`native/**/*.so`) — never committed, always rebuilt
locally. Dev/editable-install only: the Python loaders (`_native_duration.py`,
`_native_progress_stats.py`) find the compiled libraries via a path relative to their own
location in the repo checkout, which only resolves correctly for `pip install -e .`; this
was never built for distribution as a wheel with prebuilt binaries for other platforms.

## Directory map

```
native/
├── duration/
│   ├── duration.c        # ctypes target
│   ├── Makefile
│   └── libduration.so     # gitignored, built locally
├── progress_stats/
│   ├── progress_stats.cpp # pybind11 target, wired into StateManager
│   ├── build.sh
│   └── progress_stats.cpython-*.so   # gitignored, built locally
└── diff_validator/
    ├── diff_validator.cpp # pybind11 target, CASE STUDY -- not wired in
    ├── build.sh
    ├── diff_validator.cpython-*.so   # gitignored, built locally
    └── tests/fuzz_compare.py         # differential fuzz test vs. the real Python validator
```
