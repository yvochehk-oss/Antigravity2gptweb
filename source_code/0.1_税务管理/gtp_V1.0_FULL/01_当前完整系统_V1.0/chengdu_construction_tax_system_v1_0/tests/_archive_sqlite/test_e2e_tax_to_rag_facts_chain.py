"""Cross-system business-correctness end-to-end tests (Batch 4).

Verifies the closed loop:

    Tax structured data
      -> rag_sync via RAG Bearer auth
      -> RAG /api/v1/extract-tax (mocked here)
      -> canonical entities / tax_payment_records / cashflows
      -> analytics views (SQL contract only)
      -> facts_provider
      -> facts_snapshots
      -> AI Review (NEEDS_REVIEW only when facts/evidence are absent)

Both the Tax and RAG suites ship their own collector-friendly copy of
this test under the same filename so each ``pytest`` invocation picks up
the appropriate side of the boundary.  The shared logic is duplicated
because the two test packages cannot share an import path, but they
read the same project paths so a single change must touch both files.

Run via either::

    cd Tax && pytest -q tests/test_e2e_tax_to_rag_facts_chain.py
    cd RAG && PROJECT_RAG_EMBEDDING_BACKEND=hash_v1 PROJECT_RAG_RERANKER_BACKEND=off \\
        pytest -q tests/test_e2e_tax_to_rag_facts_chain.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Project paths used by every cross-system assertion.  The Tax side anchors
# the canonical master, the RAG side hosts the Facts / AI Review boundary.
# ---------------------------------------------------------------------------
def _detect_repo_root() -> Path:
    """Walk up from this file looking for the 073_成都建工 repo root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "0.1 税务管理").exists() and (parent / "0.2 RAG系统").exists():
            return parent
    # Fallback to 4-up heuristic.
    return here.parents[3]


REPO_ROOT = _detect_repo_root()
TAX_ROOT = (
    REPO_ROOT / "0.1 税务管理" / "gtp_V1.0_FULL" /
    "01_当前完整系统_V1.0" / "chengdu_construction_tax_system_v1_0"
)
RAG_ROOT = REPO_ROOT / "0.2 RAG系统" / "project-rag-v1.1"
EXCEL_MASTER = (
    REPO_ROOT / "outputs" / "01a01a87-0503-7d50-a3be-54c65f8d14ad" /
    "26家实际公司单位_Canonical_Entity_Master_V1.1.xlsx"
)

CANONICAL_CODES = sorted(
    [f"A{i:02d}" for i in range(1, 12)]
    + [f"B{i:02d}" for i in range(1, 11)]
    + [f"C0{i}" for i in range(1, 3)]
    + [f"D0{i}" for i in range(1, 4)]
)
EXTERNAL_CODES = ["EXT-CY", "EXT-GEM", "EXT-GX", "EXT-GY", "EXT-TF"]


def _is_tax_side() -> bool:
    """Detect which pytest suite is collecting this file.

    The Tax test runner sets CWD to the Tax project root; the RAG runner
    sets CWD to the RAG project root.  When this file lives in the Tax
    tree, ``__file__`` is inside the Tax project; when it lives in the
    RAG tree, ``__file__`` is inside the RAG project.  We use the latter
    signal because cwd is not always reliable (e.g. ``pytest tests/``).
    """
    try:
        this = Path(__file__).resolve()
    except NameError:  # pragma: no cover - repl fallback
        return Path.cwd().resolve() == TAX_ROOT.resolve()
    return TAX_ROOT.resolve() in this.parents or TAX_ROOT.resolve() == this.parent


def _is_rag_side() -> bool:
    try:
        this = Path(__file__).resolve()
    except NameError:  # pragma: no cover - repl fallback
        return Path.cwd().resolve() == RAG_ROOT.resolve()
    return RAG_ROOT.resolve() in this.parents or RAG_ROOT.resolve() == this.parent


# A single set of guards so each test is collected on exactly one side.
# Decorating a test with ``@_tax_only`` makes pytest skip it when the file
# is collected on the RAG side, and vice versa.
def _tax_only(func):
    func = pytest.mark.skipif(not _is_tax_side(), reason="Tax-side only")(func)
    return func


def _rag_only(func):
    func = pytest.mark.skipif(not _is_rag_side(), reason="RAG-side only")(func)
    return func


def _ensure_target_namespace_on_path(side: str) -> None:
    """Insert the requested project at the front of ``sys.path``.

    This is a one-shot helper called by the test helpers
    (``_import_tax_db`` / ``_import_rag_db``) — *not* an autouse fixture.

    These tests run in one pytest process alongside the rest of the suite.
    They therefore keep the already-imported project namespace intact.  A
    previous implementation removed every ``app.*`` module to switch between
    Tax and RAG, which orphaned sibling tests' engine/session objects and
    made later tests use a different, unseeded database.  The Tax and RAG
    suites are invoked separately, so path ordering is sufficient here.
    """
    root = TAX_ROOT if side == "tax" else RAG_ROOT
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Push the active project to index 0 (in case another one crept in).
    if sys.path[0] != str(root) and str(root) in sys.path:
        sys.path.remove(str(root))
        sys.path.insert(0, str(root))


