PYTHON ?= python3
VENV_BIN ?= .venv/bin

BLACK := $(VENV_BIN)/black
ISORT := $(VENV_BIN)/isort
FLAKE8 := $(VENV_BIN)/flake8
MYPY := $(VENV_BIN)/mypy
PYTEST := $(VENV_BIN)/pytest

CARGO ?= cargo
RUST_BINS := dispatch dmcp contextor
INSTALL_BIN_DIR ?= $(HOME)/.local/bin

.PHONY: help check fix test lint format typecheck rust rust-install

help:
	@echo "Available targets:"
	@echo "  make check       - run formatting, lint, typecheck, and tests (CI baseline)"
	@echo "  make fix         - auto-format code and sort imports"
	@echo "  make test        - run deterministic local test subset"
	@echo "  make lint        - run critical flake8 checks"
	@echo "  make format      - verify formatting with black/isort"
	@echo "  make typecheck   - run incremental mypy check"
	@echo "  make rust        - build all Rust binaries (dispatch, dmcp, contextor)"
	@echo "  make rust-install - build + install Rust binaries to $(INSTALL_BIN_DIR)"

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

rust:
	@echo "Building Rust binaries..."
	@for bin in $(RUST_BINS); do \
		if [ -d "deps/rust/$$bin" ]; then \
			echo "  Building $$bin..."; \
			$(CARGO) build --release --manifest-path deps/rust/$$bin/Cargo.toml; \
		else \
			echo "  Skipping $$bin (deps/rust/$$bin not found)"; \
		fi; \
	done
	@echo "Done. Binaries are in deps/rust/<name>/target/release/"
	@echo "Run 'make rust-install' to copy them to $(INSTALL_BIN_DIR)"

rust-install: rust
	@mkdir -p $(INSTALL_BIN_DIR)
	@for bin in $(RUST_BINS); do \
		src="deps/rust/$$bin/target/release/$$bin"; \
		if [ -f "$$src" ]; then \
			cp "$$src" $(INSTALL_BIN_DIR)/$$bin; \
			echo "  Installed: $(INSTALL_BIN_DIR)/$$bin"; \
		fi; \
	done
	@echo "Ensure $(INSTALL_BIN_DIR) is in your PATH."
