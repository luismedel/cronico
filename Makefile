
.PHONY: lint format build publish

lint:
	@ruff --version
	@ruff check src/cronico
	@mypy --version
	@mypy --python-version 3.12 src/cronico
	@mypy --python-version 3.13 src/cronico

lint-fix:
	@ruff --version
	@ruff check --fix src/cronico

format:
	@ruff --version
	@ruff check --select I --fix src/cronico
	@ruff format --line-length=120 src/cronico

build:
	python -m build

publish:
	twine upload dist/*

clean:
	rm -rf build dist cronico.egg-info
	find . -name '__pycache__' -type d -exec rm -rf {} +
	find . -name '*.pyc' -type f -exec rm -f {} +
