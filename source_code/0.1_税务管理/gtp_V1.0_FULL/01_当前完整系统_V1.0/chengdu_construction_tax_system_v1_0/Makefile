.PHONY: install seed run test fmt lint clean demo

TAX_PORT ?= 8921

install:
	uv sync

seed:
	uv run python -m app.seed

run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port $(TAX_PORT)

demo: install seed run

test:
	uv run pytest -q

fmt:
	uv run ruff format . && uv run ruff check --fix .

lint:
	uv run ruff check .

clean:
	rm -rf .venv data/*.db __pycache__ */__pycache__ */*/__pycache__
