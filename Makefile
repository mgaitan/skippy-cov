.PHONY: install check test test-cov release help
export UV_MALWARE_CHECK := 1

install: ## Create the virtual environment and install prek hooks when available
	uv sync
	@if command -v prek >/dev/null 2>&1; then prek install; else echo "Install hooks with: uv tool install prek && prek install"; fi

check: ## Check the lockfile, lint, formatting and types
	uv lock --check
	uv run --locked ruff check
	uv run --locked ruff format --check
	uv run --locked ty check skippy_cov

test: ## Run the tests
	uv run --locked pytest tests -vv

test-cov: ## Run tests with coverage and pytest contexts
	uv run --locked pytest tests --cov --cov-context=test --cov-report=term-missing

release: ## Create a GitHub release for the version in pyproject.toml
	@version=$$(uv version --short); \
	gh release create "$$version" --generate-notes

help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{match[0]:<20}\033[0m {match[1]}') for match in re.findall(r'^([a-zA-Z_.-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
