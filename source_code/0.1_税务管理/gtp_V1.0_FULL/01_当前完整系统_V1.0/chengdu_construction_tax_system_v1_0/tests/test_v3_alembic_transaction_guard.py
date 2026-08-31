"""Regression guard for SQLAlchemy 2.0 / Alembic transaction ownership.

Task17 local validation exposed a subtle failure mode: the version-column probe
performed a SELECT, SQLAlchemy 2.0 autobegan a transaction, and when no resize
was needed that transaction remained open. Alembic then ran inside a transaction
it did not own and DDL could be rolled back when the connection closed.

This structural test is intentionally database-independent. The real PostgreSQL
migration path remains covered by the Task17 local Gate; this guard prevents the
environment file from reintroducing the offending transaction shape.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "alembic" / "env.py"


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _contains_call(node: ast.AST, rendered_name: str) -> bool:
    return any(
        isinstance(child, ast.Call) and ast.unparse(child.func) == rendered_name
        for child in ast.walk(node)
    )


def test_version_capacity_helper_does_not_commit_implicitly():
    tree = ast.parse(ENV_PATH.read_text(encoding="utf-8"))
    helper = _function(tree, "_ensure_version_column_capacity")
    calls = [_call_name(node) for node in ast.walk(helper) if isinstance(node, ast.Call)]
    assert "commit" not in calls
    assert "rollback" not in calls


def test_online_migration_closes_probe_transaction_before_alembic_transaction():
    tree = ast.parse(ENV_PATH.read_text(encoding="utf-8"))
    function = _function(tree, "run_migrations_online")

    explicit_probe_transaction = False
    migration_transaction = False
    for node in ast.walk(function):
        if not isinstance(node, ast.With):
            continue
        rendered_items = [ast.unparse(item.context_expr) for item in node.items]
        body_calls = [
            ast.unparse(child.func)
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
        ]
        if any("connection.begin()" in item for item in rendered_items):
            if "_ensure_version_column_capacity" in body_calls:
                explicit_probe_transaction = True
        if any("context.begin_transaction()" in item for item in rendered_items):
            if "context.run_migrations" in body_calls:
                migration_transaction = True

    assert explicit_probe_transaction, (
        "version-table capacity probe must run in its own explicit connection.begin() transaction"
    )
    assert migration_transaction, "Alembic migrations must keep their own context.begin_transaction()"


def test_online_migration_has_fail_closed_transaction_boundary_guards():
    tree = ast.parse(ENV_PATH.read_text(encoding="utf-8"))
    function = _function(tree, "run_migrations_online")
    guard_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "_assert_clean_transaction_boundary"
    ]
    assert len(guard_calls) >= 2, (
        "online migrations must assert a clean SQLAlchemy transaction boundary "
        "both before and after Alembic owns the migration transaction"
    )


def test_online_migration_does_not_swallow_migration_exceptions():
    tree = ast.parse(ENV_PATH.read_text(encoding="utf-8"))
    function = _function(tree, "run_migrations_online")
    for node in ast.walk(function):
        if isinstance(node, (ast.Try, ast.TryStar)) and _contains_call(node, "context.run_migrations"):
            raise AssertionError(
                "context.run_migrations() must not be wrapped by try/except in env.py; "
                "the original migration failure must propagate to the caller"
            )
