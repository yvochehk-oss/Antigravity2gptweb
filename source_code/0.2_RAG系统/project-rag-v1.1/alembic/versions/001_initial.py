"""Initial schema - all models from app.models + ai_review.models.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-20

This migration is idempotent - it can be run on databases that were
created with Base.metadata.create_all() and will skip existing tables/indexes.
"""

import os
import re
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# These tables are physically owned and migrated by Tax first.
# RAG baseline must not validate them against its historical 001 schema.
_TAX_OWNED_SHARED_TABLES = frozenset(
    {
        "projects",
        "entities",
        "external_parties",
        "facts_snapshots",
    }
)


def _type_signature(value: object) -> str:
    """Return a stable, dialect-neutral signature for reflected columns."""
    # SQLAlchemy reflects ``String`` as the dialect-specific ``VARCHAR``;
    # their rendered SQL is the compatibility signal we need here.
    return str(value).upper()


def _is_forward_converged_timestamp(
    bind,
    column_name: str,
    declared_type: object,
    reflected_type: object,
) -> bool:
    """Accept the intentional 019 forward migration of legacy *_at strings.

    001 is a historical baseline. PostgreSQL databases may already contain
    columns converged by Tax 71 / RAG 019 from VARCHAR to TIMESTAMPTZ.
    Treat that as a newer compatible schema, not drift.
    """
    return (
        bind.dialect.name == "postgresql"
        and column_name.endswith("_at")
        and isinstance(declared_type, sa.String)
        and isinstance(reflected_type, sa.DateTime)
    )


def _sql_signature(value: object) -> str:
    """Normalize reflected/declaration SQL enough for dialect whitespace."""
    text = " ".join(str(value).strip().split()).lower()
    return re.sub(r"\s*,\s*", ",", text)


