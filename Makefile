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

.PHONY: venv
venv:  ## Create virtual environment
	$(shell which python3 || which python) -m venv $(VENV_DIR)
	@echo "Virtual environment created in $(VENV_DIR)"

.PHONY: deps
deps: venv  ## Install Python dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	$(PIP) install flake8
	@echo "Dependencies installed"

.PHONY: nltk-setup
nltk-setup: venv  ## Set up NLTK data and AI models
	$(PYTHON) setup_models.py
	@echo "NLTK data and AI models setup complete"

.PHONY: models
models: venv  ## Download AI upscaling models only
	$(PYTHON) setup_models.py
	@echo "AI models setup complete"

.PHONY: frontend-deps
frontend-deps:  ## Install frontend dependencies
	cd frontend && npm install
	@echo "Frontend dependencies installed"

.PHONY: dev-install
dev-install: venv  ## Install package in development mode
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
test: venv  ## Run all Python tests
	$(PYTEST)

.PHONY: test-prompt-tokenizer
test-prompt-tokenizer: venv  ## Run prompt tokenizer tests
	$(PYTEST) tests/test_prompt_tokenizer.py

.PHONY: test-coverage
test-coverage: venv  ## Run tests with coverage report
	$(PYTEST) --cov=metascan

# Code quality targets
.PHONY: lint
lint: venv  ## Lint with flake8 (matches CI)
	$(FLAKE8) $(PY_DIRS) --count --select=E9,F63,F7,F82 --show-source --statistics
	$(FLAKE8) $(PY_DIRS) --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

.PHONY: format
format: venv  ## Format code with black
	$(BLACK) $(PY_DIRS)

.PHONY: format-check
format-check: venv  ## Check formatting without modifying files (matches CI)
	$(BLACK) --check $(PY_DIRS)

.PHONY: typecheck
typecheck: venv  ## Run type checking with mypy
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
start-frontend:  venv
	cd frontend && npm run dev

.PHONY: start-backend
start-backend: venv
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

# Check if virtual environment exists
check-venv:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "Virtual environment not found. Run 'make install' first."; \
		exit 1; \
	fi

# Add dependency to targets that need venv
deps nltk-setup dev-install test test-prompt-tokenizer test-coverage lint format format-check typecheck: check-venv