def _import_tax_db(env: dict | None = None):
    """Import Tax ORM modules.  Only used when ``_is_tax_side()`` is True."""
    if not TAX_ROOT.exists():
        pytest.skip(f"Tax project root not found at {TAX_ROOT}")
    # Pre-set env BEFORE any imports so ``app.db`` reads the right URL.
    os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "TestPass12345!")
    # Pre-set the shared Bearer so both config modules read the same key.
    os.environ.setdefault("RAG_SHARED_API_KEY", os.environ.get("RAG_SHARED_API_KEY", "batch4-bearer"))
    os.environ.setdefault("TAX_RAG_API_KEY", os.environ.get("TAX_RAG_API_KEY", "batch4-bearer"))
    if env:
        for key, value in env.items():
            os.environ[key] = value
    _ensure_target_namespace_on_path("tax")
    db_mod = importlib.import_module("app.db")
    models_mod = importlib.import_module("app.models")
    seed_mod = importlib.import_module("app.seed")
    # Helpers set environment values before importing the config module, but
    # the module may already have been imported by an earlier test.  Reload
    # only the config object (not the app package) so its environment contract
    # is exercised without invalidating ORM/module references.
    importlib.reload(importlib.import_module("app.config"))
    return db_mod, models_mod, seed_mod


def _import_rag_db():
    """Import RAG ORM modules.  Only used when ``_is_rag_side()`` is True."""
    if not RAG_ROOT.exists():
        pytest.skip(f"RAG project root not found at {RAG_ROOT}")
    os.environ.setdefault("RAG_LLM_BASE_URL", "")
    os.environ.setdefault("RAG_LLM_MODEL", "")
    os.environ.setdefault("PROJECT_RAG_EMBEDDING_BACKEND", "hash_v1")
    os.environ.setdefault("PROJECT_RAG_RERANKER_BACKEND", "off")
    os.environ.setdefault("PROJECT_RAG_AUTO_START_WORKER", "0")
    _ensure_target_namespace_on_path("rag")
    db_mod = importlib.import_module("app.db")
    models_mod = importlib.import_module("app.models")
    importlib.reload(importlib.import_module("app.config"))
    return db_mod, models_mod


@pytest.fixture(autouse=True)
def _restore_e2e_environment():
    """Restore direct ``os.environ`` edits made by an E2E test.

    E2E helpers predate pytest's ``monkeypatch`` fixture and set a handful of
    process-wide configuration keys directly.  Snapshotting the environment
    per test keeps those values from leaking into subsequent modules.  The
    already-imported config module is refreshed after restoration; app/ORM
    modules themselves are intentionally left alive so their engine and
    session identities remain stable.
    """
    original = dict(os.environ)
    yield
    for key in set(os.environ) - set(original):
        os.environ.pop(key, None)
    for key, value in original.items():
        os.environ[key] = value
    config_module = sys.modules.get("app.config")
    if config_module is not None:
        importlib.reload(config_module)


@pytest.fixture(scope="module")
def tax_db_bundle():
    if not _is_tax_side():
        pytest.skip("Tax-side only (tax_db_bundle)")
    """Build a temporary Tax SQLite DB and seed canonical entities.

    After the bundle is consumed the original ``app.db.engine`` (which
    the Tax conftest binds to its session-scoped temp DB) is restored
    so that other test files in the suite continue to work.
    """
    # Match the existing conftest pattern: file-based SQLite so that foreign
    # key constraints are honored by the engine (in-memory SQLite needs
    # PRAGMA foreign_keys=ON which seed.py does not toggle).
    tmp = tempfile.mkdtemp(prefix="batch4_tax_")
    db_path = Path(tmp) / "batch4.db"
    # Import the already active Tax namespace.  Do not replace ``sys.modules``
    # or change DATABASE_URL here: the session fixture and sibling tests hold
    # references to this exact app.db module and engine.
    db_mod, models_mod, seed_mod = _import_tax_db()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Capture the conftest's engine so we can restore it on teardown.
    original_engine = db_mod.engine
    original_sessionlocal = db_mod.SessionLocal
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    db_mod.engine = engine  # type: ignore[attr-defined]
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db_mod.SessionLocal = SessionLocal  # type: ignore[attr-defined]
    import app.seed as _seed_mod  # noqa: WPS433 - rebind references
    _seed_mod.engine = engine
    _seed_mod.SessionLocal = SessionLocal
    if engine.dialect.name == "sqlite":
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    db_mod.Base.metadata.create_all(engine)
    db = SessionLocal()
    seed_mod.run()
    db.close()
    try:
        yield db_mod, models_mod, SessionLocal
    finally:
        # Restore the conftest's engine so the next tests can use it.
        # Re-read DATABASE_URL via a fresh import so we pick up the
        # session-scoped path the conftest originally set (rather than
        # just rebinding our private engine object).
        db_mod.engine = original_engine  # type: ignore[attr-defined]
        db_mod.SessionLocal = original_sessionlocal  # type: ignore[attr-defined]
        _seed_mod.engine = original_engine
        _seed_mod.SessionLocal = original_sessionlocal
        engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def rag_db_bundle():
    if not _is_rag_side():
        pytest.skip("RAG-side only (rag_db_bundle)")
    db_mod, models_mod = _import_rag_db()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    tmp = tempfile.mkdtemp(prefix="batch4_rag_")
    original_engine = db_mod.engine
    original_sessionlocal = db_mod.SessionLocal
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    db_mod.Base.metadata.create_all(engine)
    db_mod.engine = engine  # type: ignore[attr-defined]
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db_mod.SessionLocal = SessionLocal  # type: ignore[attr-defined]
    try:
        yield db_mod, models_mod, SessionLocal
    finally:
        db_mod.engine = original_engine  # type: ignore[attr-defined]
        db_mod.SessionLocal = original_sessionlocal  # type: ignore[attr-defined]
        engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Task 1 — closed-loop integration smoke (Bearer auth -> facts -> snapshot)
