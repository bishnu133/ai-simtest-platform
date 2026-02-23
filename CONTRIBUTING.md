# Contributing to AI SimTest

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/your-username/ai-simtest.git
cd ai-simtest
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# Unit tests (fast, no external deps)
PYTHONPATH=. pytest tests/test_core.py tests/test_providers.py -v

# Full suite (start mock bot first)
python -m tests.mock_bot_server &
PYTHONPATH=. pytest tests/ -v
```

## Making Changes

1. Fork the repo and create a branch: `git checkout -b feature/my-feature`
2. Make your changes
3. Add tests for new functionality
4. Run the test suite to ensure nothing breaks
5. Submit a PR with a clear description

## Areas We Need Help

- **Custom judges** — Create evaluators for specific domains (healthcare, finance, legal)
- **Persona templates** — Share persona libraries for different bot types
- **Documentation** — Tutorials, examples, translations
- **Bug reports** — Reproduce issues with clear steps
- **Performance** — Optimize parallel execution, caching, memory usage

## Code Style

- Python 3.11+ with type hints
- Pydantic v2 for data models
- Async/await for all I/O operations
- Structured logging via structlog

## Adding a Custom Judge

Extend `BaseJudge` in `src/judges/`:

```python
from src.judges import BaseJudge, JudgmentResult

class MyJudge(BaseJudge):
    async def initialize(self):
        # Load models, config, etc.
        pass

    async def evaluate(self, response, context=None, conversation_history=None, **kwargs):
        # Your evaluation logic
        return JudgmentResult(
            judge_name="my_judge",
            passed=True,
            score=0.9,
            severity="info",
            message="All good",
            evidence={}
        )
```

Register it in your `SimulationConfig.judges` list.

## Questions?

Open an issue or start a discussion. We're friendly!
