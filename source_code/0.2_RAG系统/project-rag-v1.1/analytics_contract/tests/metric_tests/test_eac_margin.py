"""Management EAC v2.0 formula tests."""
import pytest

def _estimate(contract_amount, recognized_revenue, actual_cost):
    if not contract_amount or not recognized_revenue or actual_cost is None: return None
    progress=recognized_revenue/contract_amount
    if progress<=0: return None
    eac_cost=actual_cost/progress
    eac_revenue=contract_amount
    eac_profit=eac_revenue-eac_cost
    return eac_cost,eac_revenue,eac_profit,eac_profit/eac_revenue

def test_management_eac_revenue_progress_proxy():
    cost,revenue,profit,margin=_estimate(50_000_000,12_500_000,11_800_000)
    assert cost==pytest.approx(47_200_000)
    assert revenue==50_000_000
    assert profit==pytest.approx(2_800_000)
    assert margin==pytest.approx(0.056)

def test_eac_is_unavailable_without_real_progress_proxy():
    assert _estimate(50_000_000,0,1_000_000) is None

def test_group_margin_is_weighted_not_average():
    projects=[(1_000_000,10_000_000),(500_000,20_000_000)]
    weighted=sum(p for p,_ in projects)/sum(r for _,r in projects)
    simple=sum(p/r for p,r in projects)/len(projects)
    assert weighted==pytest.approx(0.05)
    assert weighted!=pytest.approx(simple)