def _assert_existing_table_compatible(table_name: str, declarations: tuple[object, ...]) -> None:
    """Validate an existing table instead of swallowing schema errors.

    ``001_initial`` is a baseline migration and may encounter a database
    created earlier by ``Base.metadata.create_all``.  Existing tables are
    accepted only when their columns and essential column properties match the
    revision.  A partially-created or drifted table must fail loudly so that
    the SQLite backup in ``alembic/env.py`` can restore the database.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    actual = {column["name"]: column for column in inspector.get_columns(table_name)}
    primary_key_columns = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
    declared = {column.name: column for column in declarations if isinstance(column, sa.Column)}
    declared_primary_key_columns = {
        str(column.name if isinstance(column, sa.Column) else column)
        for declaration in declarations
        if isinstance(declaration, sa.PrimaryKeyConstraint)
        for column in (list(declaration.columns) or list(getattr(declaration, "_pending_colargs", ())))
    }
    if set(actual) != set(declared):
        raise RuntimeError(
            f"existing table {table_name!r} is incompatible: "
            f"columns actual={sorted(actual)}, expected={sorted(declared)}"
        )
    for name, column in declared.items():
        reflected = actual[name]
        forward_timestamp = _is_forward_converged_timestamp(
            bind,
            name,
            column.type,
            reflected["type"],
        )

        if (
            _type_signature(reflected["type"]) != _type_signature(column.type)
            and not forward_timestamp
        ):
            raise RuntimeError(
                f"existing table {table_name!r} column {name!r} has incompatible type: "
                f"actual={reflected['type']!s}, expected={column.type!s}"
            )
        if (
            bool(reflected.get("nullable", True)) != bool(column.nullable)
            and not forward_timestamp
        ):
            raise RuntimeError(f"existing table {table_name!r} column {name!r} has incompatible nullability")
        if (name in primary_key_columns) != (name in declared_primary_key_columns or bool(column.primary_key)):
            raise RuntimeError(f"existing table {table_name!r} column {name!r} has incompatible primary-key state")

    # Existing tables are accepted only when their declared integrity
    # constraints are present as well.  Constraint names can be rewritten by
    # SQLite (for example, a unique constraint may surface as an auto-index),
    # so compare the semantics: constrained columns, check SQL, and FK
    # targets.  A missing constraint is schema drift and must fail closed.
    expected_unique: set[tuple[str, ...]] = set()
    expected_checks: set[str] = set()
    expected_foreign_keys: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()
    for declaration in declarations:
        if isinstance(declaration, sa.UniqueConstraint):
            names = tuple(
                str(column.name if isinstance(column, sa.Column) else column)
                for column in (list(declaration.columns) or list(getattr(declaration, "_pending_colargs", ())))
            )
            expected_unique.add(names)
        elif isinstance(declaration, sa.CheckConstraint):
            expected_checks.add(_sql_signature(declaration.sqltext))
        elif isinstance(declaration, sa.ForeignKeyConstraint):
            local = tuple(str(name) for name in declaration.column_keys)
            targets = tuple(str(element.target_fullname) for element in declaration.elements)
            # Composite foreign keys have one target table and one target
            # column per element.  Keep the table/columns as one stable tuple.
            target_table = targets[0].split(".")[-2] if targets and "." in targets[0] else ""
            target_columns = tuple(target.rsplit(".", 1)[-1] for target in targets)
            expected_foreign_keys.add((local, target_table, target_columns))

    if expected_unique:
        actual_unique: set[tuple[str, ...]] = set()
        for constraint in inspector.get_unique_constraints(table_name):
            actual_unique.add(tuple(str(name) for name in constraint.get("column_names") or ()))
        # SQLite often exposes a model's ``unique=True, index=True`` as a
        # unique index rather than a named table constraint.
        for index in inspector.get_indexes(table_name):
            if index.get("unique"):
                actual_unique.add(tuple(str(name) for name in index.get("column_names") or ()))
        missing = expected_unique - actual_unique
        if missing:
            raise RuntimeError(f"existing table {table_name!r} is missing unique constraints: {sorted(missing)}")

    if expected_checks:
        actual_checks = {
            _sql_signature(check.get("sqltext", "")) for check in inspector.get_check_constraints(table_name)
        }
        missing = expected_checks - actual_checks
        if missing:
            raise RuntimeError(f"existing table {table_name!r} is missing check constraints: {sorted(missing)}")

    if expected_foreign_keys:
        actual_foreign_keys: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()
        for fk in inspector.get_foreign_keys(table_name):
            local = tuple(str(name) for name in fk.get("constrained_columns") or ())
            target_table = str(fk.get("referred_table") or "")
            target_columns = tuple(str(name) for name in fk.get("referred_columns") or ())
            actual_foreign_keys.add((local, target_table, target_columns))
        missing = expected_foreign_keys - actual_foreign_keys
        if missing:
            raise RuntimeError(f"existing table {table_name!r} is missing foreign keys: {sorted(missing)}")


def _assert_existing_index_compatible(index_name: str, table_name: str, columns: list[str], unique: bool) -> bool:
    """Ensure existing indexes match declarations before skipping creation."""
    inspector = sa_inspect(op.get_bind())
    for existing in inspector.get_indexes(table_name):
        if existing["name"] != index_name:
            continue
        actual_columns = list(existing.get("column_names") or [])
        actual_unique = bool(
            existing.get("unique", True if existing in inspector.get_unique_constraints(table_name) else False)
        )
        if actual_columns != list(columns) or actual_unique != bool(unique):
            raise RuntimeError(
                f"existing index {index_name!r} on {table_name!r} is incompatible: "
                f"actual=({actual_columns}, unique={actual_unique}), "
                f"expected=({list(columns)}, unique={bool(unique)})"
            )
        return True
    return False


def _create_if_compatible(op_func, *args, **kwargs):
    """Create schema objects, skipping only proven-compatible duplicates.

    The previous implementation caught every exception.  That made malformed
    tables, SQL errors, and unsupported DDL look like a successful migration.
    All non-duplicate errors now propagate to Alembic's recovery boundary.
    """
    operation = getattr(op_func, "__name__", "")
    if operation == "create_table":
        table_name = str(args[0])
        inspector = sa_inspect(op.get_bind())
        if table_name in _TAX_OWNED_SHARED_TABLES:
            if table_name not in inspector.get_table_names():
                raise RuntimeError(f"shared table {table_name!r} is Tax-owned; run Tax migrations before RAG")
            return
        if table_name in inspector.get_table_names():
            _assert_existing_table_compatible(table_name, tuple(args[1:]))
            return
    elif operation == "create_index":
        index_name = str(args[0])
        table_name = str(args[1])
        columns = list(args[2]) if len(args) > 2 else list(kwargs.get("columns", ()))
        unique = bool(kwargs.get("unique", False))
        if table_name in _TAX_OWNED_SHARED_TABLES:
            inspector = sa_inspect(op.get_bind())
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            if not set(columns) <= existing_columns:
                # Tax owns the shared base table. RAG-specific columns/indexes
                # are added in 004 after this baseline revision.
                return
        if _assert_existing_index_compatible(index_name, table_name, columns, unique):
            return
    return op_func(*args, **kwargs)


def upgrade() -> None:
    # Projects
    _create_if_compatible(
        op.create_table,
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("external_system", sa.String(80), nullable=False),
        sa.Column("external_project_id", sa.String(120), nullable=False),
        sa.Column("entity_code", sa.String(16), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_code", name="uq_projects_project_code"),
        sa.CheckConstraint(
            "entity_code IS NULL OR entity_code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',"
            "'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',"
            "'C01','C02','D01','D02','D03')",
            name="ck_projects_real_entity_code",
        ),
    )
    _create_if_compatible(op.create_index, "ix_projects_entity_code", "projects", ["entity_code"])
    _create_if_compatible(op.create_index, "ix_projects_project_code", "projects", ["project_code"], unique=True)
    _create_if_compatible(op.create_index, "ix_projects_status", "projects", ["status"])

    # Entities
    _create_if_compatible(
        op.create_table,
        "entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_code", sa.String(16), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("industry", sa.String(32), nullable=False),
        sa.Column("business_role", sa.String(32), nullable=False),
        sa.Column("entity_kind", sa.String(24), nullable=False),
        sa.Column("legal_entity", sa.Boolean(), nullable=False),
        sa.Column("parent_entity_code", sa.String(16), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("legal_representative", sa.String(80), nullable=False),
        sa.Column("legal_rep_id", sa.String(32), nullable=False),
        sa.Column("legal_rep_phone", sa.String(32), nullable=False),
        sa.Column("shareholders", sa.Text(), nullable=False),
        sa.Column("supervisor", sa.String(80), nullable=False),
        sa.Column("finance_officer", sa.String(80), nullable=False),
        sa.Column("registered_capital", sa.String(40), nullable=False),
        sa.Column("establishment_date", sa.String(20), nullable=False),
        sa.Column("acquisition_date", sa.String(20), nullable=False),
        sa.Column("registration_authority", sa.String(120), nullable=False),
        sa.Column("registration_number", sa.String(40), nullable=False),
        sa.Column("unified_social_credit_code", sa.String(40), nullable=False),
        sa.Column("tax_id", sa.String(40), nullable=True),
        sa.Column("business_scope", sa.Text(), nullable=False),
        sa.Column("contributed_legal", sa.Float(), nullable=False),
        sa.Column("contributed_shareholder", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("data_as_of", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_code", name="uq_entities_entity_code"),
        sa.UniqueConstraint("tax_id", name="uq_entities_tax_id"),
        sa.CheckConstraint(
            "entity_code IS NULL OR entity_code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',"
            "'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',"
            "'C01','C02','D01','D02','D03')",
            name="ck_entities_real_entity_code",
        ),
        sa.CheckConstraint(
            "entity_code IS NULL OR entity_code != 'A04' OR (legal_entity = 0 AND parent_entity_code = 'A03')",
            name="ck_entities_a04_branch_parent",
        ),
    )
    _create_if_compatible(op.create_index, "ix_entities_code_status", "entities", ["entity_code", "status"])
    _create_if_compatible(op.create_index, "ix_entities_entity_code", "entities", ["entity_code"])
    _create_if_compatible(op.create_index, "ix_entities_entity_type", "entities", ["entity_type"])
    _create_if_compatible(op.create_index, "ix_entities_entity_kind", "entities", ["entity_kind"])
    _create_if_compatible(op.create_index, "ix_entities_status", "entities", ["status"])
    _create_if_compatible(op.create_index, "ix_entities_name", "entities", ["name"])
    _create_if_compatible(op.create_index, "ix_entities_industry", "entities", ["industry"])
    _create_if_compatible(op.create_index, "ix_entities_legal_entity", "entities", ["legal_entity"])
    _create_if_compatible(op.create_index, "ix_entities_tax_id", "entities", ["tax_id"])
    _create_if_compatible(op.create_index, "ix_entities_short_name", "entities", ["short_name"])
    _create_if_compatible(op.create_index, "ix_entities_business_role", "entities", ["business_role"])
    _create_if_compatible(op.create_index, "ix_entities_parent_entity_code", "entities", ["parent_entity_code"])

    # Documents
    _create_if_compatible(
        op.create_table,
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_code", sa.String(80), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("file_type", sa.String(24), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("original_path", sa.String(600), nullable=False),
        sa.Column("parsed_dir", sa.String(600), nullable=False),
        sa.Column("markdown_path", sa.String(600), nullable=False),
        sa.Column("content_list_path", sa.String(600), nullable=False),
        sa.Column("parse_status", sa.String(32), nullable=False),
        sa.Column("parse_message", sa.Text(), nullable=False),
        sa.Column("parse_attempts", sa.Integer(), nullable=False),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column("document_type", sa.String(60), nullable=False),
        sa.Column("entity_code", sa.String(16), nullable=False),
        sa.Column("counterparty_code", sa.String(16), nullable=False),
        sa.Column("business_category", sa.String(40), nullable=False),
        sa.Column("tax_category", sa.String(32), nullable=False),
        sa.Column("tax_vat_rate", sa.Float(), nullable=False),
        sa.Column("tax_vat_input", sa.Float(), nullable=False),
        sa.Column("tax_vat_output", sa.Float(), nullable=False),
        sa.Column("tax_vat_paid", sa.Float(), nullable=False),
        sa.Column("tax_income_rate", sa.Float(), nullable=False),
        sa.Column("tax_income_amount", sa.Float(), nullable=False),
        sa.Column("tax_income_paid", sa.Float(), nullable=False),
        sa.Column("tax_individual_rate", sa.Float(), nullable=False),
        sa.Column("tax_individual_amount", sa.Float(), nullable=False),
        sa.Column("tax_individual_paid", sa.Float(), nullable=False),
        sa.Column("tax_surtax_urban", sa.Float(), nullable=False),
        sa.Column("tax_surtax_edu", sa.Float(), nullable=False),
        sa.Column("tax_surtax_local_edu", sa.Float(), nullable=False),
        sa.Column("tax_stamp_duty", sa.Float(), nullable=False),
        sa.Column("tax_land", sa.Float(), nullable=False),
        sa.Column("tax_environmental", sa.Float(), nullable=False),
        sa.Column("invoice_no", sa.String(50), nullable=False),
        sa.Column("invoice_code", sa.String(20), nullable=False),
        sa.Column("invoice_type", sa.String(20), nullable=False),
        sa.Column("invoice_date", sa.String(20), nullable=False),
        sa.Column("invoice_deductible", sa.Boolean(), nullable=False),
        sa.Column("tax_total", sa.Float(), nullable=False),
        sa.Column("contract_no", sa.String(100), nullable=False),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("document_date", sa.String(20), nullable=False),
        sa.Column("confidentiality", sa.String(32), nullable=False),
        sa.Column("version_label", sa.String(40), nullable=False),
        sa.Column("version_status", sa.String(24), nullable=False),
        sa.Column("metadata_confidence", sa.Float(), nullable=False),
        sa.Column("metadata_source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_code", name="uq_documents_document_code"),
        sa.CheckConstraint(
            "entity_code = '' OR entity_code IS NULL OR entity_code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',"
            "'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',"
            "'C01','C02','D01','D02','D03')",
            name="ck_documents_real_entity_code",
        ),
        sa.CheckConstraint(
            "counterparty_code = '' OR counterparty_code IS NULL OR counterparty_code NOT IN ('甲', '乙', '丙', '丁')",
            name="ck_documents_no_virtual_counterparty",
        ),
    )
    _create_if_compatible(op.create_index, "ix_documents_contract_no", "documents", ["contract_no"])
    _create_if_compatible(op.create_index, "ix_documents_version_status", "documents", ["version_status"])
    _create_if_compatible(op.create_index, "ix_documents_document_type", "documents", ["document_type"])
    _create_if_compatible(op.create_index, "ix_documents_confidentiality", "documents", ["confidentiality"])
    _create_if_compatible(op.create_index, "ix_documents_project_status", "documents", ["project_id", "parse_status"])
    _create_if_compatible(op.create_index, "ix_documents_entity_code", "documents", ["entity_code"])
    _create_if_compatible(op.create_index, "ix_documents_period", "documents", ["period"])
    _create_if_compatible(op.create_index, "ix_documents_file_hash", "documents", ["file_hash"])

    # Chunks
    _create_if_compatible(
        op.create_table,
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.String(500), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("chunk_strategy_version", sa.String(32), nullable=False),
        sa.Column("title_chain", sa.Text(), nullable=False),
        sa.Column("semantic_type", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_chunk_index"),
    )
    _create_if_compatible(op.create_index, "ix_chunks_document_id", "chunks", ["document_id"])
    _create_if_compatible(op.create_index, "ix_chunks_project_id", "chunks", ["project_id"])
    _create_if_compatible(op.create_index, "ix_chunks_search_text", "chunks", ["search_text"], unique=False)
    _create_if_compatible(op.create_index, "ix_chunks_chunk_strategy_version", "chunks", ["chunk_strategy_version"])

    # Ingest jobs
    _create_if_compatible(
        op.create_table,
        "ingest_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=False),
        sa.Column("next_retry_at", sa.String(40), nullable=False),
        sa.Column("parse_quality_score", sa.Float(), nullable=False),
        sa.Column("is_encrypted", sa.Boolean(), nullable=False),
        sa.Column("parse_quality_flags_json", sa.Text(), nullable=False),
        sa.Column("chunk_strategy_version", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_ingest_jobs_document_id", "ingest_jobs", ["document_id"])
    _create_if_compatible(op.create_index, "ix_ingest_jobs_status", "ingest_jobs", ["status"])
    _create_if_compatible(op.create_index, "ix_jobs_status_priority", "ingest_jobs", ["status", "created_at"])

    # Query logs
    _create_if_compatible(
        op.create_table,
        "query_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("filters_json", sa.Text(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("result_chunk_ids_json", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("embedding_backend", sa.String(40), nullable=False),
        sa.Column("reranker_backend", sa.String(40), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("rewrite_result_json", sa.Text(), nullable=False),
        sa.Column("hyde_used", sa.Boolean(), nullable=False),
        sa.Column("quality_gate_json", sa.Text(), nullable=False),
        sa.Column("bm25_candidates_json", sa.Text(), nullable=False),
        sa.Column("vector_candidates_json", sa.Text(), nullable=False),
        sa.Column("reranked_json", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("chunk_version", sa.String(32), nullable=False),
        sa.Column("embedding_version", sa.String(32), nullable=False),
        sa.Column("reranker_version", sa.String(32), nullable=False),
        sa.Column("retrieval_status", sa.String(32), nullable=False),
        sa.Column("deep_mode", sa.Boolean(), nullable=False),
        sa.Column("answer_faithful", sa.Boolean(), nullable=False),
        sa.Column("no_answer_confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_query_logs_project_id", "query_logs", ["project_id"])

    # Benchmark questions
    _create_if_compatible(
        op.create_table,
        "benchmark_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("gold_answer", sa.Text(), nullable=False),
        sa.Column("answerable", sa.Boolean(), nullable=False),
        sa.Column("gold_document_ids_json", sa.Text(), nullable=False),
        sa.Column("gold_keywords_json", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(16), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_benchmark_questions_project_id", "benchmark_questions", ["project_id"])
    _create_if_compatible(op.create_index, "ix_benchmark_questions_difficulty", "benchmark_questions", ["difficulty"])
    _create_if_compatible(op.create_index, "ix_benchmark_questions_category", "benchmark_questions", ["category"])

    # Benchmark runs
    _create_if_compatible(
        op.create_table,
        "benchmark_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("per_question_json", sa.Text(), nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_benchmark_runs_project_id", "benchmark_runs", ["project_id"])
    _create_if_compatible(op.create_index, "ix_benchmark_runs_status", "benchmark_runs", ["status"])

    # Backup records
    _create_if_compatible(
        op.create_table,
        "backup_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backup_type", sa.String(24), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("path", sa.String(600), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("tested_at", sa.String(40), nullable=False),
        sa.Column("test_status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_backup_records_backup_type", "backup_records", ["backup_type"])
    _create_if_compatible(op.create_index, "ix_backup_records_status", "backup_records", ["status"])

    # Query feedback
    _create_if_compatible(
        op.create_table,
        "query_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query_log_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("correction", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["query_log_id"], ["query_logs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_query_feedback_query_log_id", "query_feedback", ["query_log_id"])
    _create_if_compatible(op.create_index, "ix_query_feedback_project_id", "query_feedback", ["project_id"])
    _create_if_compatible(op.create_index, "ix_query_feedback_helpful", "query_feedback", ["helpful"])

    # Knowledge conflicts
    _create_if_compatible(
        op.create_table,
        "knowledge_conflicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(60), nullable=False),
        sa.Column("conflict_type", sa.String(32), nullable=False),
        sa.Column("document_ids_json", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.String(40), nullable=False),
        sa.Column("resolved_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_knowledge_conflicts_project_id", "knowledge_conflicts", ["project_id"])
    _create_if_compatible(op.create_index, "ix_knowledge_conflicts_field_name", "knowledge_conflicts", ["field_name"])
    _create_if_compatible(op.create_index, "ix_knowledge_conflicts_status", "knowledge_conflicts", ["status"])

    # Regulations
    _create_if_compatible(
        op.create_table,
        "regulations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("document_no", sa.String(100), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("legal_level", sa.String(32), nullable=False),
        sa.Column("jurisdiction", sa.String(100), nullable=False),
        sa.Column("tax_type", sa.String(100), nullable=False),
        sa.Column("industry", sa.String(100), nullable=False),
        sa.Column("publish_date", sa.String(20), nullable=False),
        sa.Column("effective_date", sa.String(20), nullable=False),
        sa.Column("expiry_date", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(300), nullable=False),
        sa.Column("version_label", sa.String(40), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["supersedes_id"], ["regulations.id"]),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["regulations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_regulations_title", "regulations", ["title"])
    _create_if_compatible(op.create_index, "ix_regulations_document_no", "regulations", ["document_no"])
    _create_if_compatible(op.create_index, "ix_regulations_status", "regulations", ["status"])
    _create_if_compatible(op.create_index, "ix_regulations_tax_type", "regulations", ["tax_type"])
    _create_if_compatible(op.create_index, "ix_regulations_industry", "regulations", ["industry"])
    _create_if_compatible(op.create_index, "ix_regulations_legal_level", "regulations", ["legal_level"])
    _create_if_compatible(op.create_index, "ix_regulations_jurisdiction", "regulations", ["jurisdiction"])

    # Regulation articles
    _create_if_compatible(
        op.create_table,
        "regulation_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("regulation_id", sa.Integer(), nullable=False),
        sa.Column("chapter", sa.String(200), nullable=False),
        sa.Column("article_no", sa.String(20), nullable=False),
        sa.Column("paragraph_no", sa.String(20), nullable=False),
        sa.Column("heading", sa.String(500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("full_chapter_text", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["regulation_id"], ["regulations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(
        op.create_index, "ix_regulation_articles_regulation_id", "regulation_articles", ["regulation_id"]
    )
    _create_if_compatible(op.create_index, "ix_regulation_articles_chapter", "regulation_articles", ["chapter"])

    # Regulation chunks
    _create_if_compatible(
        op.create_table,
        "regulation_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("regulation_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["regulation_id"], ["regulations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("regulation_id", "chunk_index", name="uq_regulation_chunks_reg_chunk_index"),
    )
    _create_if_compatible(op.create_index, "ix_regulation_chunks_regulation_id", "regulation_chunks", ["regulation_id"])

    # === AI Review models ===
    # Facts snapshots
    _create_if_compatible(
        op.create_table,
        "facts_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(64), nullable=False),
        sa.Column("facts_data", sa.JSON(), nullable=False),
        sa.Column("as_of", sa.String(40), nullable=False),
        sa.Column("facts_version", sa.String(64), nullable=False),
        sa.Column("analytics_contract_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_code", "facts_version", name="uq_facts_snapshots_project_facts_version"),
    )
    _create_if_compatible(op.create_index, "ix_facts_snapshots_project_id", "facts_snapshots", ["project_id"])
    _create_if_compatible(op.create_index, "ix_facts_snapshots_project_code", "facts_snapshots", ["project_code"])

    # AI review runs
    _create_if_compatible(
        op.create_table,
        "ai_review_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("facts_snapshot_id", sa.String(36), nullable=True),
        sa.Column("rag_evidence_pack_id", sa.String(36), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["facts_snapshot_id"], ["facts_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_if_compatible(op.create_index, "ix_ai_review_runs_project_id", "ai_review_runs", ["project_id"])
    _create_if_compatible(op.create_index, "ix_ai_review_runs_status", "ai_review_runs", ["status"])
    _create_if_compatible(op.create_index, "ix_ai_review_runs_project_code", "ai_review_runs", ["project_code"])

    # Metric versions
    _create_if_compatible(
        op.create_table,
        "metric_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("metric_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.String(32), nullable=False),
        sa.Column("effective_to", sa.String(32), nullable=True),
        sa.Column("deprecates", sa.String(16), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("formula_engine", sa.String(32), nullable=True),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metric_id", "version", name="uq_metric_versions_metric_version"),
    )
    _create_if_compatible(op.create_index, "ix_metric_versions_metric_id", "metric_versions", ["metric_id"])


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _database_has_rows() -> bool:
    """Return whether any application table contains data.

    ``alembic_version`` is deliberately excluded.  This keeps a fresh
    schema downgrade available while protecting a populated runtime database
    from an accidental ``downgrade base`` data wipe.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        quoted = '"' + table_name.replace('"', '""') + '"'
        if bind.execute(sa.text(f"SELECT 1 FROM {quoted} LIMIT 1")).first() is not None:
            return True
    return False


