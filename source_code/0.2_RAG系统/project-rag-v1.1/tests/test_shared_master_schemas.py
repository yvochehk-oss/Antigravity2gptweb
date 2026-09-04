from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.schemas import EntityCreate, ExternalPartyCreate, ProjectCreate


def test_project_requires_real_contract_amount_and_location():
    p = ProjectCreate(project_code="P-001", name="Project", contract_amount=Decimal("1.00"), location="成都")
    assert p.contract_amount == Decimal("1.00")
    with pytest.raises(ValidationError):
        ProjectCreate(project_code="P-002", name="Project", contract_amount=0, location="成都")
    with pytest.raises(ValidationError):
        ProjectCreate(project_code="P-003", name="Project", contract_amount=1, location="")


def test_entity_role_is_derived_from_canonical_code():
    e = EntityCreate(entity_code="B10", name="Trade entity")
    assert e.business_role == "B"
    with pytest.raises(ValidationError):
        EntityCreate(entity_code="B10", name="Trade entity", business_role="A")


def test_a04_branch_semantics_are_fail_closed():
    e = EntityCreate(entity_code="A04", name="Branch", legal_entity=False, parent_entity_code="A03")
    assert e.entity_kind == "branch"
    with pytest.raises(ValidationError):
        EntityCreate(entity_code="A04", name="Bad branch")


def test_external_party_rejects_internal_master_code():
    with pytest.raises(ValidationError):
        ExternalPartyCreate(code="D03", name="Internal")
    ext = ExternalPartyCreate(code="E01", name="External owner")
    assert ext.code == "E01"
