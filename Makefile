PY := .venv/bin/python
EVAL := .venv/bin/eval

.PHONY: install lint test regression demo serve docker clean-runtime
install:
	uv sync --frozen --extra dev
lint:
	.venv/bin/ruff check src tests scripts
	.venv/bin/ruff format --check src tests scripts
test:
	$(PY) -m pytest -q
regression:
	$(PY) scripts/regression.py
demo:
	$(PY) scripts/demo.py
serve:
	$(EVAL) serve --db runtime/demo.db
docker:
	docker build -t agentops-eval-platform:local .
