.PHONY: install seed run test test-vat-phase4 verify-vat-phase4 fmt lint clean demo

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

test-vat-phase4:
	uv run pytest -q \
		tests/test_formal_vat_closed_loop.py \
		tests/test_formal_vat_rebuild.py \
		tests/test_formal_vat_rebuild_closed_loop_integration.py \
		tests/test_formal_vat_completeness_review.py \
		tests/test_tax_data_policy_contract.py \
		tests/test_formal_vat_phase4_delivery_contract.py

verify-vat-phase4:
	uv run alembic upgrade head
	$(MAKE) test-vat-phase4
	uv run pytest -q

fmt:
	uv run ruff format . && uv run ruff check --fix .

lint:
	uv run ruff check .

clean:
	rm -rf .venv data/*.db __pycache__ */__pycache__ */*/__pycache__
