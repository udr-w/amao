---
name: Bug report
about: Report something that doesn't work as expected
title: "[Bug] "
labels: bug
assignees: ""
---

**Describe the bug**
A clear, concise description of what went wrong.

**To Reproduce**
Steps to reproduce the behavior, ideally minimal:
1. Command/config used (e.g. `amao run --dir ... --goal "..."`, or the Python snippet)
2. Relevant environment variables set (provider/model config, redact API keys)
3. What happened

**Expected behavior**
What you expected to happen instead.

**Logs / traceback**
Paste the relevant log output or traceback. If it involves a rejected diff or review, the
`orchestrator_state.db` audit log entry for that milestone is very useful — please redact
anything sensitive.

**Environment**
- amao version / commit: 
- Python version: 
- OS: 
- Provider config (`PLANNER_PROVIDER` / `EXECUTOR_PROVIDER` / `REVIEWER_PROVIDER`, if non-default): 

**Additional context**
Anything else relevant (e.g. does this happen with the default demo goal, or only with a custom
one? Is it reproducible every time?).
