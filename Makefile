
.PHONY: lint format build publish

lint:
	mypy --version
	@mypy src/cronico
	ruff --version
	@ruff check src/cronico

lint-fix:
	mypy --version
	@mypy src/cronico
	ruff --version
	@ruff check --fix src/cronico

format:
	ruff --version
	@ruff check --select I --fix src/cronico

build:
	python -m build

publish:
	twine upload dist/*

clean:
	rm -rf build dist cronico.egg-info
	find . -name '__pycache__' -type d -exec rm -rf {} +
	find . -name '*.pyc' -type f -exec rm -f {} +
