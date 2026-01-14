# TITAN Testing Suite

Comprehensive testing for the TITAN betting system.

## 📁 Test Structure

```
tests/
├── unit/              # Unit tests
├── integration/       # Integration tests
├── e2e/              # End-to-end tests  
└── fixtures/         # Test data fixtures
```

## 🧪 Test Categories

### Unit Tests (`unit/`)
- **`test_strategies.py`** - Betting strategy tests
- **`test_quality_gates.py`** - Quality gate validation
- **`integration_test.py`** - Component integration tests

### End-to-End Tests (`e2e/`)
- **`playwright.config.ts`** - Playwright configuration
- Browser automation tests
- Full system workflow tests

### Fixtures (`fixtures/`)
- Sample match data
- Mock odds history
- Test signal payloads

## 🚀 Running Tests

### All Tests
```bash
pytest tests/
```

### Specific Test File
```bash
pytest tests/unit/test_strategies.py
```

### With Coverage
```bash
pytest tests/ --cov=backend --cov=cortex --cov=scraper
```

### Verbose Output
```bash
pytest tests/ -v
```

### E2E Tests (Playwright)
```bash
cd frontend
npx playwright test
```

## 📊 Test Coverage

Current coverage targets:
- **Strategies:** 80%+
- **Quality Gates:** 90%+
- **Backend API:** 75%+
- **Scraper:** 70%+

## 🔧 Test Configuration

### pytest.ini (in pyproject.toml)
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
```

### Playwright Config
See `tests/e2e/playwright.config.ts`

## 🧩 Writing Tests

### Unit Test Example
```python
# tests/unit/test_example.py
import pytest
from cortex.strategies import PanicReboundStrategy

def test_panic_rebound_signal_generation():
    strategy = PanicReboundStrategy()
    # Test logic here
    assert strategy.name == "panic_rebound"
```

### Integration Test Example
```python
# tests/integration/test_example.py
import pytest
from backend.main import app
from fastapi.testclient import TestClient

def test_api_health_endpoint():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
```

## 🎯 Test Priorities

### High Priority
1. Strategy signal generation
2. Quality gate validation
3. Backend API endpoints
4. Database operations

### Medium Priority
1. Scraper parsing logic
2. Redis pub/sub
3. WebSocket connections
4. Error handling

### Low Priority
1. UI components
2. Logging
3. Utility functions

## 🐛 Debugging Tests

### Run with pdb
```bash
pytest tests/unit/test_strategies.py --pdb
```

### Show print statements
```bash
pytest tests/ -s
```

### Stop on first failure
```bash
pytest tests/ -x
```

## 📝 Test Requirements

**Python Tests:**
- pytest
- pytest-asyncio
- pytest-cov
- fakeredis (for mocking Redis)

**E2E Tests:**
- playwright
- @playwright/test

Install test dependencies:
```bash
pip install pytest pytest-asyncio pytest-cov fakeredis
playwright install
```

## 🔍 Continuous Integration

Tests are run automatically on:
- Pull requests
- Commits to main branch
- Pre-deployment

See `.github/workflows/` for CI configuration (if configured).

## 📚 Related Documentation

- [docs/user-guides/MANUAL_TESTING.md](../docs/user-guides/MANUAL_TESTING.md) - Manual testing procedures
- [Project Cleanup Plan](../PROJECT_CLEANUP_PLAN.md) - Testing reorganization

## 🎯 Coverage Goals

| Component | Target | Current |
|-----------|--------|---------|
| Strategies | 80% | TBD |
| Quality Gates | 90% | TBD |
| Backend API | 75% | TBD |
| Scraper | 70% | TBD |

Run `pytest --cov` to generate coverage reports.

