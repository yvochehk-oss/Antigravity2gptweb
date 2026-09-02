from __future__ import annotations

import ast
from pathlib import Path


_API_PATH = Path("app/routers/api.py")


def _is_calc_module(module: str | None) -> bool:
    value = module or ""
    return value == "calc" or value == "app.calc" or value.endswith(".calc")


def _is_project_summary_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "project_summary"
    if isinstance(func, ast.Attribute):
        return func.attr == "project_summary"
    return False


def test_project_summary_route_does_not_read_legacy_finance_tables():
    source = _API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_API_PATH))

    forbidden_imports: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_calc_module(node.module) and any(alias.name == "project_summary" for alias in node.names):
                forbidden_imports.append(node)
        elif isinstance(node, ast.Import):
            if any(
                alias.name == "app.calc.project_summary"
                or alias.name == "calc.project_summary"
                or alias.name.endswith(".calc.project_summary")
                for alias in node.names
            ):
                forbidden_imports.append(node)
    assert not forbidden_imports, "legacy project_summary import is forbidden"

    api_project = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "api_project"
        ),
        None,
    )
    assert api_project is not None, "api_project route function not found"

    legacy_calls = [node for node in ast.walk(api_project) if isinstance(node, ast.Call) and _is_project_summary_call(node)]
    assert not legacy_calls, "api_project must not call legacy project_summary"

    canonical_calls = [
        node
        for node in ast.walk(api_project)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "canonical_project_summary"
    ]
    assert canonical_calls, "api_project must call canonical_project_summary"

    forbidden_model_names = {"RealCost", "Invoice", "Progress"}
    used_names = {node.id for node in ast.walk(api_project) if isinstance(node, ast.Name)}
    assert not (used_names & forbidden_model_names), "api_project must not read legacy finance models"

    legacy_project_contract_reads = [
        node
        for node in ast.walk(api_project)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "project"
        and node.attr in {"contract_total", "contract_amount"}
    ]
    assert not legacy_project_contract_reads, "api_project must not read legacy project contract values"