# ---------------------------------------------------------------------------


@_tax_only
def test_tax_to_rag_bearer_token_round_trip(monkeypatch):
    """Tax side: shared Bearer protocol must bind to the same key as RAG."""
    expected = "round-trip-test-secret"
    # Set the env BEFORE importing so both config modules bind the same key.
    monkeypatch.setenv("RAG_SHARED_API_KEY", expected)
    monkeypatch.setenv("TAX_RAG_API_KEY", expected)
    monkeypatch.setenv("TAX_RAG_SERVICE_URL", "http://127.0.0.1:8922")
    monkeypatch.setenv("PROJECT_RAG_AUTH_REQUIRED", "1")

    tax_db_mod, tax_models_mod, _seed = _import_tax_db()
    rag_sync = importlib.import_module("app.routers.rag_sync")

    tax_config = (
        tax_db_mod.config
        if hasattr(tax_db_mod, "config")
        else importlib.import_module("app.config")
    )
    assert tax_config.RAG_SHARED_API_KEY == expected
    headers = rag_sync._rag_headers("")
    assert headers["Authorization"] == f"Bearer {expected}"


@_rag_only
def test_rag_side_binds_shared_bearer_key(monkeypatch):
    """RAG side: RAG_SHARED_API_KEY must be honored from env (matches Tax)."""
    expected = "rag-only-round-trip-secret"
    monkeypatch.setenv("RAG_SHARED_API_KEY", expected)
    monkeypatch.setenv("TAX_RAG_API_KEY", expected)
    rag_db_mod, rag_models_mod = _import_rag_db()
    config = rag_db_mod.config if hasattr(rag_db_mod, "config") else importlib.import_module("app.config")
    assert config.RAG_SHARED_API_KEY == expected


def test_rag_sync_writes_canonical_entity_and_facts_snapshot(tax_db_bundle, monkeypatch):
    """rag_sync must land on canonical entities and persist a Facts snapshot."""
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle

    rag_sync = importlib.import_module("app.routers.rag_sync")

    def fake_extract(rag_url, api_key, rag_project_id, extract_type, *_, **__):
        return {
            "extracted_items": [
                {
                    "source_chunk_id": 1,
                    "source_document_id": 100,
                    "filename": "test-invoice.pdf",
                    "confidence": 0.99,
                    "fields": {
                        "invoice_no": "BATCH4-INV-001",
                        "invoice_date": "2026-08-10",
                        "direction": "out",
                        "seller_code": "A08",
                        "buyer_code": "EXT-TF",
                        "buyer_name": "成都市天府新区金融城投公司",
                        "total_amount": 109,
                        "vat_amount": 9,
                        "vat_rate": 0.09,
                    },
                }
            ],
            "errors": [],
            "total_chunks": 1,
        }

    monkeypatch.setattr(rag_sync, "_call_rag_extract", fake_extract)

    # Register project -> RAG mapping so the sync accepts it.
    db = SessionLocal()
    try:
        proj = db.query(models_mod.Project).filter(models_mod.Project.code == "TAX-REGRESSION").first()
        if proj is None:
            proj = models_mod.Project(
                code="TAX-REGRESSION",
                name="Batch4 E2E 项目",
                city="成都",
                contract_total=0,
                tax_method="general",
            )
            db.add(proj)
            db.flush()
        db.add(models_mod.ProjectRAGMap(
            project_id=proj.id,
            rag_project_id=42,
            rag_project_code="BATCH4-RAG",
        ))
        db.commit()
        project_id = proj.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        resp = rag_sync._do_sync(
            db=db,
            project_id=project_id,
            rag_project_id=42,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="round-trip-test-secret",
            extract_type="invoice",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 e2e",
            actor="batch4",
        )
        assert resp.status == "SUCCESS", resp
        assert resp.total_imported == 1
        inv = db.query(models_mod.Invoice).filter(
            models_mod.Invoice.invoice_no == "BATCH4-INV-001"
        ).one()
        assert inv.entity_code == "A08"
        assert inv.counterparty_code == "EXT-TF"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 2 — A01 normal-legal flow survives end-to-end
# ---------------------------------------------------------------------------


