# SNN — <Title>

**Priority:** P0|P1|P2 · **Tier:** sonnet|haiku · **Depends on:** <ids or none> · **Wave:** N
**Conflicts with:** <stories sharing a file, if any> · **Publishes/consumes:** <contract name, if any>

## Story

As a <role>, I need <capability>, so that <outcome>.

## Background (what is wrong today)

- `path/to/file.py:L1-L2` — <defect, with the concrete failure it causes>.
- ...

## Context pack

Everything the executor needs without exploring the repo:

- `path/to/owned.py` (<n> lines) map: `function_a(args) -> ret` L10-40; `Class.method` L50-90; ...
- Signatures/data shapes that must stay compatible: ...
- **Callers / importers** of anything you change (keep them working):
  `other/module.py:123` (`call(kw=...)`), ...
- Shared test files and who owns them: ...
- Reusable helpers/fixtures: `tests/conftest.py::fixture_name`, `module.helper()` L..
- Gotchas: <platform, library quirks, things that look wrong but are intentional>.

## Owned files

- `path/to/owned.py`
- New: `tests/unit/test_sNN_<topic>.py`

## Tasks

1. <imperative, specific, with the exact behaviour required>
2. ...

## Success criteria

- [ ] <observable, testable statement — a test or command proves it>
- [ ] ...

## Verification

```bash
python -m pytest tests/unit/test_sNN_<topic>.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- <adjacent work that belongs to another story or the backlog>
