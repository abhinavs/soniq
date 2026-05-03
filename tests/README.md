# Soniq Test Suite Organization

This directory contains the Soniq test suite, organized into unit and integration tests for better performance and clarity.

## Structure

```
tests/
├── unit/              # Fast, isolated tests (no database)
├── integration/       # Full system tests (require database)
├── conftest.py        # Shared pytest configuration
└── db_utils.py        # Database utilities for integration tests
```

## Test Categories

### Unit Tests (`tests/unit/`)
- **Fast execution** (< 1 second each)
- **No external dependencies** (no database, no network)
- **Isolated testing** of individual components
- **Mock external dependencies** when needed

Examples:
- Configuration validation
- Core logic functions
- Utility functions
- Data model validation

### Integration Tests (`tests/integration/`)
- **Full system testing** with database
- **End-to-end scenarios**
- **Multi-component interactions**
- **Real database operations**

Examples:
- Job processing workflows
- CLI integration
- Database schema validation
- Production scenarios

## Running Tests

### Run all tests:
```bash
pytest tests/
```

### Run only unit tests (fast):
```bash
pytest tests/unit/
```

### Run only integration tests:
```bash
pytest tests/integration/
```

### Run with coverage:
```bash
pytest tests/ --cov=soniq --cov-report=html
```

## Guidelines

### When writing unit tests:
- Keep tests fast and isolated
- Mock database connections and external services
- Focus on testing business logic
- Use descriptive test names
- Each test should test one specific behavior

### When writing integration tests:
- Test real database operations
- Use the test database setup utilities
- Clean up after each test
- Test complete workflows
- Verify end-to-end functionality

### Test Database Setup
Integration tests use a dedicated test database (`soniq_test`). The `db_utils.py` module provides utilities for:
- Creating/dropping test databases
- Clearing tables between tests
- Setting up test data

### Dependencies
- All tests require: `pytest`, `pytest-asyncio`
- Integration tests require: PostgreSQL server running locally
- Some optional feature tests require additional dependencies as specified in `pyproject.toml`
