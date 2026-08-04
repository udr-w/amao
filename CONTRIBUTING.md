# Contributing to amao

Thanks for considering a contribution. This project is small and moves fast, so a quick heads-up
before a large change (open an issue first) saves everyone rework.

## Ground rules

By participating, you're expected to follow the [Code of Conduct](./CODE_OF_CONDUCT.md). Security
vulnerabilities should be reported privately per [SECURITY.md](./SECURITY.md), never as a public
issue.

## Getting set up

```bash
git clone https://github.com/udr-w/amao.git
cd amao
pip install -e ".[dev]"
```

Tests don't call any real API — OpenAI/Anthropic clients are mocked, so you don't need API keys
to develop or run the test suite. `git_helper.py` tests do shell out to a real, local `git`
binary in a temp directory (no network access needed).

## Before opening a PR

Run the same checks CI runs:

```bash
ruff check .              # lint (includes bandit-style security rules)
ruff format .             # auto-format
mypy src                  # type checking
pytest                    # unit tests
```

All four must pass. If `ruff check` flags something you believe is a false positive, prefer a
narrowly-scoped `# noqa: <RULE>` with a one-line comment explaining why, over disabling the rule
project-wide.

## Code style

* Follow the patterns already in the codebase rather than introducing a new one for a similar
  problem — e.g. new exceptions belong in the `AmaoError` hierarchy in `exceptions.py`; new config
  belongs in `config.py` with validation in `Config.validate()`.
* Type hints are required (`mypy src` is strict — `disallow_untyped_defs = true`).
* Prefer no comments; when you do add one, make it explain *why*, not *what* — the code should
  already say what it does.
* If you're touching anything in `git_helper.py`'s diff validation, add a test that proves the
  specific attack/edge case you're fixing (see the existing `test_apply_diff_rejects_*` tests in
  `tests/test_git_helper.py` for the pattern) — this is the most security-sensitive part of the
  codebase.

## Adding a new LLM provider

`src/amao/llm.py` is the extension point. Most providers worth adding expose an
OpenAI-Chat-Completions-compatible endpoint (DeepSeek, Moonshot, xAI, and Gemini all do, alongside
OpenAI itself) — for those, adding support is one entry in the `PROVIDERS` registry:

```python
"some_provider": ProviderSpec(
    default_model="...",
    api_key_env="SOME_PROVIDER_API_KEY",
    kind="openai",
    base_url="https://api.some-provider.example/v1",
),
```

Then add a matching `SOME_PROVIDER_API_KEY` field to `Config` in `config.py` (the field name must
match `api_key_env` exactly — `Config.api_keys()` looks it up via `getattr`). `build_backend()`
picks up any `kind="openai"` entry automatically via `OpenAIBackend`.

For a genuinely different wire format (not OpenAI-compatible), implement `LLMBackend` directly
(one method: `complete(system, user, cache_key, json_mode) -> str`), add a `kind` for it, and
branch on that kind in `build_backend()` the same way the existing `"anthropic"` kind does.

No changes to `agents.py` or `orchestrator.py` should be needed either way — if they are, that's a
sign the abstraction leaked and is itself worth fixing.

Model names for third-party providers move fast — double check the provider's current docs before
picking a `default_model`, and mention in your PR when you last verified it resolves.

## Pull requests

* Keep PRs focused — one logical change per PR is much easier to review than a bundle.
* Update tests and, if behavior or config changed, the README, alongside the code change in the
  same PR.
* Describe *why* the change is needed, not just what it does — the PR template will prompt you.

## Reporting bugs / requesting features

Please use the issue templates — they ask for the specific details (repro steps, environment,
expected vs. actual behavior) that speed up triage.
