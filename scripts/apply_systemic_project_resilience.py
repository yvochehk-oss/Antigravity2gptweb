from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0"
BACKEND = BASE / "chengdu_construction_tax_system_v1_0"
FRONTEND = BASE / "frontend_stitch"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tier 1: dynamic primary-contract resolution. No project-specific code list.
# ---------------------------------------------------------------------------
summary_path = BACKEND / "app/services/canonical_project_summary.py"
summary = summary_path.read_text(encoding="utf-8")
summary = re.sub(
    r"\n_PRIMARY_CONTRACT_CODES = \(.*?\n\)\n",
    "\n",
    summary,
    count=1,
    flags=re.S,
)
start = summary.index("def resolve_project_transaction_price(")
end = summary.index("\n\ndef payment_boundary", start)
resolver = '''def resolve_project_transaction_price(
    db,
    project_id: int,
    *,
    contract_facts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve project revenue transaction price without project-specific codes.

    Resolution order is intentionally data-driven and non-crashing:
    1. explicit canonical contract_scope marker;
    2. ZB-* general/main-contract convention;
    3. largest external-boundary contract (degraded when ambiguity exists);
    4. projects master contract_total / contract_amount (degraded fallback).
    """
    internal_codes = {
        _code(code)
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    facts = (
        list(contract_facts)
        if contract_facts is not None
        else load_current_facts(db, int(project_id), "contract")
    )
    candidates: list[dict[str, Any]] = []
    for fact in facts:
        payload = _payload(fact.get("payload"))
        contract_no = _clean(payload.get("contract_no") or fact.get("business_key"))
        amount = _decimal(payload.get("total_amount"))
        party_a = _code(payload.get("party_a_entity_code") or payload.get("party_a_code"))
        party_b = _code(payload.get("party_b_entity_code") or payload.get("party_b_code"))
        scope = _clean(payload.get("contract_scope")).upper()
        if amount <= 0:
            continue
        explicit_main = scope in {"PRIMARY_CUSTOMER_CONTRACT", "PROJECT_REVENUE"}
        zb_main = _code(contract_no).startswith("ZB-")
        crosses_boundary = bool(party_a and party_b) and ((party_a in internal_codes) != (party_b in internal_codes))
        candidates.append(
            {
                "fact_id": int(fact.get("fact_id") or 0),
                "fact_version": int(fact.get("fact_version") or 0),
                "contract_no": contract_no,
                "amount": amount,
                "explicit_main": explicit_main,
                "zb_main": zb_main,
                "crosses_boundary": crosses_boundary,
            }
        )

    def _pick(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            rows,
            key=lambda row: (row["amount"], row["fact_version"], row["fact_id"]),
            reverse=True,
        )[0]

    explicit = [row for row in candidates if row["explicit_main"]]
    if explicit:
        selected = _pick(explicit)
        status = "READY" if len(explicit) == 1 else "DEGRADED"
        reason = "EXPLICIT_SCOPE" if len(explicit) == 1 else "MULTIPLE_EXPLICIT_SCOPE_MAX_AMOUNT"
    else:
        zb_candidates = [row for row in candidates if row["zb_main"]]
        if zb_candidates:
            selected = _pick(zb_candidates)
            status = "READY" if len(zb_candidates) == 1 else "DEGRADED"
            reason = "ZB_PREFIX" if len(zb_candidates) == 1 else "MULTIPLE_ZB_MAX_AMOUNT"
        else:
            boundary = [row for row in candidates if row["crosses_boundary"]]
            if boundary:
                selected = _pick(boundary)
                status = "READY" if len(boundary) == 1 else "DEGRADED"
                reason = "SINGLE_BOUNDARY" if len(boundary) == 1 else "MULTIPLE_BOUNDARY_MAX_AMOUNT"
            else:
                project = db.get(Project, int(project_id)) if hasattr(db, "get") else None
                fallback_amount = _decimal(
                    getattr(project, "contract_total", None)
                    or getattr(project, "contract_amount", None)
                ) if project is not None else Decimal("0")
                return {
                    "amount": fallback_amount,
                    "status": "DEGRADED" if fallback_amount > 0 else "EMPTY",
                    "source_fact_id": None,
                    "fact_version": None,
                    "contract_no": "",
                    "resolution_reason": "PROJECT_MASTER_FALLBACK" if fallback_amount > 0 else "NO_CONTRACT_PRICE_FACT",
                }

    return {
        "amount": selected["amount"],
        "status": status,
        "source_fact_id": selected["fact_id"],
        "fact_version": selected["fact_version"],
        "contract_no": selected["contract_no"],
        "resolution_reason": reason,
    }
'''
summary = summary[:start] + resolver + summary[end:]
summary = summary.replace(
    '            "contract_no": transaction["contract_no"],\n',
    '            "contract_no": transaction["contract_no"],\n            "resolution_reason": transaction.get("resolution_reason", ""),\n',
    1,
)
summary_path.write_text(summary, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tier 2: API-level defensive fallback + canonical project collection route.
# ---------------------------------------------------------------------------
api_path = BACKEND / "app/routers/api.py"
api = api_path.read_text(encoding="utf-8")
marker = '@router.get("/api/projects/{pid}")\ndef api_project(pid: int, _user=_reader_dependency):\n'
if marker not in api:
    raise RuntimeError("api_project marker not found")
helper_and_list = '''def _master_project_summary(project: Project, *, message: str = "") -> dict[str, Any]:
    contract_total = float(project.contract_total or project.contract_amount or 0)
    return {
        "status": "DEGRADED",
        "message": message or "项目计算摘要暂不可用，已降级为 Project Master 主数据。",
        "project": {
            "id": project.id,
            "code": project.code,
            "project_code": project.project_code or project.code,
            "name": project.name,
            "city": project.city,
            "location": project.location or project.city,
            "contract_total": contract_total,
            "contract_amount": float(project.contract_amount or project.contract_total or 0),
        },
        "revenue": 0.0,
        "real_cost": 0.0,
        "profit": 0.0,
        "margin": 0.0,
        "progress": 0.0,
        "vat": 0.0,
        "eac": None,
        "data_gaps": ["CANONICAL_PROJECT_SUMMARY_UNAVAILABLE"],
        "source_of_truth": "projects",
    }


def _render_canonical_project_summary(summary: dict[str, Any]) -> dict[str, Any]:
    project = summary.get("project")
    if project is None:
        raise LookupError("project not found")
    out = {k: v for k, v in summary.items() if k != "project"}
    contract_total = float(summary.get("contract_total") or 0)
    out.setdefault("status", "READY")
    out["project"] = {
        "id": project.id,
        "code": project.code,
        "project_code": project.project_code or project.code,
        "name": project.name,
        "city": project.city,
        "location": project.location or project.city,
        "contract_total": contract_total,
        "contract_amount": float(summary.get("contract_amount") or contract_total),
    }
    return out


@router.get("/api/projects")
def api_projects(_user=_reader_dependency):
    db = SessionLocal()
    try:
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        items: list[dict[str, Any]] = []
        degraded = False
        for project in projects:
            try:
                items.append(_render_canonical_project_summary(canonical_project_summary(db, project.id)))
            except Exception as exc:
                db.rollback()
                degraded = True
                _LOGGER.exception("canonical project collection item degraded: pid=%s", project.id)
                refreshed = db.get(Project, project.id)
                if refreshed is not None:
                    items.append(_master_project_summary(refreshed, message=str(exc)))
        return {
            "status": "DEGRADED" if degraded else "READY",
            "projects": items,
            "total": len(items),
        }
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("project collection query failed")
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        items = [_master_project_summary(project, message=str(exc)) for project in projects]
        return {"status": "DEGRADED", "projects": items, "total": len(items)}
    finally:
        db.close()


@router.get("/api/projects/{pid}")
def api_project(pid: int, _user=_reader_dependency):
'''
api = api.replace(marker, helper_and_list, 1)
old_body = '''    db = SessionLocal()
    try:
        s = canonical_project_summary(db, pid)
        project = s.get("project")
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        out = {k: v for k, v in s.items() if k != "project"}
        contract_total = float(s.get("contract_total") or 0)
        out["project"] = {
            "id": project.id,
            "code": project.code,
            "project_code": project.project_code or project.code,
            "name": project.name,
            "city": project.city,
            "location": project.location or project.city,
            "contract_total": contract_total,
            "contract_amount": float(s.get("contract_amount") or contract_total),
        }
        return out
    except LookupError:
        db.rollback()
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("canonical project summary query failed: pid=%s", pid)
        raise
    finally:
        db.close()
'''
new_body = '''    db = SessionLocal()
    try:
        return _render_canonical_project_summary(canonical_project_summary(db, pid))
    except LookupError:
        db.rollback()
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("canonical project summary degraded to master data: pid=%s", pid)
        project = db.get(Project, pid)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        return _master_project_summary(project, message=str(exc))
    finally:
        db.close()
'''
if old_body not in api:
    raise RuntimeError("api_project body not found")
api = api.replace(old_body, new_body, 1)
api_path.write_text(api, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tier 3: frontend non-destructive project display gate.
# ---------------------------------------------------------------------------
types_path = FRONTEND / "src/types.ts"
types = types_path.read_text(encoding="utf-8")
types = types.replace(
    '  costItems: CostBreakdownItem[];// 关联成本分解\n',
    '  costItems: CostBreakdownItem[];// 关联成本分解\n  dataStatus?: DataStatus;      // 摘要降级时仍保留项目卡片\n  dataGaps?: string[];          // 降级原因，不得据此删除项目\n',
    1,
)
types_path.write_text(types, encoding="utf-8")

api_ts_path = FRONTEND / "src/api.ts"
api_ts = api_ts_path.read_text(encoding="utf-8")
api_ts = api_ts.replace(
    'export interface ProjectSummaryResponse {\n  project?: {',
    'export interface ProjectSummaryResponse {\n  status?: DataStatus | string;\n  message?: string;\n  data_gaps?: string[];\n  project?: {',
    1,
)
api_ts = api_ts.replace(
    '    costItems: [],\n  };\n}\n',
    '    costItems: [],\n    dataStatus: summary.status === \'DEGRADED\' ? \'DEGRADED\' : \'READY\',\n    dataGaps: Array.isArray(summary.data_gaps) ? summary.data_gaps.map(String) : [],\n  };\n}\n',
    1,
)
old_discovery = '''export async function discoverProjectIds(signal?: AbortSignal): Promise<number[]> {
  const payload = await fetchJson<unknown>('/api/projects', { signal });
  const ids = extractProjectIds(payload);
  if (ids.length === 0) {
    throw new ApiError('Tax 项目集合接口返回的数据不包含有效项目 ID。', 502, payload);
  }
  return ids;
}

export async function fetchConfiguredProjects(signal?: AbortSignal): Promise<ProjectItem[]> {
  const configuredIds = configuredProjectIds();
  let ids = configuredIds;
  if (ids.length === 0) {
    try {
      ids = await discoverProjectIds(signal);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        throw new ApiError(
          '未配置 VITE_PROJECT_IDS，且 Tax 后端没有项目集合 JSON 接口。',
          0,
          error.payload,
        );
      }
      throw error;
    }
  }
  if (ids.length === 0) {
    throw new ApiError('没有可加载的真实项目 ID。', 0);
  }
  const results = await Promise.allSettled(ids.map(id => fetchProjectSummary(id, signal)));
  const projects = results
    .filter((result): result is PromiseFulfilledResult<ProjectItem> => result.status === 'fulfilled')
    .map(result => result.value);
  if (projects.length === 0) {
    const firstFailure = results.find(result => result.status === 'rejected');
    if (firstFailure?.status === 'rejected' && firstFailure.reason instanceof Error) {
      throw firstFailure.reason;
    }
    throw new ApiError('已配置的项目均无法从 Tax 服务加载。', 502);
  }
  return projects;
}
'''
new_discovery = '''interface ProjectMasterReference {
  id: number;
  code: string;
  name: string;
  city: string;
  location: string;
  contractTotal: number;
}

function extractProjectMasterReferences(payload: unknown): ProjectMasterReference[] {
  const root = asRecord(payload);
  const raw = Array.isArray(payload)
    ? payload
    : root && Array.isArray(root.projects) ? root.projects : [];
  const references: ProjectMasterReference[] = [];
  const seen = new Set<number>();
  for (const item of raw) {
    const row = asRecord(item);
    const project = asRecord(row?.project) ?? row;
    if (!project) continue;
    const id = positiveInteger(project.id ?? project.project_id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    references.push({
      id,
      code: String(project.code ?? project.project_code ?? '').trim(),
      name: String(project.name ?? '').trim(),
      city: String(project.city ?? '').trim(),
      location: String(project.location ?? project.city ?? '').trim(),
      contractTotal: toFiniteNumber(project.contract_total ?? project.contract_amount),
    });
  }
  return references;
}

function fallbackProjectItem(ref: ProjectMasterReference): ProjectItem {
  return {
    id: String(ref.id),
    numericId: ref.id,
    projectCode: ref.code,
    name: ref.name || `项目 ${ref.id}`,
    constructionStage: '—',
    healthGrade: '数据降级',
    totalBudget: ref.contractTotal,
    spentAmount: 0,
    remainingBudget: ref.contractTotal,
    progressPercent: 0,
    taxRiskGrade: '未知',
    isOverBudget: null,
    managerName: '—',
    location: ref.location || ref.city || '—',
    teamAvatars: [],
    costItems: [],
    dataStatus: 'DEGRADED',
    dataGaps: ['PROJECT_SUMMARY_REQUEST_FAILED'],
  };
}

export async function discoverProjectIds(signal?: AbortSignal): Promise<number[]> {
  const payload = await fetchJson<unknown>('/api/projects', { signal });
  const refs = extractProjectMasterReferences(payload);
  if (refs.length === 0) {
    throw new ApiError('Tax 项目集合接口返回的数据不包含有效项目 ID。', 502, payload);
  }
  return refs.map(item => item.id);
}

export async function fetchConfiguredProjects(signal?: AbortSignal): Promise<ProjectItem[]> {
  let masterRefs: ProjectMasterReference[] = [];
  try {
    const collection = await fetchJson<unknown>('/api/projects', { signal });
    masterRefs = extractProjectMasterReferences(collection);
  } catch (error) {
    if (signal?.aborted) throw error;
    const configuredIds = configuredProjectIds();
    if (configuredIds.length === 0) throw error;
    masterRefs = configuredIds.map(id => ({
      id,
      code: '',
      name: `项目 ${id}`,
      city: '',
      location: '',
      contractTotal: 0,
    }));
  }

  if (masterRefs.length === 0) {
    throw new ApiError('没有可加载的真实项目主数据。', 0);
  }

  const results = await Promise.allSettled(
    masterRefs.map(ref => fetchProjectSummary(ref.id, signal)),
  );
  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError');
  }

  return results.map((result, index) => (
    result.status === 'fulfilled'
      ? result.value
      : fallbackProjectItem(masterRefs[index])
  ));
}
'''
if old_discovery not in api_ts:
    raise RuntimeError("frontend discovery block not found")
api_ts = api_ts.replace(old_discovery, new_discovery, 1)
api_ts_path.write_text(api_ts, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tier 4: systemic regression tests.
# ---------------------------------------------------------------------------
test_path = BACKEND / "tests/test_canonical_project_summary.py"
tests = test_path.read_text(encoding="utf-8")
tests = tests.replace('from pathlib import Path\n\nimport pytest\n', 'from pathlib import Path\nfrom types import SimpleNamespace\n\nimport pytest\nfrom fastapi.testclient import TestClient\nfrom sqlalchemy import select\n')
tests = tests.replace(
    'from app.services.canonical_project_summary import resolve_project_transaction_price\n',
    'from app.services.canonical_project_summary import resolve_project_transaction_price\nfrom app.db import SessionLocal\nfrom app.models import Project\n',
)
old_ambiguous = '''def test_resolver_fails_closed_when_boundary_contracts_are_ambiguous(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "200", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    with pytest.raises(ValueError, match="ambiguous canonical project transaction price"):
        resolve_project_transaction_price(_fake_db(["A08"]), 15)


def test_resolver_fails_closed_when_boundary_contracts_have_same_amount(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 2, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "100", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    with pytest.raises(ValueError, match="ambiguous canonical project transaction price"):
        resolve_project_transaction_price(_fake_db(["A08"]), 15)
'''
new_ambiguous = '''def test_resolver_degrades_to_largest_boundary_contract(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "200", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("200")
    assert result["status"] == "DEGRADED"
    assert result["resolution_reason"] == "MULTIPLE_BOUNDARY_MAX_AMOUNT"


def test_resolver_degrades_deterministically_when_boundary_amounts_tie(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 2, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "100", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("100")
    assert result["contract_no"] == "X-2"
    assert result["status"] == "DEGRADED"


def test_resolver_prefers_zb_prefix_without_static_project_codes(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "OTHER-EXT", "total_amount": "990", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "ZB-FUTURE-NEW-PROJECT", "total_amount": "880", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 999)
    assert result["contract_no"] == "ZB-FUTURE-NEW-PROJECT"
    assert result["amount"] == Decimal("880")
    assert result["status"] == "READY"
'''
if old_ambiguous not in tests:
    raise RuntimeError("old ambiguity tests not found")
tests = tests.replace(old_ambiguous, new_ambiguous, 1)
anchor = '\n\ndef test_api_project_source_has_no_legacy_project_summary_call():'
integration = '''\n\ndef test_all_projects_resilient_summary(seeded_app, monkeypatch):
    """Every Project Master row must remain HTTP-visible even if summary logic degrades."""
    import app.dependencies as dependencies
    from app.main import app

    monkeypatch.setattr(
        dependencies,
        "require_login",
        lambda _request: SimpleNamespace(role="admin", username="resilience-test"),
    )

    with SessionLocal() as db:
        project_ids = list(db.execute(select(Project.id).order_by(Project.id)).scalars().all())
    assert project_ids, "seeded Project Master must contain projects"

    client = TestClient(app)
    collection = client.get("/api/projects")
    assert collection.status_code == 200
    collection_payload = collection.json()
    returned_ids = {
        int(item["project"]["id"])
        for item in collection_payload.get("projects", [])
        if isinstance(item, dict) and isinstance(item.get("project"), dict)
    }
    assert returned_ids == set(project_ids)

    for project_id in project_ids:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["project"]["id"] == project_id
        assert payload.get("status") in {"READY", "DEGRADED"}
        assert payload["project"].get("name")
'''
if anchor not in tests:
    raise RuntimeError("test insertion anchor not found")
tests = tests.replace(anchor, integration + anchor, 1)
tests = tests.replace('    assert "project.contract_total" not in api_block\n', '    assert "project.contract_total" not in api_block\n    assert "_master_project_summary" in api_block\n', 1)
test_path.write_text(tests, encoding="utf-8")

print("systemic project resilience patch applied")