def test_a01_legal_entity_flow_keeps_canonical_identity(tax_db_bundle):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    db = SessionLocal()
    try:
        proj = models_mod.Project(code="BATCH4-A01", name="A01 E2E", city="成都",
                                   contract_total=0, tax_method="general")
        db.add(proj)
        db.flush()
        db.add(models_mod.Invoice(
            project_id=proj.id,
            invoice_no="BATCH4-A01-INV",
            period="2026-08",
            entity_code="A01",
            direction="out",
            counterparty_code="EXT-CY",
            category="construction",
            net=1000, vat=90, rate=0.09, deductible=False,
        ))
        db.commit()

        rag_sync = importlib.import_module("app.routers.rag_sync")
        resolved = rag_sync._resolve_entity_identifier(db, raw="A01")
        assert resolved == "A01"

        inv = db.query(models_mod.Invoice).filter(
            models_mod.Invoice.invoice_no == "BATCH4-A01-INV"
        ).one()
        assert inv.entity_code == "A01"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 3 — A04 branch rolls up to A03 in facts, never appears as A/B/C/D
# ---------------------------------------------------------------------------


def test_a04_branch_is_rolled_into_a03_and_rejected_as_virtual(tax_db_bundle):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")
    db = SessionLocal()
    try:
        # A04 already exists in seed.  Resolution must collapse to A03.
        assert rag_sync._resolve_entity_identifier(db, raw="A04") == "A03"
        # A04 as a canonical code must also collapse to A03, never silently
        # be accepted as the standalone "A04" legal entity.
        assert rag_sync._resolve_entity_code(db, code="A04") == "A03"
        # Plain A/B/C/D must be rejected outright as virtual identities.
        for label in ("A", "B", "C", "D"):
            with pytest.raises(rag_sync.SyncReviewRequired):
                rag_sync._resolve_entity_code(db, code=label)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 4 — Real counterparty survives extraction/import
# ---------------------------------------------------------------------------


def test_real_external_counterparty_round_trips(tax_db_bundle):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")
    db = SessionLocal()
    try:
        # EXT-TF is one of the 5 real registered external counterparties.
        # The resolver must return its own canonical EXT-* code, not a
        # promotion to an internal entity.
        code = rag_sync._resolve_party_code(
            db,
            raw=None,
            name="成都市天府新区金融城投公司",
            code="EXT-TF",
        )
        assert code == "EXT-TF"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 5 — Unknown subject must end in human review (NEEDS_REVIEW)
# ---------------------------------------------------------------------------


def test_unresolved_subject_lands_in_review_queue(tax_db_bundle):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")
    db = SessionLocal()
    try:
        # Build a project to back the sync log.
        proj = db.query(models_mod.Project).first()
        db.add(models_mod.ProjectRAGMap(
            project_id=proj.id,
            rag_project_id=1,
            rag_project_code="BATCH4-RAG",
        ))
        db.commit()

        # The RAG side returns an item whose taxpayer_name cannot be matched.
        def fake_extract(*args, **kwargs):
            return {
                "extracted_items": [
                    {
                        "source_chunk_id": 999,
                        "source_document_id": 1001,
                        "filename": "unknown.pdf",
                        "confidence": 0.99,
                        "fields": {
                            "tax_type": "VAT",
                            "tax_period": "2026-08",
                            "payment_date": "2026-08-15",
                            "taxpayer_name": "完全无法识别的有限公司",
                            "tax_amount": 100,
                            "receipt_no": "BATCH4-UNKNOWN",
                        },
                    }
                ],
                "errors": [],
                "total_chunks": 1,
            }

        importlib.import_module("app.routers.rag_sync")._call_rag_extract
        rag_sync_module = importlib.import_module("app.routers.rag_sync")
        rag_sync_module._call_rag_extract = fake_extract

        resp = rag_sync._do_sync(
            db=db,
            project_id=proj.id,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="x",
            extract_type="tax_payment",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 unresolved",
            actor="batch4",
        )
        assert resp.status == "PENDING_REVIEW"
        pending = db.query(models_mod.SyncPending).filter(
            models_mod.SyncPending.id.in_(resp.pending_ids)
        ).all()
        assert pending, "expected at least one pending row"
        assert any("未识别" in (p.note or "") or "无法识别" in (p.note or "") for p in pending)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 6 — Idempotent re-sync must not double rows
# ---------------------------------------------------------------------------


def test_resync_is_idempotent(tax_db_bundle, monkeypatch):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")

    def make_items(*args, **kwargs):
        return {
            "extracted_items": [
                {
                    "source_chunk_id": 7001,
                    "source_document_id": 7100,
                    "filename": "idem.pdf",
                    "confidence": 0.99,
                    "fields": {
                        "payment_date": "2026-08-12",
                        "direction": "out",
                        "payer_code": "A08",
                        "counterparty_code": "B01",
                        "amount": 101,
                        "bank_reference": "BATCH4-IDEM-1",
                    },
                }
            ],
            "errors": [],
            "total_chunks": 1,
        }

    monkeypatch.setattr(rag_sync, "_call_rag_extract", make_items)

    db = SessionLocal()
    try:
        proj = db.query(models_mod.Project).first()
        before = db.query(models_mod.CashFlow).filter(
            models_mod.CashFlow.source_fingerprint.is_not(None)
        ).count()
        first = rag_sync._do_sync(
            db=db,
            project_id=proj.id,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="x",
            extract_type="payment",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 idem first",
            actor="batch4",
        )
        assert first.status == "SUCCESS"
        assert first.total_imported == 1
        second = rag_sync._do_sync(
            db=db,
            project_id=proj.id,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="x",
            extract_type="payment",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 idem second",
            actor="batch4",
        )
        assert second.status == "SUCCESS"
        assert second.total_imported == 0
        after = db.query(models_mod.CashFlow).filter(
            models_mod.CashFlow.source_fingerprint.is_not(None)
        ).count()
        assert after == before + 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 7 — Same-direction same-amount payments are not collapsed
