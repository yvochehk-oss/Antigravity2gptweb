"""Task32 fail-closed HTTP error mapping regression tests."""
from __future__ import annotations

from app.integration.idp_canonical.service import CanonicalIngestRejected
from app.routers.v3_canonical import _coded_domain_http_error


def test_task24_rejection_is_preserved_as_structured_http_409():
    error = CanonicalIngestRejected("SOURCE_NOT_APPROVED", "source must be approved")
    mapped = _coded_domain_http_error(error)
    assert mapped is not None
    assert mapped.status_code == 409
    assert mapped.detail == {
        "code": "SOURCE_NOT_APPROVED",
        "detail": "source must be approved",
    }


def test_unknown_runtime_error_is_not_mislabeled_as_business_rejection():
    assert _coded_domain_http_error(RuntimeError("programming failure")) is None
