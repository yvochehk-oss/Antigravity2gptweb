.PHONY: install seed run test fmt lint clean

PROJECT_RAG_PORT ?= 8922

install:
	uv sync

seed:
	uv run python -m app.seed 2>/dev/null || true

run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port $(PROJECT_RAG_PORT) --reload

test:
	uv run pytest -q

fmt:
	uv run ruff format . && uv run ruff check --fix .

lint:
	uv run ruff check .

clean:
	rm -rf .venv data/*.db __pycache__ */__pycache__ */*/__pycache__
