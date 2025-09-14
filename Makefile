# Makefile for metascan
# A media metadata scanning application

# Variables
PYTHON := python
VENV_DIR := venv
VENV_ACTIVATE := $(VENV_DIR)/bin/activate
PIP := $(VENV_DIR)/bin/pip
PYTEST := $(VENV_DIR)/bin/pytest
BLACK := $(VENV_DIR)/bin/black
MYPY := $(VENV_DIR)/bin/mypy
PYINSTALLER := $(VENV_DIR)/bin/pyinstaller

# Default target
.DEFAULT_GOAL := help

# Help target
.PHONY: help
help:  ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# First-time setup
.PHONY: install
install: venv deps nltk-setup dev-install  ## Complete first-time setup (virtual env, dependencies, NLTK data, dev install)

.PHONY: venv
venv:  ## Create virtual environment
	$(PYTHON) -m venv $(VENV_DIR)
	@echo "Virtual environment created in $(VENV_DIR)"

.PHONY: deps
deps: venv  ## Install dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "Dependencies installed"

.PHONY: nltk-setup
nltk-setup: venv  ## Set up NLTK data and AI models (required for first-time setup)
	$(PYTHON) setup_models.py
	@echo "NLTK data and AI models setup complete"

.PHONY: models
models: venv  ## Download AI upscaling models only
	$(PYTHON) setup_models.py
	@echo "AI models setup complete"

.PHONY: dev-install
dev-install: venv  ## Install package in development mode
	$(PIP) install -e .
	@echo "Development installation complete"

# Running targets
.PHONY: run
run:  ## Run the application from source
	$(PYTHON) main.py

.PHONY: run-installed
run-installed:  ## Run the installed metascan command
	$(VENV_DIR)/bin/metascan

.PHONY: run-inspect
run-inspect:  ## Run the application with PyQtInspect for debugging
	$(PYTHON) -m PyQtInspect --direct --qt-support=pyqt6 --file main.py

# Testing targets
.PHONY: test
test: venv  ## Run all tests
	$(PYTEST)

.PHONY: test-prompt-tokenizer
test-prompt-tokenizer: venv  ## Run prompt tokenizer tests
	$(PYTEST) tests/test_prompt_tokenizer.py

.PHONY: test-components
test-components: venv  ## Run component tests
	$(PYTEST) test_components.py

.PHONY: test-metadata
test-metadata: venv  ## Run metadata logging tests
	$(PYTEST) test_metadata_logging.py

.PHONY: test-coverage
test-coverage: venv  ## Run tests with coverage report
	$(PYTEST) --cov=metascan

# Code quality targets
.PHONY: format
format: venv  ## Format code with black
	$(BLACK) metascan/ tests/

.PHONY: typecheck
typecheck: venv  ## Run type checking with mypy
	$(MYPY) metascan/

.PHONY: quality
quality: format typecheck  ## Run both formatting and type checking

# Build targets
.PHONY: build
build: venv  ## Build application bundle/executable
	$(PYTHON) build_app.py

.PHONY: build-manual
build-manual: venv  ## Manual PyInstaller build
	$(PYINSTALLER) metascan.spec --clean

# Debugging and analysis targets
.PHONY: analyze-metadata
analyze-metadata:  ## Analyze all metadata extraction errors
	$(PYTHON) -m metascan.utils.metadata_log_cli analyze-all

.PHONY: metadata-stats
metadata-stats:  ## Show metadata extraction statistics
	$(PYTHON) -m metascan.utils.metadata_log_cli stats

.PHONY: validate-scan
validate-scan:  ## Run manual scan validation
	$(PYTHON) validate_scan.py

# Utility targets
.PHONY: clean
clean:  ## Clean build artifacts and cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

.PHONY: clean-all
clean-all: clean  ## Clean everything including virtual environment
	rm -rf $(VENV_DIR)

.PHONY: reinstall
reinstall: clean-all install  ## Clean reinstall from scratch

# Check if virtual environment exists
check-venv:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "Virtual environment not found. Run 'make install' first."; \
		exit 1; \
	fi

# Docker targets
.PHONY: docker-build
docker-build:  ## Build Docker images
	docker-compose build

.PHONY: docker-up
docker-up:  ## Start Docker containers
	docker-compose up -d

.PHONY: docker-down
docker-down:  ## Stop Docker containers
	docker-compose down

.PHONY: docker-logs
docker-logs:  ## View Docker container logs
	docker-compose logs -f

.PHONY: docker-clean
docker-clean:  ## Clean Docker containers and volumes
	docker-compose down -v
	docker image rm metascan-metascan metascan-sqlite metascan-upscaler 2>/dev/null || true

.PHONY: docker-shell
docker-shell:  ## Access metascan container shell
	docker exec -it metascan-app /bin/bash

.PHONY: docker-sqlite
docker-sqlite:  ## Access SQLite CLI in container
	docker exec -it metascan-sqlite sqlite3 /data/metascan.db

.PHONY: docker-rebuild
docker-rebuild: docker-down docker-clean docker-build docker-up  ## Full rebuild and restart of Docker containers

.PHONY: docker-dev
docker-dev:  ## Run Docker in development mode with live code mounting
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

# Add dependency to targets that need venv
deps nltk-setup dev-install test test-prompt-tokenizer test-components test-metadata test-coverage format typecheck build build-manual: check-venv
