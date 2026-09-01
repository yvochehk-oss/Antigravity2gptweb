"""Task29 focused tests: explicit project attribution, no inference, no new schema."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.integration.idp_canonical.project_allocation_basis import (
    CONTRACT_PROJECT_BASIS_V1,
    INVOICE_PROJECT_BASIS_V1,
    PAYMENT_PROJECT_BASIS_V1,
    TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
    ProjectAllocationValidationError,
    build_project_allocation_basis,
    build_project_allocation_plans,
)
from app.integration.idp_canonical.project_attribution_schemas import (
    ProjectAllocationInput,
    ProjectAttributionRequest,
)


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app/integration/idp_canonical/project_attribution_service.py"
RESOLVER = ROOT / "app/integration/idp_canonical/project_attribution_resolver.py"
GATE = ROOT / "scripts/v3/project_attribution_gate_29.py"
MIGRATIONS = ROOT / "alembic/versions"


def invoice(net="100.00", vat="13.00", gross="113.00"):
    return SimpleNamespace(net_amount=Decimal(net) if net is not None else None, vat_amount=Decimal(vat) if vat is not None else None, gross_amount=Decimal(gross) if gross is not None else None)


def payment(amount="100.00"):
    return SimpleNamespace(amount=Decimal(amount) if amount is not None else None)


def contract(amount="100.00"):
    return SimpleNamespace(contract_amount=Decimal(amount) if amount is not None else None)


def test_task29_ruleset_is_versioned():
    assert TASK29_PROJECT_ATTRIBUTION_RULESET_V1 == "TASK29_PROJECT_ATTRIBUTION_V1"


def test_basis_versions_are_explicit():
    assert INVOICE_PROJECT_BASIS_V1.endswith("_V1")
    assert PAYMENT_PROJECT_BASIS_V1.endswith("_V1")
    assert CONTRACT_PROJECT_BASIS_V1.endswith("_V1")


def test_project_code_nfkc_and_trim_only():
    item = ProjectAllocationInput(project_code="  ＡＢＣ-001  ")
    assert item.project_code == "ABC-001"


def test_project_name_is_not_an_allowed_request_field():
    with pytest.raises(ValidationError):
        ProjectAllocationInput(project_code="P001", project_name="fuzzy hint")


def test_request_requires_reviewer():
    with pytest.raises(ValidationError):
        ProjectAttributionRequest(fact_id=1, allocations=[ProjectAllocationInput(project_code="P001")], reviewed_by="   ")


def test_invoice_basis_uses_canonical_components():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    assert (basis.net, basis.vat, basis.gross) == (Decimal("100.00"), Decimal("13.00"), Decimal("113.00"))
    assert basis.basis_version == INVOICE_PROJECT_BASIS_V1


def test_invoice_basis_requires_all_components():
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice(vat=None))
    assert exc.value.code == "INVOICE_BASIS_INCOMPLETE"


def test_invoice_basis_must_balance():
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice(gross="114.00"))
    assert exc.value.code == "ALLOCATION_COMPONENT_IMBALANCE"


def test_payment_basis_is_project_monetary_basis_not_tax_decomposition():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    assert (basis.net, basis.vat, basis.gross) == (Decimal("100.00"), Decimal("0.00"), Decimal("100.00"))
    assert "NOT_TAX_DECOMPOSITION" in basis.audit_note


def test_payment_basis_requires_positive_amount():
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment("0.00"))
    assert exc.value.code == "PAYMENT_BASIS_INVALID"


def test_contract_basis_is_project_monetary_basis_not_tax_decomposition():
    basis = build_project_allocation_basis(fact_type="CONTRACT", specialized_fact=contract())
    assert (basis.net, basis.vat, basis.gross) == (Decimal("100.00"), Decimal("0.00"), Decimal("100.00"))
    assert "NOT_TAX_DECOMPOSITION" in basis.audit_note


def test_contract_basis_requires_amount():
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_basis(fact_type="CONTRACT", specialized_fact=contract(None))
    assert exc.value.code == "CONTRACT_BASIS_INCOMPLETE"


def test_unsupported_fact_type_fails_closed():
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_basis(fact_type="FULFILLMENT", specialized_fact=SimpleNamespace())
    assert exc.value.code == "UNSUPPORTED_FACT_TYPE"


def test_invoice_single_project_can_derive_full_basis():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    plans = build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001")])
    assert [(p.net, p.vat, p.gross) for p in plans] == [(Decimal("100.00"), Decimal("13.00"), Decimal("113.00"))]


def test_payment_single_project_can_derive_full_basis():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    plans = build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001")])
    assert plans[0].net == plans[0].gross == Decimal("100.00") and plans[0].vat == 0


def test_contract_single_project_can_derive_full_basis():
    basis = build_project_allocation_basis(fact_type="CONTRACT", specialized_fact=contract())
    plans = build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001")])
    assert plans[0].net == plans[0].gross == Decimal("100.00") and plans[0].vat == 0


def test_invoice_multi_project_explicit_split():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    plans = build_project_allocation_plans(basis=basis, allocations=[
        ProjectAllocationInput(project_code="P001", allocated_net="60.00", allocated_vat="7.80", allocated_gross="67.80"),
        ProjectAllocationInput(project_code="P002", allocated_net="40.00", allocated_vat="5.20", allocated_gross="45.20"),
    ])
    assert len(plans) == 2 and sum((p.gross for p in plans), Decimal("0")) == Decimal("113.00")


def test_payment_multi_project_explicit_split():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    plans = build_project_allocation_plans(basis=basis, allocations=[
        ProjectAllocationInput(project_code="P001", allocated_amount="60.00"),
        ProjectAllocationInput(project_code="P002", allocated_amount="40.00"),
    ])
    assert [(p.net, p.vat, p.gross) for p in plans] == [(Decimal("60.00"), Decimal("0.00"), Decimal("60.00")), (Decimal("40.00"), Decimal("0.00"), Decimal("40.00"))]


def test_contract_multi_project_explicit_split():
    basis = build_project_allocation_basis(fact_type="CONTRACT", specialized_fact=contract())
    plans = build_project_allocation_plans(basis=basis, allocations=[
        ProjectAllocationInput(project_code="P001", allocated_amount="25.00"),
        ProjectAllocationInput(project_code="P002", allocated_amount="75.00"),
    ])
    assert sum((p.gross for p in plans), Decimal("0")) == Decimal("100.00")


def test_duplicate_project_codes_fail_closed():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[
            ProjectAllocationInput(project_code="P001", allocated_amount="50.00"),
            ProjectAllocationInput(project_code="P001", allocated_amount="50.00"),
        ])
    assert exc.value.code == "DUPLICATE_PROJECT_CODE"


def test_invoice_multi_requires_all_three_components():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[
            ProjectAllocationInput(project_code="P001", allocated_net="60.00", allocated_vat="7.80"),
            ProjectAllocationInput(project_code="P002", allocated_net="40.00", allocated_vat="5.20", allocated_gross="45.20"),
        ])
    assert exc.value.code == "INVOICE_ALLOCATION_INCOMPLETE"


def test_invoice_rejects_allocated_amount_shortcut():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001", allocated_amount="113.00")])
    assert exc.value.code == "INVOICE_ALLOCATION_SHAPE_INVALID"


def test_payment_rejects_caller_vat_decomposition():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001", allocated_net="87.00", allocated_vat="13.00", allocated_gross="100.00")])
    assert exc.value.code == "MONETARY_ALLOCATION_SHAPE_INVALID"


def test_contract_rejects_caller_vat_decomposition():
    basis = build_project_allocation_basis(fact_type="CONTRACT", specialized_fact=contract())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001", allocated_net="87.00", allocated_vat="13.00", allocated_gross="100.00")])
    assert exc.value.code == "MONETARY_ALLOCATION_SHAPE_INVALID"


def test_multi_payment_requires_explicit_amount_for_each_project():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001"), ProjectAllocationInput(project_code="P002", allocated_amount="100.00")])
    assert exc.value.code == "MONETARY_ALLOCATION_INCOMPLETE"


def test_invoice_row_balance_enforced():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001", allocated_net="100.00", allocated_vat="13.00", allocated_gross="112.00")])
    assert exc.value.code == "ALLOCATION_COMPONENT_IMBALANCE"


def test_aggregate_conservation_enforced():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[
            ProjectAllocationInput(project_code="P001", allocated_amount="60.00"),
            ProjectAllocationInput(project_code="P002", allocated_amount="39.00"),
        ])
    assert exc.value.code == "ALLOCATION_AGGREGATE_MISMATCH"


def test_money_precision_more_than_cents_rejected():
    basis = build_project_allocation_basis(fact_type="PAYMENT", specialized_fact=payment())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[ProjectAllocationInput(project_code="P001", allocated_amount="100.001")])
    assert exc.value.code == "MONEY_PRECISION_EXCEEDED"


def test_negative_invoice_basis_can_split_with_same_sign():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice("-100.00", "-13.00", "-113.00"))
    plans = build_project_allocation_plans(basis=basis, allocations=[
        ProjectAllocationInput(project_code="P001", allocated_net="-60.00", allocated_vat="-7.80", allocated_gross="-67.80"),
        ProjectAllocationInput(project_code="P002", allocated_net="-40.00", allocated_vat="-5.20", allocated_gross="-45.20"),
    ])
    assert sum((p.gross for p in plans), Decimal("0")) == Decimal("-113.00")


def test_allocation_sign_may_not_reverse_fact_basis():
    basis = build_project_allocation_basis(fact_type="INVOICE", specialized_fact=invoice())
    with pytest.raises(ProjectAllocationValidationError) as exc:
        build_project_allocation_plans(basis=basis, allocations=[
            ProjectAllocationInput(project_code="P001", allocated_net="110.00", allocated_vat="14.30", allocated_gross="124.30"),
            ProjectAllocationInput(project_code="P002", allocated_net="-10.00", allocated_vat="-1.30", allocated_gross="-11.30"),
        ])
    assert exc.value.code == "ALLOCATION_SIGN_CONFLICT"


def test_resolver_is_exact_project_code_only():
    source = RESOLVER.read_text(encoding="utf-8")
    lower = source.lower()
    assert "Project.project_code.in_(codes)" in source
    assert "ilike" not in lower
    assert ".like(" not in lower
    assert "Project.name" not in source
    assert "Project.project_name" not in source


def test_resolver_never_creates_project():
    source = RESOLVER.read_text(encoding="utf-8")
    assert "Project(" not in source


def test_service_reuses_task17_allocation_model():
    source = SERVICE.read_text(encoding="utf-8")
    assert "FactProjectAllocation(" in source
    assert "TASK29_PROJECT_ATTRIBUTION_RULESET_V1" in source


def test_service_never_creates_fact_or_relationship_or_tax_analysis():
    source = SERVICE.read_text(encoding="utf-8")
    assert "Fact(" not in source
    assert "FactRelationship" not in source
    assert "ProjectTaxAnalysis" not in source


def test_service_does_not_assign_fact_validation_or_identity_fields():
    source = SERVICE.read_text(encoding="utf-8")
    fields = ["validation_status", "business_identity_key", "version_no", "supersedes_fact_id", "is_current"]
    assert all(re.search(rf"fact\.{field}\s*=(?!=)", source) is None for field in fields)


def test_service_does_not_auto_supersede_allocations():
    source = SERVICE.read_text(encoding="utf-8")
    assert "supersedes_allocation_id=None" in source
    assert ".supersedes_allocation_id =" not in source
    assert ".is_current = False" not in source


def test_service_supports_validated_source_document_audit():
    source = SERVICE.read_text(encoding="utf-8")
    assert 'document.status != "VALIDATED"' in source
    assert 'allocation_method = "SOURCE_DOCUMENT"' in source
    assert 'proposal_source = "DOCUMENT"' in source


def test_service_does_not_use_party_or_amount_direction_for_project_inference():
    source = SERVICE.read_text(encoding="utf-8")
    for token in ("buyer_party_id", "seller_party_id", "payer_party_id", "payee_party_id", "amount_direction", "date_proximity"):
        assert token not in source


def test_gate_exists_and_requires_same_alembic_head_support():
    source = GATE.read_text(encoding="utf-8")
    assert '"S29"' in source
    assert "EXPECTED_HEAD" in source
    assert "ProductionRouteGuard" in source
    assert "production_seal_unchanged" in source
    assert "cutover_state_unchanged" in source


def test_task29_does_not_add_migration_98_or_later():
    task29_named = [path.name for path in MIGRATIONS.glob("*.py") if path.name.startswith(("98_", "99_", "100_"))]
    assert task29_named == []