def _require_explicit_destructive_downgrade_approval() -> None:
    """Refuse populated-database downgrade without an explicit backup.

    ``alembic/env.py`` persists ``PROJECT_RAG_DOWNGRADE_BACKUP`` using the
    SQLite backup API before this function runs.  Requiring both the switch
    and a real backup file makes the destructive action auditable and
    recoverable.
    """
    if not _database_has_rows():
        return
    if not _env_flag("PROJECT_RAG_ALLOW_DESTRUCTIVE_DOWNGRADE"):
        raise RuntimeError(
            "refusing downgrade of populated RAG database; set "
            "PROJECT_RAG_ALLOW_DESTRUCTIVE_DOWNGRADE=1 and "
            "PROJECT_RAG_DOWNGRADE_BACKUP=/explicit/backup/path.db"
        )
    backup_value = os.environ.get("PROJECT_RAG_DOWNGRADE_BACKUP", "").strip()
    if not backup_value:
        raise RuntimeError("populated-database downgrade requires PROJECT_RAG_DOWNGRADE_BACKUP")
    backup = Path(backup_value).expanduser().resolve()
    if not backup.is_file() or backup.stat().st_size == 0:
        raise RuntimeError(f"populated-database downgrade backup is missing or empty: {backup}")


def downgrade() -> None:
    _require_explicit_destructive_downgrade_approval()
    op.drop_table("metric_versions")
    op.drop_table("ai_review_runs")
    op.drop_table("facts_snapshots")
    op.drop_table("regulation_chunks")
    op.drop_table("regulation_articles")
    op.drop_table("regulations")
    op.drop_table("knowledge_conflicts")
    op.drop_table("query_feedback")
    op.drop_table("backup_records")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_questions")
    op.drop_table("query_logs")
    op.drop_table("ingest_jobs")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("entities")
    op.drop_table("projects")
