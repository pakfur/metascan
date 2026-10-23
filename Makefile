# Makefile for metascan
# A media metadata scanning application

# Variables
VENV_DIR := venv
PYTHON := $(VENV_DIR)/bin/python
VENV_ACTIVATE := $(VENV_DIR)/bin/activate
PIP := $(VENV_DIR)/bin/pip
PYTEST := $(VENV_DIR)/bin/pytest
BLACK := $(VENV_DIR)/bin/black
MYPY := $(VENV_DIR)/bin/mypy --check-untyped-defs
FLAKE8 := $(VENV_DIR)/bin/flake8

# Metascan supports Python 3.11.x only (see install.sh, setup.py, CI).
PY_SERIES := 3.11
PY_CHECK := import sys; sys.exit(0 if sys.version_info[:2] == tuple(map(int, "$(PY_SERIES)".split("."))) else 1)
# Interpreter used ONLY to create a missing venv; never touches an existing one.
PYTHON_BOOTSTRAP ?= $(shell command -v python$(PY_SERIES) || command -v python3 || command -v python)

# Directories to lint/format
PY_DIRS := metascan/ backend/ tests/

# Default target
.DEFAULT_GOAL := help

# Help target
.PHONY: help
help:  ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# First-time setup
.PHONY: install
install: venv deps nltk-setup frontend-deps dev-install  ## Complete first-time setup

# Real file target: the venv is created only when it is missing, so it is never
# re-run against (and never rewrites pyvenv.cfg of) an existing environment.
$(PYTHON):
	@test -n "$(PYTHON_BOOTSTRAP)" || { echo "Error: no Python found. Metascan requires Python $(PY_SERIES).x (install it, or set PYTHON_BOOTSTRAP=/path/to/python$(PY_SERIES))." >&2; exit 1; }
	@"$(PYTHON_BOOTSTRAP)" -c '$(PY_CHECK)' || { echo "Error: $(PYTHON_BOOTSTRAP) is Python $$("$(PYTHON_BOOTSTRAP)" -c 'import platform; print(platform.python_version())'); Metascan requires Python $(PY_SERIES).x. Install it (macOS: brew install python@$(PY_SERIES)) and re-run with PYTHON_BOOTSTRAP=python$(PY_SERIES)." >&2; exit 1; }
	"$(PYTHON_BOOTSTRAP)" -m venv $(VENV_DIR) || { rm -rf $(VENV_DIR); exit 1; }
	@echo "Virtual environment created in $(VENV_DIR)"

# Ensure the venv exists (creating it only if missing) and is Python 3.11.x.
# Fails loudly rather than recreating an existing venv with the wrong Python.
.PHONY: check-venv
check-venv: $(PYTHON)
	@$(PYTHON) -c '$(PY_CHECK)' || { echo "Error: $(VENV_DIR) uses Python $$($(PYTHON) -c 'import platform; print(platform.python_version())'), but this project pins Python $(PY_SERIES).x, so the venv must be rebuilt. Either: rm -rf $(VENV_DIR) && make deps   (removes the venv's installed packages; 'make venv' alone suffices if python$(PY_SERIES) is on PATH), or keep it and use a separate 3.11 venv: make VENV_DIR=venv-311 deps   (or ./install.sh --recreate-venv)." >&2; exit 1; }

.PHONY: venv
venv: check-venv  ## Create virtual environment if missing (never recreates)
	@echo "Virtual environment ready in $(VENV_DIR) (Python $(PY_SERIES).x)"

.PHONY: deps
deps: check-venv  ## Install Python dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	$(PIP) install flake8
	@echo "Dependencies installed"

.PHONY: nltk-setup
nltk-setup: check-venv  ## Set up NLTK data and AI models
	$(PYTHON) setup_models.py
	@echo "NLTK data and AI models setup complete"

.PHONY: models
models: check-venv  ## Download AI upscaling models only
	$(PYTHON) setup_models.py
	@echo "AI models setup complete"

.PHONY: frontend-deps
frontend-deps:  ## Install frontend dependencies
	cd frontend && npm install
	@echo "Frontend dependencies installed"

.PHONY: dev-install
dev-install: check-venv  ## Install package in development mode
	$(PIP) install -e .
	@echo "Development installation complete"

# Running targets
.PHONY: serve
serve:  ## Run the FastAPI backend server
	$(PYTHON) run_server.py

.PHONY: dev
dev:  ## Run backend + frontend dev servers (use two terminals)
	@echo "Terminal 1: make serve"
	@echo "Terminal 2: cd frontend && npm run dev"

# Testing targets
.PHONY: test
test: check-venv  ## Run all Python tests
	$(PYTEST)

.PHONY: test-prompt-tokenizer
test-prompt-tokenizer: check-venv  ## Run prompt tokenizer tests
	$(PYTEST) tests/test_prompt_tokenizer.py

.PHONY: test-coverage
test-coverage: check-venv  ## Run tests with coverage report
	$(PYTEST) --cov=metascan

# Code quality targets
.PHONY: lint
lint: check-venv  ## Lint with flake8 (matches CI)
	$(FLAKE8) $(PY_DIRS) --count --select=E9,F63,F7,F82 --show-source --statistics
	$(FLAKE8) $(PY_DIRS) --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

.PHONY: format
format: check-venv  ## Format code with black
	$(BLACK) $(PY_DIRS)

.PHONY: format-check
format-check: check-venv  ## Check formatting without modifying files (matches CI)
	$(BLACK) --check $(PY_DIRS)

.PHONY: typecheck
typecheck: check-venv  ## Run type checking with mypy
	$(MYPY) metascan/

.PHONY: quality
quality: lint format-check typecheck  ## Run all Python quality checks (matches CI)

.PHONY: frontend-typecheck
frontend-typecheck:  ## Type-check the Vue frontend
	cd frontend && npx vue-tsc --noEmit

.PHONY: frontend-build
frontend-build:  ## Build the Vue frontend for production
	cd frontend && npm run build

.PHONY: quality-all
quality-all: quality frontend-typecheck  ## Run all quality checks (Python + frontend)

# Local run targets
.PHONY: start-frontend
start-frontend: check-venv
	cd frontend && npm run dev

.PHONY: start-backend
start-backend: check-venv
	$(PYTHON) run_server.py

# Debugging and analysis targets
.PHONY: analyze-metadata
analyze-metadata:  ## Analyze all metadata extraction errors
	$(PYTHON) -m metascan.utils.metadata_log_cli analyze-all

.PHONY: metadata-stats
metadata-stats:  ## Show metadata extraction statistics
	$(PYTHON) -m metascan.utils.metadata_log_cli stats

# Utility targets
.PHONY: clean
clean:  ## Clean build artifacts and cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -not -path './venv/*' -not -path './frontend/*' -exec rm -rf {} +
	find . -type f -name "*.pyc" -not -path './venv/*' -delete
	find . -type f -name "*.pyo" -not -path './venv/*' -delete

.PHONY: clean-all
clean-all: clean  ## Clean everything including virtual environment and node_modules
	rm -rf $(VENV_DIR)
	rm -rf frontend/node_modules
	rm -rf frontend/dist

.PHONY: reinstall
reinstall: clean-all install  ## Clean reinstall from scratch
