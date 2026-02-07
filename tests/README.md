# Aula Integration Tests

## Running Tests

### Install test dependencies:
```bash
pip install -r requirements_test.txt
```

### Run all tests:
```bash
pytest tests/
```

### Run with coverage:
```bash
pytest --cov=custom_components.aula --cov-report=html tests/
```

### Run specific test file:
```bash
pytest tests/test_config_flow.py
```

### Run with verbose output:
```bash
pytest -v tests/
```

## Test Coverage

The test suite covers:
- Config flow (adding Aula credentials)
- Sensor entity initialization
- Calendar entity initialization
- Login flow (mocked - does not use real MitID)

## Test Structure

```
tests/
├── __init__.py
├── conftest.py          # Pytest fixtures
├── const.py             # Test constants
└── README.md            # This file
```

## Notes on MitID Testing

Since MitID requires interactive approval via the MitID app, the actual login 
flow cannot be fully automated in tests. The tests use mocked Selenium drivers
to simulate successful login without requiring actual MitID interaction.