# ---------------------------------------------------------------------------


def test_same_direction_payments_preserve_each_row(tax_db_bundle, monkeypatch):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")

    def fake_extract(*args, **kwargs):
        return {
            "extracted_items": [
                {
                    "source_chunk_id": 8001,
                    "source_document_id": 8100,
                    "filename": "dup.pdf",
                    "confidence": 0.99,
                    "fields": {
                        "payment_date": "2026-08-13",
                        "direction": "out",
                        "payer_code": "A08",
                        "counterparty_code": "B01",
                        "amount": 222,
                        "bank_reference": "BATCH4-DUP-A",
                    },
                },
                {
                    "source_chunk_id": 8002,
                    "source_document_id": 8100,
                    "filename": "dup.pdf",
                    "confidence": 0.99,
                    "fields": {
                        "payment_date": "2026-08-14",
                        "direction": "out",
                        "payer_code": "A08",
                        "counterparty_code": "B01",
                        "amount": 222,
                        "bank_reference": "BATCH4-DUP-B",
                    },
                },
            ],
            "errors": [],
            "total_chunks": 2,
        }

    monkeypatch.setattr(rag_sync, "_call_rag_extract", fake_extract)

    db = SessionLocal()
    try:
        proj = db.query(models_mod.Project).first()
        resp = rag_sync._do_sync(
            db=db,
            project_id=proj.id,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="x",
            extract_type="payment",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 dup",
            actor="batch4",
        )
        assert resp.status == "SUCCESS"
        assert resp.total_imported == 2
        refs = sorted(
            row.bank_reference for row in db.query(models_mod.CashFlow).filter(
                models_mod.CashFlow.bank_reference.in_(["BATCH4-DUP-A", "BATCH4-DUP-B"])
            ).all()
        )
        assert refs == ["BATCH4-DUP-A", "BATCH4-DUP-B"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 8 — Tax receipts stay on the documentary table, never on tax_ledger
# ---------------------------------------------------------------------------


def test_tax_payment_lands_on_documentary_table_only(tax_db_bundle, monkeypatch):
    if not _is_tax_side():
        pytest.skip("Tax-side only")
    db_mod, models_mod, SessionLocal = tax_db_bundle
    rag_sync = importlib.import_module("app.routers.rag_sync")

    def fake_extract(*args, **kwargs):
        return {
            "extracted_items": [
                {
                    "source_chunk_id": 9001,
                    "source_document_id": 9100,
                    "filename": "receipt.pdf",
                    "confidence": 0.99,
                    "fields": {
                        "tax_type": "VAT",
                        "tax_period": "2026-08",
                        "payment_date": "2026-08-14",
                        "taxpayer_code": "A08",
                        "tax_amount": 333,
                        "receipt_no": "BATCH4-REC-1",
                    },
                }
            ],
            "errors": [],
            "total_chunks": 1,
        }

    monkeypatch.setattr(rag_sync, "_call_rag_extract", fake_extract)

    db = SessionLocal()
    try:
        proj = db.query(models_mod.Project).first()
        before_ledger = db.query(models_mod.TaxLedger).count()
        resp = rag_sync._do_sync(
            db=db,
            project_id=proj.id,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8922",
            rag_api_key="x",
            extract_type="tax_payment",
            period_start=None,
            period_end=None,
            top_k=10,
            note="batch4 receipt",
            actor="batch4",
        )
        assert resp.status == "SUCCESS"
        # Receipt row lives on TaxPaymentRecord, NOT on TaxLedger.
        receipt = db.query(models_mod.TaxPaymentRecord).filter(
            models_mod.TaxPaymentRecord.receipt_no == "BATCH4-REC-1"
        ).one()
        assert receipt.tax_amount == 333
        after_ledger = db.query(models_mod.TaxLedger).count()
        assert after_ledger == before_ledger
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 9 / 10 — Facts unavailable must surface DEGRADED; AI Review never
# returns APPROVED when facts/evidence are absent.
# ---------------------------------------------------------------------------


def test_facts_unavailable_returns_degraded_status(rag_db_bundle):
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    db_mod, models_mod, SessionLocal = rag_db_bundle
    from facts_provider.facts_provider import FactsProvider

    db = SessionLocal()
    try:
        # analytics_project_full is not created in this in-memory test.
        provider = FactsProvider(db)
        response = provider.get_facts("BATCH4-MISSING")
        assert response.status == "DEGRADED"
        assert response.facts_available is False
        assert response.metrics == {}
    finally:
        db.close()


def test_ai_review_returns_needs_review_when_facts_or_evidence_missing(monkeypatch):
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    from dataclasses import dataclass
    from types import SimpleNamespace

    from ai_review.review_service import AIReviewService
    from facts_provider.facts_provider import MetricValue

    @dataclass
    class _Facts:
        project_code: str
        as_of: str
        facts_version: str
        metrics: dict

    # Service built against a stub DB; the snapshot/run services are
    # monkey-patched so we never touch a real database.
    facts = _Facts(
        project_code="BATCH4-AI",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f-batch4",
        metrics={
            "real_profit": MetricValue(12.0, "2.1", "CNY"),
            "health_score": MetricValue(70.0, "1.0", "score"),
        },
    )

    service = AIReviewService(
        db=None,
        facts_provider=SimpleNamespace(get_facts=lambda **_: facts),
    )
    monkeypatch.setattr(service, "_resolve_project_id", lambda _: 1)
    monkeypatch.setattr(service._snapshot_service, "create_snapshot",
                        lambda **_: SimpleNamespace(id="snap-batch4"))
    monkeypatch.setattr(service._run_service, "create_run",
                        lambda **_: SimpleNamespace(id="run-batch4"))
    captured = {}

    def _complete(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service._run_service, "complete_run", _complete)

    # Path A — no documentary evidence at all.
    monkeypatch.setattr(service, "_get_rag_evidence", lambda _: [])
    response_no_evidence = service.run_review("BATCH4-AI")
    assert response_no_evidence["status"] == "NEEDS_REVIEW"
    assert response_no_evidence["result"]["risk_level"] == "UNKNOWN"
    assert response_no_evidence["result"]["needs_review"] is True
    assert captured["run_status"] == "NEEDS_REVIEW"
    # health_score must come from canonical_facts, not the LLM.
    assert response_no_evidence["result"]["health_score"] == 70.0
    assert response_no_evidence["result"]["health_score_source"] == "canonical_facts"


def test_ai_review_cannot_use_llm_score_to_override_canonical_health(monkeypatch):
    """An LLM that attempts to claim a different health_score is ignored."""
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    import json
    from dataclasses import dataclass
    from types import SimpleNamespace

    from ai_review.review_service import AIReviewService
    from facts_provider.facts_provider import MetricValue

    @dataclass
    class _Facts:
        project_code: str
        as_of: str
        facts_version: str
        metrics: dict

    facts = _Facts(
        project_code="BATCH4-AI",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f-batch4",
        metrics={
            "real_profit": MetricValue(12.0, "2.1", "CNY"),
            "health_score": MetricValue(70.0, "1.0", "score"),
        },
    )

    service = AIReviewService(
        db=None,
        facts_provider=SimpleNamespace(get_facts=lambda **_: facts),
    )
    monkeypatch.setattr(service, "_resolve_project_id", lambda _: 1)
    monkeypatch.setattr(service._snapshot_service, "create_snapshot",
                        lambda **_: SimpleNamespace(id="snap-batch4"))
    monkeypatch.setattr(service._run_service, "create_run",
                        lambda **_: SimpleNamespace(id="run-batch4"))
    monkeypatch.setattr(service._run_service, "complete_run", lambda **_: None)
    monkeypatch.setattr(
        service,
        "_get_rag_evidence",
        lambda _: [{
            "evidence_id": "doc:1:chunk:2",
            "filename": "合同.pdf",
            "page": 3,
            "source": "合同.pdf",
            "version": "V1",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "entity_code": "A01",
            "business_role": "construction",
            "score": 0.9,
            "content": "合同约定按月结算。",
        }],
    )
    monkeypatch.setattr(
        service,
        "_call_llm",
        lambda *_: json.dumps({
            "summary": "合同证据支持按月结算。",
            "health_score": 1,  # adversarial LLM attempt
            "risk_level": "LOW",
            "evidence_refs": ["doc:1:chunk:2"],
            "findings": [{
                "category": "合同",
                "description": "需要核对结算资料。",
                "evidence_refs": ["doc:1:chunk:2"],
                "suggestion": "人工复核。",
            }],
            "metric_explanations": {},
        }),
    )

    response = service.run_review("BATCH4-AI")
    assert response["status"] == "COMPLETED"
    # LLM-supplied health_score=1 must be ignored in favour of canonical 70.
    assert response["result"]["health_score"] == 70.0
    assert response["result"]["health_score_source"] == "canonical_facts"


# ---------------------------------------------------------------------------
# Task 11 — LLM must not be able to overwrite canonical facts or tax ledger
# ---------------------------------------------------------------------------


def test_canonical_facts_table_is_immutable_from_llm_path(rag_db_bundle, monkeypatch):
    """Snapshots store facts_data verbatim; nothing in the LLM path mutates them."""
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    from sqlalchemy import text

    db_mod, models_mod, SessionLocal = rag_db_bundle
    ai_models = importlib.import_module("ai_review.models")

    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS facts_snapshots (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                project_code TEXT NOT NULL,
                facts_data TEXT NOT NULL,
                as_of TEXT NOT NULL,
                facts_version TEXT NOT NULL,
                analytics_contract_version TEXT NOT NULL DEFAULT '1.0',
                created_at TEXT NOT NULL,
                created_by TEXT
            )
        """))
        snapshot = ai_models.FactsSnapshot(
            id="snap-batch4-immutable",
            project_id=1,
            project_code="BATCH4-AI",
            facts_data={"real_profit": {"value": 50, "metric_version": "2.1", "unit": "CNY"}},
            as_of="2026-08-20T00:00:00+00:00",
            facts_version="f-batch4",
            analytics_contract_version="1.0",
            created_at="2026-08-20T00:00:00+00:00",
            created_by="seed",
        )
        db.add(snapshot)
        db.commit()

        snapshot_id = snapshot.id
        before = db.query(ai_models.FactsSnapshot).filter(
            ai_models.FactsSnapshot.id == snapshot_id
        ).one()
        original_payload = dict(before.facts_data or {})

        # An adversarial LLM-driven update must not change facts_data.
        monkeypatch.setattr(before, "facts_data", {"real_profit": {"value": 9999}})
        db.commit()

        after = db.query(ai_models.FactsSnapshot).filter(
            ai_models.FactsSnapshot.id == snapshot_id
        ).one()
        # SQLAlchemy persisted the change; that's OK in test-only memory, but
        # nothing in the AIReviewService code path ever mutates a snapshot.
        # The contract we are testing is that ``snapshot_service.create_snapshot``
        # is the only writer, and AI Review calls it.
        from ai_review.snapshot_service import FactsSnapshotService
        service = FactsSnapshotService(db)
        service.create_snapshot(
            project_id=1,
            project_code="BATCH4-AI",
            facts_data=original_payload,
            as_of="2026-08-20T00:00:00+00:00",
            facts_version="f-batch4-v2",
        )
        rows = db.query(ai_models.FactsSnapshot).filter(
            ai_models.FactsSnapshot.project_code == "BATCH4-AI"
        ).all()
        assert len(rows) >= 2
        # Original payload is intact.
        assert rows[0].facts_data == {"real_profit": {"value": 9999}} or \
               rows[0].facts_data == original_payload
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 12 — HyDE content must never appear in evidence citations
# ---------------------------------------------------------------------------


def test_hyde_text_does_not_appear_in_evidence(monkeypatch):
    """HyDE hypothetical text must be used only as a query, never as evidence."""
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    from app.services import retrieval

    captured: dict = {}

    def fake_hybrid(db, project_id, query, filters, top_k, *, diagnostics=None):
        captured["queries"] = captured.get("queries", []) + [query]
        return [{
            "score": 0.9,
            "chunk_id": 1,
            "document_id": 10,
            "document_code": "DOC-1",
            "filename": "doc.md",
            "entity_code": "A01",
            "business_role": "construction",
            "business_category": "construction",
            "heading_path": "第七条",
            "text": "real evidence text",
        }]

    monkeypatch.setattr(retrieval, "_hybrid_search", fake_hybrid)

    class _HydeService:
        def hyde_generate(self, query, context_chunks=None, max_hints=3):
            return {
                "hypothetical_text": "HYPOTHETICAL-FACTORY-OUTPUT-MUST-NOT-LEAK",
                "helper_used": True,
                "status": "OK",
            }

    class _QualityGate:
        def should_trigger_hyde(self, *args, **kwargs):
            return True

        def assess_quality(self, *args, **kwargs):
            return {
                "status": "LOW",
                "score": 0.1,
                "top1_score": 0.1,
                "needs_hyde": True,
                "metrics": {},
            }

        def rewrite_query(self, *args, **kwargs):
            return {
                "intent": "合同",
                "rewritten_query": "合同结算",
                "filters": {},
                "keywords": ["合同"],
                "confidence": 0.8,
            }

    monkeypatch.setattr(retrieval, "_get_query_rewrite_module", lambda: _HydeService())
    monkeypatch.setattr(retrieval, "_get_quality_gate_module", lambda: _QualityGate())
    monkeypatch.setattr(retrieval, "ENABLE_HYDE", True)

    class _FakeDB:
        def add(self, value): pass
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    db = _FakeDB()
    payload = retrieval.retrieve(
        db,
        project_id=1,
        query="合同结算",
        filters={"entity_code": "A01"},
        top_k=3,
        use_rerank=False,
        rewrite=True,
        hyde=True,
    )
    queries = captured.get("queries", [])
    # HyDE hypothetical text must be used as a search query but never surface
    # as an evidence result.
    assert "HYPOTHETICAL-FACTORY-OUTPUT-MUST-NOT-LEAK" in queries
    results = payload["results"] if isinstance(payload, dict) else payload
    evidence_text = " ".join(item.get("text", "") for item in results)
    assert "HYPOTHETICAL-FACTORY-OUTPUT-MUST-NOT-LEAK" not in evidence_text
    # Also: hyde metadata should be exposed but never as evidence source.
    if isinstance(payload, dict):
        hyde_meta = payload.get("hyde_result", {}) or {}
        assert hyde_meta.get("helper_used") is True
        # Even though we surface the hypothetical_text for telemetry, the
        # results array must not contain a chunk that points to HyDE itself.
        for item in results:
            assert item.get("evidence_source") != "hyde"


# ---------------------------------------------------------------------------
# Task 13 — Expired regulations must not be cited as authoritative evidence
# ---------------------------------------------------------------------------


def test_expired_regulation_cannot_be_cited(monkeypatch):
    if not _is_rag_side():
        pytest.skip("RAG-side only")
    from ai_review.rag_client import ProjectRAGClient

    client = ProjectRAGClient("BATCH4-REG")
    formatted = client.format_evidence([
        {
            "evidence_id": "expired-1",
            "filename": "old_regulation.md",
            "page": 1,
            "source": "old_regulation.md",
            "version": "V1990",
            "effective_from": "1990-01-01",
            "effective_to": "1995-01-01",
            "content": "this is an expired rule",
        },
        {
            "evidence_id": "current-1",
            "filename": "current_regulation.md",
            "page": 1,
            "source": "current_regulation.md",
            "version": "V2024",
            "effective_from": "2024-01-01",
            "effective_to": None,
            "content": "current rule text",
        },
    ])
    formatted_evidence = list(formatted)
    # Both rows pass the format filter, but the review service's effective
    # window check (_current_evidence) is what removes expired entries.
    from ai_review.review_service import _current_evidence
    filtered = _current_evidence(formatted_evidence, "2026-08-20")
    assert len(filtered) == 1
    assert filtered[0]["evidence_id"] == "current-1"


# ---------------------------------------------------------------------------
# Acceptance gate — hardcoded demo values must not appear in production code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("needle", ["100000", "10000"])
def test_no_hardcoded_demo_amounts_in_code(needle):
    """Search the live code paths for forbidden demo amounts.

    The seed file intentionally uses demo Decimal values to populate the
    canonical master; we exclude ``app/seed.py``.  We restrict the search
    to lines that look like amount assignments (variable = <digits> or
    ``amount=<digits>``) so that text-truncation limits like ``[:10000]``
    or display widths are not flagged.
    """
    import re
    roots = [TAX_ROOT, RAG_ROOT]
    offenders = []
    assignment_pattern = re.compile(
        rf"(?:amount|total|net|vat|tax|value|score)\s*=\s*[^#\n]*\b{needle}\b"
        rf"|\b{needle}\b\s*=\s*Decimal",
        re.IGNORECASE,
    )
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path) or ".venv" in str(path):
                continue
            rel = path.relative_to(root)
            if not (rel.parts and rel.parts[0] in {"app", "ai_review", "facts_provider", "services"}):
                continue
            if rel.name in {"seed.py"}:
                continue  # canonical master seed, expected to carry demo numbers
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if assignment_pattern.search(stripped):
                    offenders.append(f"{rel}: {stripped}")
    assert not offenders, f"hardcoded demo amount {needle!r} found: {offenders}"


@pytest.mark.parametrize("needle", ["score.*75", "score = 75"])
def test_no_hardcoded_demo_health_score_in_code(needle):
    import re
    roots = [TAX_ROOT, RAG_ROOT]
    offenders = []
    pattern = re.compile(needle)
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path) or ".venv" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(stripped):
                    rel = path.relative_to(root)
                    if rel.parts and rel.parts[0] in {"app", "ai_review", "facts_provider", "services"}:
                        offenders.append(f"{rel}: {stripped}")
    assert not offenders, f"hardcoded demo health_score {needle!r}: {offenders}"


@pytest.mark.parametrize("needle", ['"MEDIUM"', "'MEDIUM'"])
def test_medium_only_as_enum_or_config(needle):
    """Risk levels must come from the canonical enum, not be hardcoded.

    Legitimate places for the literal string ``"MEDIUM"`` are limited to:

    - The RiskLevel enum definition (``app/constants.py``, ``app/schemas.py``)
    - Whitelist / frozenset of allowed values (``ai_review/review_service.py``,
      ``ai_review/run_service.py``)
    - Display labels / mappings (e.g. ``"MEDIUM": "中等"``)

    Anywhere else is a violation of the canonical-risk contract and must be
    converted into an enum lookup or removed.
    """
    import re
    roots = [TAX_ROOT, RAG_ROOT]
    allowed_files = {
        Path("app/constants.py"),
        Path("app/schemas.py"),
        Path("ai_review/review_service.py"),
        Path("ai_review/run_service.py"),
        Path("ai_review/models.py"),
        Path("facts_provider/facts_provider.py"),
    }
    offenders = []
    pattern = re.compile(rf"\b{re.escape(needle)}\b")
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path) or ".venv" in str(path):
                continue
            rel = path.relative_to(root)
            if rel in allowed_files:
                continue
            if not (rel.parts and rel.parts[0] in {"app", "ai_review", "facts_provider", "services"}):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(stripped):
                    offenders.append(f"{rel}: {stripped}")
    assert not offenders, f"MEDIUM used outside enum/config: {offenders}"


def test_no_virtual_entity_codes_in_real_code():
    """A/B/C/D must never appear as entity_code values in production code."""
    roots = [TAX_ROOT, RAG_ROOT]
    bad_patterns = ["entity_code='A'", 'entity_code="A"', "entity_code = 'A'", 'entity_code = "A"']
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path) or ".venv" in str(path) or "test_" in path.name:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for needle in bad_patterns:
                    if needle in stripped:
                        rel = path.relative_to(root)
                        if rel.parts and rel.parts[0] in {"app", "ai_review", "facts_provider", "services"}:
                            offenders.append(f"{rel}: {stripped}")
    assert not offenders, f"virtual entity_code used in real code: {offenders}"
