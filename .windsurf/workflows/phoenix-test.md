---
description: Run PHOENIX test suites (unit, RL, e2e, integration)
---
You MUST execute the following command and report the results. Do NOT just print the command.

Set the environment and run tests:
```powershell
$env:PYTHONPATH = "."; python -m pytest tests/ -v --tb=short
```

If the user asked for a specific suite:
- "unit tests": `$env:PYTHONPATH = "."; python -m pytest tests/unit/ -v --tb=short`
- "rl tests": `$env:PYTHONPATH = "."; python -m pytest tests/rl/ -v --tb=short`
- "e2e tests": `$env:PYTHONPATH = "."; python -m pytest tests/e2e/ -v --tb=short`
- "integration tests": `$env:PYTHONPATH = "."; python -m pytest tests/integration/ -v --tb=short`
- Specific file: `$env:PYTHONPATH = "."; python -m pytest <filepath> -v --tb=short`

Report the pass/fail count and any failures.
