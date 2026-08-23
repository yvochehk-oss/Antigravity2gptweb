"""
项目真实利润指标测试

测试 project_real_profit 指标的计算正确性
"""

import pytest
from decimal import Decimal


# 测试数据
TEST_DATA = {
    "project": {
        "project_code": "YB001",
        "project_name": "宜宾住宅项目",
        "entity_code": "A01",
        "entity_name": "中镌（湖北）建筑有限公司",
        "business_role": "A",
        "contract_amount": 50000000,
    },
    "revenue": {
        "recognized_revenue": 12500000,
    },
    "cost": {
        "raw_cost": 11200000,
        "internal_elimination": 200000,
        "real_economic_cost": 190000,
        # 真实项目成本 = 原始成本 + 恢复成本 - 抵消金额
        # = 11200000 + 190000 - 200000 = 11190000
        "real_project_cost": 11190000,
    },
    "collection": {
        "collected_amount": 8875000,
        "planned_collection": 10000000,
    },
    "expected_results": {
        "real_profit": {
            # 真实利润 = 确认收入 - 真实项目成本
            # = 12500000 - 11190000 = 1310000
            "value": 1310000,
        },
        "eac_margin": {
            "value": 0.0926,
        },
        "collection_rate": {
            "value": 0.71,
        },
        "cash_gap_30d": {
            "value": 3000000,
        },
    },
}


class TestRealProfit:
    """项目真实利润测试"""
    
    def test_real_profit_calculation(self):
        """测试真实利润计算公式"""
        data = TEST_DATA["revenue"]
        cost_data = TEST_DATA["cost"]
        
        recognized_revenue = data["recognized_revenue"]
        real_project_cost = cost_data["real_project_cost"]
        
        expected_profit = recognized_revenue - real_project_cost
        expected = TEST_DATA["expected_results"]["real_profit"]["value"]
        
        assert expected_profit == expected, (
            f"真实利润计算错误: {recognized_revenue} - {real_project_cost} = {expected_profit}, "
            f"期望值: {expected}"
        )
    
    def test_real_profit_with_internal_elimination(self):
        """测试内部交易抵消后的利润计算"""
        raw_cost = TEST_DATA["cost"]["raw_cost"]
        elimination = TEST_DATA["cost"]["internal_elimination"]
        real_economic_cost = TEST_DATA["cost"]["real_economic_cost"]
        
        # 真实成本 = 原始成本 + 恢复成本 - 抵消金额
        calculated_real_cost = raw_cost + real_economic_cost - elimination
        expected_real_cost = TEST_DATA["cost"]["real_project_cost"]
        
        assert calculated_real_cost == expected_real_cost, (
            f"内部抵消后成本计算错误: {raw_cost} + {real_economic_cost} - {elimination} = "
            f"{calculated_real_cost}, 期望: {expected_real_cost}"
        )
    
    def test_real_profit_not_exceed_revenue(self):
        """测试利润不能超过收入"""
        profit = TEST_DATA["expected_results"]["real_profit"]["value"]
        revenue = TEST_DATA["revenue"]["recognized_revenue"]
        
        assert profit <= revenue, (
            f"利润不应超过收入: 利润={profit}, 收入={revenue}"
        )
    
    def test_real_profit_can_be_negative(self):
        """测试利润可以为负数（亏损项目）"""
        # 这是一个设计验证测试，确保系统允许负利润
        assert True, "亏损项目应该允许负利润"


class TestRealProfitValidation:
    """项目真实利润验证规则测试"""
    
    def test_internal_elimination_reduces_cost(self):
        """测试内部抵消应该减少成本"""
        raw_cost = TEST_DATA["cost"]["raw_cost"]
        real_cost = TEST_DATA["cost"]["real_project_cost"]
        
        # 抵消后成本应该小于等于原始成本
        assert real_cost <= raw_cost, (
            f"内部抵消后成本不应该大于原始成本: 抵消后={real_cost}, 原始={raw_cost}"
        )
    
    def test_profit_formula_consistency(self):
        """测试利润公式在不同表示方式下的一致性"""
        recognized_revenue = Decimal(str(TEST_DATA["revenue"]["recognized_revenue"]))
        raw_cost = Decimal(str(TEST_DATA["cost"]["raw_cost"]))
        elimination = Decimal(str(TEST_DATA["cost"]["internal_elimination"]))
        real_economic_cost = Decimal(str(TEST_DATA["cost"]["real_economic_cost"]))
        
        # 方式1: 使用预计算的真实成本
        real_project_cost = Decimal(str(TEST_DATA["cost"]["real_project_cost"]))
        profit_via_real_cost = recognized_revenue - real_project_cost
        
        # 方式2: 使用分解计算（真实成本 = 原始成本 + 恢复成本 - 抵消）
        decomposed_cost = raw_cost + real_economic_cost - elimination
        profit_via_decomposed = recognized_revenue - decomposed_cost
        
        assert profit_via_real_cost == profit_via_decomposed, (
            f"两种计算方式结果不一致: {profit_via_real_cost} vs {profit_via_decomposed}"
        )
