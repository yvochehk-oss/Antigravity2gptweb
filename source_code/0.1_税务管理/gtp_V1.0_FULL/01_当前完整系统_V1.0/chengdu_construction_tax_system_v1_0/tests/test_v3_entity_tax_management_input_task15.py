"""Task15 reviewed management-input PLAN/APPLY loader contracts.

All database cases use the shared ``seeded_app`` fixture, which requires a
disposable PostgreSQL database whose name contains ``test``.  The tests never
target the formal ``projectrag`` database.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine
from app.models import EntityTaxManagementInput
from app.v3_party_models import InternalEntity, Party, SourceDocument
from scripts.v3 import entity_tax_management_input as loader


PERIOD = "2026-08"
REVIEWED_AT = "2026-08-31T20:00:00+08:00"


def _suffix() -> str:
    return uuid.uuid4().hex[:8].upper()


def _manifest(
    *,
    entity: str,
    revenue: str = "100.00",
    real_cost: str = "40.00",
    reviewed_at: str = REVIEWED_AT,
    reviewed_by: str = "reviewer",
    source_document_id: int | None = None,
) -> dict:
    source = {
        "kind": loader.MANIFEST_KIND,
        "version": 1,
        "reviewed": True,
        "canonical_entity": entity,
        "tax_period": PERIOD,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "inputs": [
            {
                "input_type": "REVENUE",
                "input_version": 1,
                "amount": revenue,
                "source": "reviewed monthly management schedule",
            },
            {
                "input_type": "REAL_COST",
                "input_version": 1,
                "amount": real_cost,
                "source": "reviewed monthly management schedule",
            },
        ],
    }
    if source_document_id is not None:
        source["inputs"][0]["source_document_id"] = source_document_id
    return source


def _entity(
    session: Session,
    *,
    legal_entity: bool = True,
    active: bool = True,
    party_active: bool = True,
) -> str:
    suffix = _suffix()
    party = Party(
        code=f"T15-INP-P-{suffix}",
        name=f"Task15 input party {suffix}",
        short_name="T15",
        party_type="internal",
        active=party_active,
    )
    session.add(party)
    session.flush()
    entity = InternalEntity(
        party_id=party.id,
        canonical_code=f"T15I{suffix}",
        business_role="A",
        legal_entity=legal_entity,
        active=active,
    )
    session.add(entity)
    session.flush()
    return str(entity.canonical_code)


def _source_document(
    session: Session,
    *,
    status: str = "VALIDATED",
) -> int:
    suffix = _suffix()
    row = SourceDocument(
        source_system="TASK15_TEST",
        external_document_id=f"DOC-{suffix}",
        filename=f"review-{suffix}.pdf",
        mime_type="application/pdf",
        file_sha256=(suffix.lower() * 64)[:64],
        document_type="MANAGEMENT_SCHEDULE",
        status=status,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def _count_inputs(session: Session, *, party_id: int | None = None) -> int:
    statement = select(func.count()).select_from(EntityTaxManagementInput)
    if party_id is not None:
        statement = statement.where(EntityTaxManagementInput.reporting_party_id == party_id)
    return int(session.scalar(statement) or 0)


def _party_id(session: Session, entity: str) -> int:
    party_id = session.scalar(
        select(InternalEntity.party_id).where(InternalEntity.canonical_code == entity)
    )
    assert party_id is not None
    return int(party_id)


def test_manifest_parser_requires_exact_reviewed_shape_and_normalizes_money():
    entity = "A08"
    normalized = loader._normalize_manifest(_manifest(entity=entity, revenue="0"))
    assert normalized.canonical_entity == entity
    assert normalized.tax_period == PERIOD
    assert [item.input_type for item in normalized.inputs] == ["REAL_COST", "REVENUE"]
    assert next(item for item in normalized.inputs if item.input_type == "REVENUE").amount == Decimal("0.00")


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda p: p.update({"reviewed": False}), "reviewed"),
        (lambda p: p.update({"tax_period": "2026-8"}), "YYYY-MM"),
        (lambda p: p.update({"tax_period": "2026-02-30"}), "YYYY-MM"),
        (lambda p: p.update({"reviewed_at": "2026-08-31T20:00:00"}), "timezone"),
        (lambda p: p.update({"reviewed_by": ""}), "reviewed_by"),
        (lambda p: p.update({"canonical_entity": ""}), "canonical_entity"),
        (lambda p: p.update({"project_id": 1}), "prohibited"),
        (lambda p: p.update({"entity_code": "A08"}), "prohibited"),
        (lambda p: p.update({"legacy_tax_ledger_id": 1}), "prohibited"),
    ],
)
def test_manifest_parser_rejects_invalid_review_or_legacy_axes(mutator, message):
    payload = _manifest(entity="A08")
    mutator(payload)
    with pytest.raises(ValueError, match=message):
        loader._normalize_manifest(payload)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("input_version", 0, "positive"),
        ("input_version", True, "positive"),
        ("amount", "-0.01", "nonnegative"),
        ("amount", "1.005", "two decimal"),
        ("amount", "NaN", "finite"),
        ("source", "", "nonblank"),
        ("source_document_id", 0, "positive"),
    ],
)
def test_manifest_parser_rejects_invalid_input_values(field, value, message):
    payload = _manifest(entity="A08")
    payload["inputs"][0][field] = value
    with pytest.raises(ValueError, match=message):
        loader._normalize_manifest(payload)


def test_manifest_accepts_postgresql_numeric_18_2_maximum():
    payload = _manifest(
        entity="A08",
        revenue=str(loader.NUMERIC_18_2_MAX),
        real_cost="0.00",
    )
    normalized = loader._normalize_manifest(payload)
    revenue = next(item for item in normalized.inputs if item.input_type == "REVENUE")
    assert revenue.amount == loader.NUMERIC_18_2_MAX


@pytest.mark.parametrize(
    "amount",
    [
        str(loader.NUMERIC_18_2_MAX + Decimal("0.01")),
        "1e100",
    ],
)
def test_manifest_rejects_amounts_outside_postgresql_numeric_range_before_write(
    seeded_app, amount
):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="NUMERIC\\(18,2\\)"):
            loader.make_plan(session, _manifest(entity=entity, revenue=amount))
        session.rollback()
    with Session(engine) as session:
        party_id = _party_id(session, entity)
        assert _count_inputs(session, party_id=party_id) == 0


def test_manifest_parser_rejects_missing_duplicate_or_unknown_inputs():
    payload = _manifest(entity="A08")
    payload["inputs"] = payload["inputs"][:1]
    with pytest.raises(ValueError, match="exactly one"):
        loader._normalize_manifest(payload)

    payload = _manifest(entity="A08")
    payload["inputs"][1]["input_type"] = "REVENUE"
    with pytest.raises(ValueError, match="duplicate"):
        loader._normalize_manifest(payload)

    payload = _manifest(entity="A08")
    payload["inputs"][0]["invoice_date"] = "2026-08-31"
    with pytest.raises(ValueError, match="prohibited"):
        loader._normalize_manifest(payload)


def test_plan_is_read_only_and_contains_complete_document_and_existing_snapshot(seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        document_id = _source_document(session)
        session.commit()
    with Session(engine) as session:
        party_id = _party_id(session, entity)

    payload = _manifest(entity=entity, source_document_id=document_id)
    with Session(engine) as session:
        before = _count_inputs(session, party_id=party_id)
        plan = loader.make_plan(session, payload)
        after = _count_inputs(session, party_id=party_id)
        assert before == after == 0
        assert plan["kind"] == loader.PLAN_KIND
        assert plan["mode"] == "PLAN"
        assert plan["alembic_head"] == loader.EXPECTED_HEAD
        assert plan["disk_head"] == loader.EXPECTED_HEAD
        assert plan["reporting_party_id"]
        assert plan["tax_period"] == PERIOD
        assert plan["existing_same_version"]
        assert all(item["status"] == "MISSING" for item in plan["existing_same_version"])
        assert plan["source_snapshot"]["source_documents"][str(document_id)]["status"] == "VALIDATED"
        assert len(plan["plan_digest"]) == 64
        session.rollback()


def test_validated_source_document_is_accepted_and_nonvalidated_or_missing_fails_closed(seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        valid_id = _source_document(session, status="VALIDATED")
        received_id = _source_document(session, status="RECEIVED")
        session.commit()

    with Session(engine) as session:
        plan = loader.make_plan(session, _manifest(entity=entity, source_document_id=valid_id))
        assert str(valid_id) in plan["source_snapshot"]["source_documents"]

    with Session(engine) as session:
        with pytest.raises(ValueError, match="not current VALIDATED"):
            loader.make_plan(session, _manifest(entity=entity, source_document_id=received_id))

    with Session(engine) as session:
        with pytest.raises(ValueError, match="not available"):
            loader.make_plan(session, _manifest(entity=entity, source_document_id=999999999))


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"legal_entity": False}, "not an active legal internal entity"),
        ({"active": False}, "not an active legal internal entity"),
        ({"party_active": False}, "not an active legal internal entity"),
    ],
)
def test_entity_resolution_requires_active_legal_internal_entity(seeded_app, kwargs, message):
    with Session(engine) as session:
        entity = _entity(session, **kwargs)
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match=message):
            loader.make_plan(session, _manifest(entity=entity))


def test_apply_inserts_exactly_two_rows_atomically_and_is_idempotent(seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        party_id = _party_id(session, entity)
    payload = _manifest(entity=entity)

    with Session(engine) as session:
        plan = loader.make_plan(session, payload)
        database = loader._database_name(session)
        session.rollback()

    with Session(engine) as session:
        result = loader.apply_plan(session, plan, confirm_database=database)
        session.commit()
        assert result["kind"] == loader.RESULT_KIND
        assert result["status"] == "BUILT"
        assert len(result["inserted_ids"]) == 2
        assert _count_inputs(session, party_id=party_id) == 2

    with Session(engine) as session:
        repeat = loader.apply_plan(session, plan, confirm_database=database)
        session.commit()
        assert repeat["status"] == "NO_CHANGE"
        assert repeat["inserted_ids"] == []
        assert _count_inputs(session, party_id=party_id) == 2


def test_apply_rejects_wrong_database_confirmation_without_writes(seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        party_id = _party_id(session, entity)
    with Session(engine) as session:
        plan = loader.make_plan(session, _manifest(entity=entity))
        session.rollback()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="confirm-database"):
            loader.apply_plan(session, plan, confirm_database="another_test_database")
        session.rollback()
        assert _count_inputs(session, party_id=party_id) == 0


def test_plan_digest_tamper_is_rejected(tmp_path, seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        plan = loader.make_plan(session, _manifest(entity=entity))
    path = tmp_path / "task15-input-plan.json"
    tampered = dict(plan)
    tampered["actions"] = [dict(item) for item in plan["actions"]]
    tampered["actions"][0]["expected"] = dict(tampered["actions"][0]["expected"])
    tampered["actions"][0]["expected"]["amount"] = "999.99"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_digest"):
        loader.load_plan(path)


def test_apply_rejects_rehashed_action_expected_bypass_against_conflict(
    seeded_app,
):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        party_id = _party_id(session, entity)
        plan = loader.make_plan(session, _manifest(entity=entity))
        database = loader._database_name(session)
        session.rollback()

    with Session(engine) as session:
        session.add(
            EntityTaxManagementInput(
                reporting_party_id=party_id,
                tax_period=datetime.fromisoformat("2026-08-01").date(),
                input_type="REVENUE",
                input_version=1,
                amount=Decimal("101.00"),
                source="reviewed monthly management schedule",
                reviewed=True,
                reviewed_by="reviewer",
                reviewed_at=datetime.fromisoformat(REVIEWED_AT),
            )
        )
        session.commit()

    tampered = deepcopy(plan)
    revenue_action = next(
        action for action in tampered["actions"] if action["input_type"] == "REVENUE"
    )
    revenue_action["expected"]["amount"] = "101.00"
    tampered["plan_digest"] = loader.canonical_digest(
        {
            key: value
            for key, value in tampered.items()
            if key != "plan_digest"
        }
    )

    with Session(engine) as session:
        with pytest.raises(ValueError, match="expected row does not match manifest"):
            loader.apply_plan(session, tampered, confirm_database=database)
        session.rollback()

    with Session(engine) as session:
        assert _count_inputs(session, party_id=party_id) == 1
        assert session.scalar(
            select(func.count()).where(
                EntityTaxManagementInput.reporting_party_id == party_id,
                EntityTaxManagementInput.input_type == "REAL_COST",
            )
        ) == 0


def test_cli_rejects_non_postgresql_url_without_exposing_url(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(
        json.dumps(_manifest(entity="A08"), ensure_ascii=False), encoding="utf-8"
    )
    secret_url = "sqlite:///task15_secret_database"
    monkeypatch.setenv("DATABASE_URL", secret_url)

    assert loader.main(
        ["--manifest", str(manifest_path), "--json", str(result_path)]
    ) == 2
    rendered = result_path.read_text(encoding="utf-8")
    assert "PLAN_ERROR" in rendered
    assert "PostgreSQL-only" in rendered
    assert secret_url not in rendered
    assert secret_url not in capsys.readouterr().out


def test_plan_stale_source_document_and_same_version_conflict_fail_closed(seeded_app):
    with Session(engine) as session:
        entity = _entity(session)
        document_id = _source_document(session)
        session.commit()
    payload = _manifest(entity=entity, source_document_id=document_id)
    with Session(engine) as session:
        plan = loader.make_plan(session, payload)
        session.rollback()
    with Session(engine) as session:
        document = session.get(SourceDocument, document_id)
        assert document is not None
        document.status = "RECEIVED"
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="not current VALIDATED"):
            loader.apply_plan(session, plan, confirm_database=loader._database_name(session))
        session.rollback()

    with Session(engine) as session:
        entity2 = _entity(session)
        session.add(
            EntityTaxManagementInput(
                reporting_party_id=session.scalar(
                    select(InternalEntity.party_id).where(InternalEntity.canonical_code == entity2)
                ),
                tax_period=datetime.fromisoformat("2026-08-01").date(),
                input_type="REVENUE",
                input_version=1,
                amount=Decimal("101.00"),
                source="different reviewed source",
                reviewed=True,
                reviewed_by="other-reviewer",
                reviewed_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="conflicts"):
            loader.make_plan(session, _manifest(entity=entity2))


def test_apply_failure_rolls_back_both_input_rows(seeded_app, monkeypatch):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    with Session(engine) as session:
        party_id = _party_id(session, entity)
    with Session(engine) as session:
        plan = loader.make_plan(session, _manifest(entity=entity))
        database = loader._database_name(session)
        session.rollback()

    with Session(engine) as session:
        original_flush = session.flush
        calls = 0

        def fail_second_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected loader flush failure")
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", fail_second_flush)
        with pytest.raises(RuntimeError, match="injected"):
            loader.apply_plan(session, plan, confirm_database=database)
        session.rollback()

    with Session(engine) as session:
        assert _count_inputs(session, party_id=party_id) == 0


def test_cli_plan_and_apply_have_distinct_json_kinds(tmp_path, seeded_app, monkeypatch):
    with Session(engine) as session:
        entity = _entity(session)
        session.commit()
    manifest_path = tmp_path / "manifest.json"
    plan_path = tmp_path / "plan.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(
        json.dumps(_manifest(entity=entity), ensure_ascii=False), encoding="utf-8"
    )
    with Session(engine) as session:
        database = loader._database_name(session)

    assert loader.main(
        ["--manifest", str(manifest_path), "--json", str(plan_path)]
    ) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["kind"] == loader.PLAN_KIND

    assert loader.main(
        [
            "--plan",
            str(plan_path),
            "--apply",
            "--confirm-database",
            database,
            "--json",
            str(result_path),
        ]
    ) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["kind"] == loader.RESULT_KIND
    assert result["mode"] == "APPLY"
