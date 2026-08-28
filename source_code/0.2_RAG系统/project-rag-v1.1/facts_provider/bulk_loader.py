"""Request-scoped bulk loading for Canonical Facts.

Executive endpoints reuse one SQLAlchemy Session while rendering many projects.
The legacy helper creates a new FactsProvider for every project, so a normal
cache miss becomes one analytics query per project.  This installer keeps the
public API unchanged but preloads the canonical view once per Session.
"""
from __future__ import annotations

from typing import Any


def install_bulk_facts_loading() -> None:
    from sqlalchemy import text

    from . import facts_provider as fp

    if getattr(fp.FactsProvider, "_p1_bulk_installed", False):
        return

    original_get_facts = fp.FactsProvider.get_facts

    def response_from_mapping(provider: fp.FactsProvider, mapping: dict[str, Any]) -> fp.FactsResponse:
        project_code = str(mapping.get("project_code") or "")
        mapping_failure = fp.entity_mapping_failure(mapping)
        metrics, missing, invalid = provider._metrics_from_row(mapping)
        reasons: list[str] = []
        if mapping_failure:
            reasons.append(mapping_failure)
        source_flag = mapping.get("facts_available")
        source_incomplete = source_flag is False or source_flag == 0 or (
            isinstance(source_flag, str)
            and source_flag.strip().lower() in {"false", "0", "no"}
        )
        if source_incomplete:
            reasons.append("analytics source marked facts_available=false")
        if missing:
            reasons.append("missing metrics: " + ", ".join(sorted(missing)))
        if invalid:
            reasons.append("invalid metrics: " + ", ".join(sorted(invalid)))

        available = not mapping_failure and not source_incomplete and not missing and not invalid
        reason = "; ".join(reasons) if reasons else None
        as_of = fp._iso_value(mapping.get("calculated_at") or mapping.get("data_date"))
        facts_version = fp._stable_facts_version(
            project_code,
            metrics=metrics,
            mapping=mapping,
            facts_available=available,
            reason=reason,
            source=fp.FACTS_SOURCE,
        )
        return fp.FactsResponse(
            project_code=project_code,
            as_of=as_of,
            facts_version=facts_version,
            metrics=metrics if available else {},
            status="AVAILABLE" if available else "DEGRADED",
            facts_available=available,
            reason=reason,
            source=fp.FACTS_SOURCE,
            entity_code=fp.normalize_entity_code(mapping.get("entity_code")),
            entity_mapping_status=(
                str(mapping.get("entity_mapping_status")).strip().upper()
                if mapping.get("entity_mapping_status") is not None
                else None
            ),
            entity_mapping_reason=(
                str(mapping.get("entity_mapping_reason")).strip()
                if mapping.get("entity_mapping_reason") is not None
                else None
            ),
            entity_mapping_valid=fp._optional_bool(mapping.get("entity_mapping_valid")),
        )

    def preload(provider: fp.FactsProvider) -> dict[str, fp.FactsResponse]:
        info = getattr(provider._db, "info", None)
        if isinstance(info, dict):
            existing = info.get("_canonical_facts_bulk")
            if isinstance(existing, dict):
                return existing
            if info.get("_canonical_facts_bulk_failed"):
                return {}

        try:
            rows = provider._db.execute(
                text("SELECT * FROM analytics_project_full ORDER BY project_code")
            ).mappings().all()
            responses = {
                str(row.get("project_code")): response_from_mapping(provider, dict(row))
                for row in rows
                if row.get("project_code")
            }
        except Exception:
            if isinstance(info, dict):
                info["_canonical_facts_bulk_failed"] = True
            return {}

        # Available facts may also populate the normal short-lived cache.
        for code, response in responses.items():
            provider._cache.set(provider._cache_key(code), response)
        if isinstance(info, dict):
            # Request scoped: DEGRADED rows are safe here but still do not enter
            # the process-global FactsCache.
            info["_canonical_facts_bulk"] = responses
        return responses

    def get_facts(
        provider: fp.FactsProvider,
        project_code: str,
        require_fresh: bool = False,
        max_age: int = fp.FACTS_CACHE_TTL_SECONDS,
        as_of: str | None = None,
    ) -> fp.FactsResponse:
        if as_of or require_fresh:
            return original_get_facts(
                provider,
                project_code,
                require_fresh=require_fresh,
                max_age=max_age,
                as_of=as_of,
            )

        info = getattr(provider._db, "info", None)
        if isinstance(info, dict):
            loaded = info.get("_canonical_facts_bulk")
            if isinstance(loaded, dict):
                response = loaded.get(project_code)
                if response is not None:
                    return response
                return fp._degraded_response(
                    project_code,
                    f"no canonical facts for project_code={project_code}",
                )

        loaded = preload(provider)
        if loaded:
            response = loaded.get(project_code)
            if response is not None:
                return response
            return fp._degraded_response(
                project_code,
                f"no canonical facts for project_code={project_code}",
            )

        # If bulk loading itself is unavailable, retain the original fail-closed
        # behavior for the requested project rather than fabricating a result.
        return original_get_facts(provider, project_code, max_age=max_age)

    def get_facts_bulk(
        provider: fp.FactsProvider,
        project_codes: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, fp.FactsResponse]:
        loaded = preload(provider)
        if project_codes is None:
            return dict(loaded)
        return {
            code: loaded.get(code)
            or fp._degraded_response(code, f"no canonical facts for project_code={code}")
            for code in project_codes
        }

    fp.FactsProvider.get_facts = get_facts
    fp.FactsProvider.get_facts_bulk = get_facts_bulk
    fp.FactsProvider._p1_bulk_installed = True
