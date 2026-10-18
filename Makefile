.PHONY: install lint fmt typecheck test cov doctest live sweep gen gen-check openapi-check check build
install:
	pip install -e ".[dev]"
lint:
	ruff check src tests scripts
	ruff format --check src tests scripts
fmt:
	ruff format src tests scripts
	ruff check --fix src tests scripts
typecheck:
	mypy src
test:
	pytest -q
cov:
	pytest --cov=takasumibot_toolkit --cov-report=term-missing
doctest:
	pytest -q --doctest-modules src -o addopts=""
# Hits the real API. Never part of `make check`; the default pytest addopts
# deselect the `live` marker.
live:
	pytest -m live -o addopts="-ra"
# Drift sweep: samples the real API and reports parse failures / unknown fields
# Exit 2 = API unreachable.
sweep:
	python scripts/live_sweep.py
gen:
	python scripts/generate_models.py
gen-check:
	python scripts/generate_models.py --check
openapi-check:
	python scripts/check_openapi_consistency.py
check: lint typecheck test doctest gen-check openapi-check
build:
	python -m build
	twine check dist/*
