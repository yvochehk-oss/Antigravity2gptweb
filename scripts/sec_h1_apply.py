from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "source_code/0.2_RAG系统/project-rag-v1.1/app/legacy_routes.py"


def cut_between(source: str, start: str, end: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        raise RuntimeError(f"missing start marker: {start}")
    end_index = source.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"missing end marker: {end}")
    return source[:start_index] + source[end_index:]


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")

    # Retire RAG Project Master create/sync endpoints while preserving GETs.
    source = cut_between(
        source,
        '@app.post("/api/v1/projects")\n',
        '@app.get("/api/v1/projects/{project_id}")\n',
    )

    # Remove destructive Project Master purge/delete implementation wholesale.
    # Project lifecycle is owned by Tax; RAG must not carry a second delete path.
    source = cut_between(
        source,
        "_PROJECT_PURGE_SECONDARY_SQL = (\n",
        '@app.get("/api/v1/projects/{project_id}/audit")\n',
    )

    # Retire legacy web-form Project Master creation.
    source = cut_between(
        source,
        '@app.post("/projects")\n',
        '@app.get("/projects/{project_ref}", response_class=HTMLResponse)\n',
    )

    for obsolete in (
        "import hashlib\n",
        "import hmac\n",
        "    ProjectCreate,\n",
        "    ProjectDeleteRequest,\n",
        "    ProjectSync,\n",
        "from .user_center.db import UserCenterSessionLocal\n",
        "from .user_center.models import UserAccount\n",
        "from .user_center.security import verify_password\n",
    ):
        source = source.replace(obsolete, "")

    forbidden_credentials = (
        "888888",
        "admin123",
        "123456",
        "cdjg@2026",
        "Admin@2026",
    )
    leaked = [value for value in forbidden_credentials if value in source]
    if leaked:
        raise RuntimeError(f"hard-coded credentials remain: {leaked}")

    forbidden_routes = (
        '@app.post("/api/v1/projects")',
        '@app.post("/api/v1/projects/sync")',
        '@app.delete("/api/v1/projects/{project_id}")',
        '@app.post("/api/v1/projects/{project_id}/delete")',
        '@app.post("/projects")',
    )
    remaining = [route for route in forbidden_routes if route in source]
    if remaining:
        raise RuntimeError(f"forbidden Project Master routes remain: {remaining}")

    ast.parse(source, filename=str(TARGET))
    TARGET.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
