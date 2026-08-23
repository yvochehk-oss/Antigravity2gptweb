from decimal import Decimal as D
from app.domain.entities import CANONICAL_ENTITY_CODES, CANONICAL_ENTITY_RANGE_TEXT
from app.planning.engine import PartyProfile, PlanningRequest, build_scenarios

def test_canonical_internal_units_are_26():
    assert len(CANONICAL_ENTITY_CODES)==26
    assert CANONICAL_ENTITY_RANGE_TEXT=='A01-A11, B01-B10, C01-C02, D01-D03'

def test_30m_labor_scenarios_balance_exactly():
    profiles=[
      PartyProfile('C01','internal','C',capacity=D('20000000'),external_cost_ratio=D('0.82'),tax_cash_rate=D('0.025'),risk_score=D('0.30'),evidence_quality=D('0.90')),
      PartyProfile('C02','internal','C',capacity=D('18000000'),external_cost_ratio=D('0.86'),tax_cash_rate=D('0.020'),risk_score=D('0.25'),evidence_quality=D('0.85')),
      PartyProfile('EXT-L01','external','labor',capacity=D('30000000'),external_cost_ratio=D('1'),tax_cash_rate=D('-0.03'),risk_score=D('0.20'),evidence_quality=D('0.90')),
    ]
    req=PlanningRequest(D('30000000'),'劳务','balanced',D('0.3'),D('0.9'),D('0.65'))
    rows=build_scenarios(req,profiles)
    assert rows
    for s in rows:
      assert s.internal_amount+s.external_amount==D('30000000.00')
      assert sum((x.amount for x in s.allocations),D('0'))==D('30000000.00')
      assert D('0.3')<=s.internal_ratio<=D('0.9')

def test_capacity_is_hard_constraint_not_score_penalty():
    profiles=[
      PartyProfile('C01','internal',capacity=D('5000000'),external_cost_ratio=D('0.5'),risk_score=D('0.1'),evidence_quality=D('1')),
      PartyProfile('EXT-L01','external',capacity=D('30000000'),external_cost_ratio=D('1'),risk_score=D('0.2'),evidence_quality=D('1')),
    ]
    req=PlanningRequest(D('30000000'),'劳务','profit',D('0'),D('1'))
    rows=build_scenarios(req,profiles)
    assert all(s.internal_amount<=D('5000000.00') for s in rows)

def test_internal_allocation_is_penetrated_to_external_cost():
    profiles=[PartyProfile('C01','internal',external_cost_ratio=D('0.8'),risk_score=D('0.2'),evidence_quality=D('1'))]
    s=build_scenarios(PlanningRequest(D('1000'),'劳务'),profiles)[0]
    assert s.internal_amount==D('1000.00')
    assert s.external_amount==D('0.00')
    assert s.system_external_cost==D('800.00')
    assert s.savings_vs_all_external==D('200.00')
