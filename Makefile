# Makefile for Execution Engine

.PHONY: help install test test-unit test-integration lint format type-check check-env gen-secret clean docker-build docker-run run dev pre-commit service-install service-status service-logs service-restart service-stop service-remove backup

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the package in development mode
	pip install -e .[dev]

test: ## Run the test suite
	pytest

test-unit: ## Run unit tests only
	pytest tests/unit/

test-integration: ## Run integration tests only
	pytest tests/integration/

lint: ## Lint code with ruff
	ruff check src/ tests/

format: ## Format code with ruff
	ruff format src/ tests/

type-check: ## Run mypy type checking
	mypy src/

check-env: ## Validate environment configuration
	python scripts/check_env.py

gen-secret: ## Generate WebSocket secret key
	python scripts/gen_secret.py

clean: ## Clean up build artifacts
	rm -rf build/ dist/ *.egg-info .coverage htmlcov/ .pytest_cache/

docker-build: ## Build Docker image
	docker build -t execution-engine .

docker-run: ## Run Docker container
	docker run --env-file .env -p 8080:8080 execution-engine

run: ## Run the application
	execution-engine

dev: ## Run in development mode with debug logging
	LOG_LEVEL=DEBUG execution-engine

pre-commit: ## Run pre-commit hooks
	pre-commit run --all-files

# Windows Background Task (Task Scheduler - runs headless, no GUI)
service-install: ## Register the AQ Agent scheduled task (run at logon)
	powershell -ExecutionPolicy Bypass -File install.ps1

service-status: ## Check scheduled task status
	powershell -Command "Get-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\' | Select-Object TaskName, State"

service-logs: ## Tail the most recent log file (checks ProgramData, falls back to ./logs)
	powershell -File scripts/tail_logs.ps1

service-restart: ## Restart the scheduled task
	powershell -Command "Stop-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'; Start-Sleep 2; Start-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'"

service-stop: ## Stop the scheduled task
	powershell -Command "Stop-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'"

service-remove: ## Unregister the scheduled task
	powershell -ExecutionPolicy Bypass -File install.ps1 uninstall

# Backup & Recovery
backup: ## Backup database and configuration
	powershell -File scripts/backup.ps1

# Default target
.DEFAULT_GOAL := help