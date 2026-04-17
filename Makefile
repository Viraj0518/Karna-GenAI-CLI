.PHONY: install test test-cov lint format clean build run audit

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

test-cov:
	python -m pytest tests/ --cov=karna --cov-report=term-missing

lint:
	python -m ruff check karna tests

format:
	python -m ruff format karna tests

clean:
	python -c "import shutil, os; [shutil.rmtree(d, ignore_errors=True) for d in ['build', 'dist'] + [x for x in os.listdir('.') if x.endswith('.egg-info')]]"

build:
	python -m build

run:
	python -m karna

audit:
	python -m pip_audit
	python -m bandit -r karna -ll
