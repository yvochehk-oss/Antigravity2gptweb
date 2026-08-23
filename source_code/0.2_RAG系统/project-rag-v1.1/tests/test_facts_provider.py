"""Facts Provider contract tests without a database-dialect fallback."""
from __future__ import annotations
import json
from facts_provider.facts_provider import FactsProvider, FactsResponse, MetricValue
from facts_provider.routes import FactsResponse as ApiFactsResponse

class _Result:
    def __init__(self,row): self._row=row
    def first(self): return self._row
    def all(self): return [] if self._row is None else [self._row]

class _FakeSession:
    bind=object()
    def __init__(self,current=None,historical=None,error=None):
        self.current=current; self.historical=historical; self.error=error
    def execute(self,statement,params=None):
        if self.error: raise self.error
        sql=str(statement)
        if 'facts_snapshots' in sql: return _Result(self.historical)
        return _Result(self.current)

def _complete_row(**changes):
    row={
      'project_code':'P-001','calculated_at':'2026-08-20T00:00:00+08:00','facts_available':True,
      'contract_amount':110.0,'recognized_revenue':100.0,'real_project_cost':60.0,'real_profit':40.0,
      'collected_amount':20.0,'unpaid_amount':80.0,'collection_rate':0.2,
      'eac_revenue':110.0,'eac_cost':70.0,'eac_profit':40.0,'eac_margin':0.3636,
      'cash_inflow':25.0,'cash_outflow':15.0,'net_cashflow':10.0,
      # Optional metrics deliberately remain unknown.
      'cash_gap_30d':None,'tax_burden_rate':None,'health_score':None,'cost_variance':2.0,
    }
    row.update(changes); return row

def test_metric_and_response_serialization_is_json_safe():
    response=FactsResponse(project_code='P-001',as_of='2026-08-20T00:00:00+08:00',facts_version='f_test',metrics={'real_profit':MetricValue(40,'2.1','CNY')})
    assert response.to_dict()['metrics']['real_profit']=={'value':40,'metric_version':'2.1','unit':'CNY'}
    assert ApiFactsResponse.from_domain(response).metrics['real_profit'].value==40

def test_optional_unknown_metrics_do_not_poison_deterministic_core():
    response=FactsProvider(_FakeSession(current=_complete_row())).get_facts('P-001',require_fresh=True)
    assert response.status=='AVAILABLE' and response.facts_available is True
    assert response.metrics['real_profit'].value==40.0
    assert response.metrics['net_cashflow'].value==10.0
    assert 'cash_gap_30d' not in response.metrics
    assert 'tax_burden_rate' not in response.metrics
    assert 'health_score' not in response.metrics

def test_missing_required_metric_is_degraded_without_partial_financial_output():
    response=FactsProvider(_FakeSession(current=_complete_row(eac_profit=None))).get_facts('P-001',require_fresh=True)
    assert response.status=='DEGRADED' and response.metrics=={}
    assert 'eac_profit' in (response.reason or '')

def test_invalid_required_metric_is_degraded():
    response=FactsProvider(_FakeSession(current=_complete_row(real_profit='not-a-number'))).get_facts('P-001',require_fresh=True)
    assert response.status=='DEGRADED' and response.metrics=={}
    assert 'real_profit' in (response.reason or '')

def test_source_completeness_flag_is_respected():
    response=FactsProvider(_FakeSession(current=_complete_row(facts_available=False))).get_facts('P-001',require_fresh=True)
    assert response.status=='DEGRADED' and response.metrics=={}
    assert 'facts_available=false' in (response.reason or '')

def test_missing_view_or_project_never_fabricates_amounts():
    unavailable=FactsProvider(_FakeSession(error=RuntimeError('offline'))).get_facts('P-001',require_fresh=True)
    missing=FactsProvider(_FakeSession(current=None)).get_facts('P-001',require_fresh=True)
    assert unavailable.metrics=={} and missing.metrics=={}
    assert '12500000' not in unavailable.to_json()+missing.to_json()

def test_historical_snapshot_accepts_same_core_boundary_with_optional_metrics_absent():
    payload={'facts_available':True,'metrics':{
      'recognized_revenue':{'value':100,'metric_version':'1.3','unit':'CNY'},
      'real_profit':{'value':40,'metric_version':'2.1','unit':'CNY'},
      'collection_rate':{'value':0.2,'metric_version':'2.0','unit':'ratio'},
      'eac_profit':{'value':35,'metric_version':'1.6','unit':'CNY'},
      'eac_margin':{'value':0.31,'metric_version':'2.0','unit':'ratio'},
    }}
    row={'facts_data':json.dumps(payload),'as_of':'2026-07-31T00:00:00+08:00','facts_version':'f_history'}
    response=FactsProvider(_FakeSession(historical=row)).get_facts('P-001',as_of='2026-08-01T00:00:00+08:00')
    assert response.status=='AVAILABLE' and response.metrics['real_profit'].value==40.0
