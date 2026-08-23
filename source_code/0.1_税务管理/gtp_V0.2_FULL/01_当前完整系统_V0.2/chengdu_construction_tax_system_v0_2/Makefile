.PHONY: install seed run test fmt lint clean demo

PY ?= python3

install:
	$(PY) -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

seed:
	. .venv/bin/activate && DATABASE_URL=sqlite:///./data/demo.db python -m app.seed

run:
	. .venv/bin/activate && DATABASE_URL=sqlite:///./data/demo.db uvicorn app.main:app --host 127.0.0.1 --port 8765

demo: install seed run

test:
	. .venv/bin/activate && DATABASE_URL=sqlite:///./data/v02_test.db python -m pytest -q

fmt:
	. .venv/bin/activate && ruff format . && ruff check --fix .

lint:
	. .venv/bin/activate && ruff check .

clean:
	rm -rf .venv data/*.db __pycache__ */__pycache__ */*/__pycache__