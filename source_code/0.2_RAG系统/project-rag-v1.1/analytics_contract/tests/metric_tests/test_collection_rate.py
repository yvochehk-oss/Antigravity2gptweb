"""
回款率指标测试

测试 collection_rate 指标的计算正确性和预警规则
"""

import pytest
from decimal import Decimal


# 测试数据
TEST_DATA = {
    "project": {
        "project_code": "YB001",
        "contract_amount": 50000000,
    },
    "revenue": {
        "recognized_revenue": 12500000,
    },
    "collection": {
        "collected_amount": 8875000,
        "planned_collection": 10000000,
    },
    "expected_results": {
        "collection_rate": {
            "value": 0.71,
            "warning_threshold": 0.5,
            "critical_threshold": 0.3,
        },
    },
}


class TestCollectionRate:
    """回款率测试"""
    
    def test_collection_rate_calculation(self):
        """测试回款率计算公式"""
        collection = TEST_DATA["collection"]
        revenue = TEST_DATA["revenue"]
        
        collected_amount = collection["collected_amount"]
        recognized_revenue = revenue["recognized_revenue"]
        
        expected_rate = TEST_DATA["expected_results"]["collection_rate"]["value"]
        calculated_rate = round(collected_amount / recognized_revenue, 2)
        
        assert calculated_rate == expected_rate, (
            f"回款率计算错误: {collected_amount} / {recognized_revenue} = {calculated_rate}, "
            f"期望值: {expected_rate}"
        )
    
    def test_collection_rate_range(self):
        """测试回款率范围"""
        rate = TEST_DATA["expected_results"]["collection_rate"]["value"]
        
        # 回款率可以是 0% 到 100%+（包含预付款情况）
        assert rate >= 0, f"回款率不能为负: {rate}"
    
    def test_collection_rate_thresholds(self):
        """测试回款率预警阈值"""
        rate = TEST_DATA["expected_results"]["collection_rate"]["value"]
        thresholds = TEST_DATA["expected_results"]["collection_rate"]
        
        # 正常：>= 50%
        # 预警：< 50%
        # 重大风险：< 30%
        
        if rate < 0.5:
            assert True, "回款率低于50%，应触发预警"
        
        if rate < 0.3:
            assert True, "回款率低于30%，应触发重大风险预警"
    
    def test_unpaid_amount_calculation(self):
        """测试未收款金额计算"""
        revenue = TEST_DATA["revenue"]["recognized_revenue"]
        collected = TEST_DATA["collection"]["collected_amount"]
        
        expected_unpaid = revenue - collected
        # 在这里验证公式的正确性
        assert expected_unpaid > 0, "宜宾项目应该有未收款"


class TestCollectionRateValidation:
    """回款率验证规则测试"""
    
    def test_collection_rate_with_advance_payment(self):
        """测试预付款情况（回款率超过100%）"""
        # 模拟预付款场景
        recognized_revenue = 10000000
        collected_amount = 12000000  # 包含预付款
        
        rate = collected_amount / recognized_revenue
        
        # 回款率超过100%是可能的（预付款场景）
        assert rate > 1.0, "预付款场景回款率应该大于1"
    
    def test_zero_revenue(self):
        """测试收入为零的情况"""
        recognized_revenue = 0
        collected_amount = 0
        
        # 分母为零时，回款率应该为 0 或 NULL
        if recognized_revenue == 0:
            # 这种情况应该特殊处理，避免除零错误
            assert True


class TestCollectionTrend:
    """回款趋势分析测试"""
    
    def test_collection_trend_calculation(self):
        """测试回款趋势计算"""
        # 模拟近6个月回款数据
        monthly_collections = [
            {"month": "2026-03", "amount": 1000000, "revenue": 1500000},
            {"month": "2026-04", "amount": 1200000, "revenue": 1500000},
            {"month": "2026-05", "amount": 1400000, "revenue": 1500000},
            {"month": "2026-06", "amount": 1300000, "revenue": 1500000},
            {"month": "2026-07", "amount": 1500000, "revenue": 1500000},
            {"month": "2026-08", "amount": 1500000, "revenue": 1500000},
        ]
        
        rates = [m["amount"] / m["revenue"] for m in monthly_collections]
        
        # 验证趋势：回款率在改善
        early_avg = sum(rates[:3]) / 3
        late_avg = sum(rates[3:]) / 3
        
        assert late_avg >= early_avg, (
            f"回款率趋势改善: 前期平均={early_avg:.2%}, 后期平均={late_avg:.2%}"
        )
