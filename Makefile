PYTHON ?= python3
VENV_BIN ?= .venv/bin

BLACK := $(VENV_BIN)/black
ISORT := $(VENV_BIN)/isort
FLAKE8 := $(VENV_BIN)/flake8
MYPY := $(VENV_BIN)/mypy
PYTEST := $(VENV_BIN)/pytest

.PHONY: help check fix test lint format typecheck

help:
	@echo "Available targets:"
	@echo "  make check     - run formatting, lint, typecheck, and tests (CI baseline)"
	@echo "  make fix       - auto-format code and sort imports"
	@echo "  make test      - run deterministic local test subset"
	@echo "  make lint      - run critical flake8 checks"
	@echo "  make format    - verify formatting with black/isort"
	@echo "  make typecheck - run incremental mypy check"

check: format lint typecheck test

fix:
	$(BLACK) .
	$(ISORT) .

test:
	$(PYTEST) -m "not integration and not manual and not slow" -k "not voice_activation"

lint:
	$(FLAKE8) . --count --select=E9,F63,F7,F82 --show-source --statistics --exclude .venv

format:
	$(BLACK) --check .
	$(ISORT) --check-only .

typecheck:
	$(MYPY) --config-file /dev/null --follow-imports=skip --ignore-missing-imports jarvis/tui/slash_commands_doc.py
