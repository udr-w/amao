# Security Policy

## Supported Versions

amao is a young, actively developed project without long-term-support branches yet. Security
fixes are made against the latest commit on `main`. Please make sure you're on the latest version
before reporting an issue.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's private reporting flow:

1. Go to the [Security tab](https://github.com/udr-w/amao/security) of this repository.
2. Click **"Report a vulnerability"** to open a private advisory.

This lets us discuss and fix the issue before it's publicly visible.

If you'd prefer not to use GitHub's advisory flow, you can instead open a regular issue asking to
be contacted privately, without including any exploit details, and we'll follow up.

## What to include

To help us triage quickly, please include:

* A clear description of the vulnerability and its potential impact.
* Steps to reproduce it (a minimal repro is ideal — e.g. a crafted diff, config, or input).
* The version/commit you tested against.

## Scope

Areas that are especially security-sensitive in this codebase, and where reports are most
valuable:

* `src/amao/git_helper.py` — diff validation and `git apply` sandboxing (path traversal, symlinks,
  binary content).
* `src/amao/notifier.py` and any outbound HTTP calls (SSRF, webhook handling).
* `src/amao/config.py` — secret/credential handling.
* Anything that shells out to a subprocess.

## Response

This is a small, independently maintained project — response times are best-effort, not covered
by an SLA. We aim to acknowledge reports within a few days and will keep you updated as a fix
progresses.

## Disclosure

Once a fix is available, we'll publish a GitHub Security Advisory crediting the reporter (unless
you'd prefer to stay anonymous) and describing the issue and remediation.
