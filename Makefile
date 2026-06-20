.PHONY: init test lint run

# Install runtime + dev deps and the package itself (editable, flat layout).
init:
	pip install -r requirements.txt
	pip install -e .[dev]

# Run the test suite (pytest reads testpaths=tests from pyproject).
test:
	pytest tests

# Static checks: style/lint (ruff), types (mypy), and the purity import contract.
lint:
	ruff check orb_bot tests
	mypy orb_bot
	lint-imports

# Launch the bot: loads config, builds deps, runs the orchestrator.
run:
	python -m orb_bot
