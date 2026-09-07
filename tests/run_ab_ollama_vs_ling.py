#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chengdu Construction V3.0 - 01/02/03 Project Archives A/B Benchmark
Compares Ling-3.0-tiny (Port 8930) vs Ollama Served Model (Port 11434)
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, List
import httpx

LING_API_URL = os.environ.get("LING_API_URL", "http://127.0.0.1:8930/v1/chat/completions")
OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434/v1/chat/completions")
OLLAMA_MODEL_NAME = os.environ.get("OLLAMA_MODEL_NAME", "qwen2.5:3b")

TEST_CASES = [
    {
        "id": "PROJ-01",
        "category": "01项目合同质保金",
        "prompt": "【证据材料】[EVD-01] 《天府国际金融中心二期施工主合同》第8.2条：工程质量保证金按工程结算总额的3%预留（计4350万元），缺陷责任期自实际竣工验收合格之日起满24个月后30日内无息结清。\n【问题】请依据证据说明天府新区项目的质量保证金比例是多少？结清期限是多久？",
        "expected_keywords": ["3%", "24个月", "无息", "30日"],
        "expected_numbers": [3, 24, 30],
        "format": "text"
    },
    {
        "id": "PROJ-02",
        "category": "02项目逾期违约金",
        "prompt": "【证据材料】[EVD-02] 《成渝双城经济圈跨江特大桥合同》第12.1条：逾期竣工违约金按每日合同总价的万分之三计算，违约金累计最高不超过合同结算总价的5%。\n【问题】该项目的逾期违约金每日计提比例是多少？最高违约金上限是多少？",
        "expected_keywords": ["万分之三", "5%"],
        "expected_numbers": [5],
        "format": "text"
    },
    {
        "id": "PROJ-03",
        "category": "03项目简易计税预缴",
        "prompt": "广元利州项目采取一般计税方法，如果发生异地简易计税项目，取得含税工程款 1030 万元，支付分包款 206 万元。请按差额征税公式计算应预缴的增值税额（万元）。",
        "expected_keywords": ["24", "差额", "3%"],
        "expected_numbers": [24.0],
        "format": "text"
    },
    {
        "id": "TAX-01",
        "category": "增值税价税分离",
        "prompt": "01项目天府金融中心第1期工程预付款收到含税金额 14500 万元（税率 9%）。请计算该笔工程款对应的增值税不含税销售额和销项税额（万元，保留两位小数）。",
        "expected_keywords": ["13302.75", "1197.25"],
        "expected_numbers": [13302.75, 1197.25],
        "format": "text"
    },
    {
        "id": "TAX-02",
        "category": "进项税额转出",
        "prompt": "某施工项目发生因管理不善导致的钢材被盗损失，损失账面成本为 100 万元（采购时已抵扣 13% 进项税）。请问按税法规定应做进项税额转出多少万元？",
        "expected_keywords": ["13", "转出", "管理不善"],
        "expected_numbers": [13.0],
        "format": "text"
    },
    {
        "id": "FF-01",
        "category": "四流一致性审查",
        "prompt": "【业务场景】01项目部与劳务公司 B01 签订合同，发票由 B01 开具给总包 A08，但资金由项目经理个人银行卡转账给自然人张某。请指出四流中何处存在严重违规及税务风险。",
        "expected_keywords": ["资金流", "公对私", "虚开", "不得抵扣"],
        "format": "text"
    },
    {
        "id": "FF-02",
        "category": "发票抬头不符合",
        "prompt": "【业务场景】钢材运抵天府新区施工现场并验收，但发票开票抬头写成了集团本部 A01，实际采购与付款主体为天府建设 A08。请指出发票流异常及处理方案。",
        "expected_keywords": ["抬头不一致", "作废重开", "红冲", "发票流"],
        "format": "text"
    },
    {
        "id": "FACTS-01",
        "category": "Facts 风险审查",
        "prompt": "【确定性指标】CD-GX-004 项目确认收入 4500 万元，真实利润 -230.5 万元，30天现金缺口 820 万元，回款率 48.5%。请评估财务风险并给出建议。",
        "expected_keywords": ["亏损", "-230.5", "现金缺口", "820", "48.5%"],
        "expected_numbers": [-230.5, 820.0, 48.5],
        "format": "text"
    },
    {
        "id": "JSON-01",
        "category": "纯 JSON 结构化输出",
        "prompt": "【证据包】[EVD-A1] 成都高新项目回款延迟45天，涉及逾期款520万元。[EVD-A2] 钢筋抽检合格率仅82%。\n【要求】以纯 JSON 格式输出，不要包含 Markdown 代码围栏。字段包含：summary, risk_level, evidence_refs。",
        "expected_keywords": ["summary", "risk_level", "EVD-A1", "EVD-A2"],
        "format": "json"
    },
    {
        "id": "JSON-02",
        "category": "证据编号关联引用",
        "prompt": "【证据包】[EVD-B1] 环保停工18天产生窝工损失35万元。\n【要求】以纯 JSON 格式输出，必须准确引用证据编号 EVD-B1。字段：summary, evidence_refs。",
        "expected_keywords": ["EVD-B1", "summary"],
        "format": "json"
    }
]

def evaluate_case_on_api(api_url: str, model_name: str, case: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一个专业的建筑工程与财税智能分析专家。"},
            {"role": "user", "content": case["prompt"]}
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "stream": True
    }
    
    start_time = time.perf_counter()
    first_token_time = None
    generated_text = ""
    token_count = 0
    
    try:
        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", api_url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    return {
                        "id": case["id"],
                        "category": case["category"],
                        "success": False,
                        "error": f"HTTP {response.status_code}",
                        "ttft_ms": 0,
                        "tokens_per_sec": 0,
                        "business_correct": False,
                        "output": ""
                    }
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            if first_token_time is None:
                                first_token_time = time.perf_counter()
                            generated_text += delta
                            token_count += 1
                    except Exception:
                        pass
    except Exception as e:
        return {
            "id": case["id"],
            "category": case["category"],
            "success": False,
            "error": str(e),
            "ttft_ms": 0,
            "tokens_per_sec": 0,
            "business_correct": False,
            "output": ""
        }
        
    total_time = time.perf_counter() - start_time
    ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else total_time * 1000
    gen_time = total_time - (ttft_ms / 1000)
    tok_per_sec = token_count / gen_time if gen_time > 0 and token_count > 0 else 0
    
    # 关键词校验
    kw_hits = sum(1 for kw in case.get("expected_keywords", []) if kw.lower() in generated_text.lower())
    expected_kws = case.get("expected_keywords", [])
    kw_score = kw_hits / len(expected_kws) if expected_kws else 1.0
    
    # 数字匹配
    num_hits = 0
    expected_nums = case.get("expected_numbers", [])
    for num in expected_nums:
        if str(num) in generated_text or str(int(num)) in generated_text:
            num_hits += 1
    num_score = num_hits / len(expected_nums) if expected_nums else 1.0
    
    business_correct = (kw_score >= 0.4) and (num_score >= 0.5)
    
    # JSON 校验
    json_valid = True
    if case.get("format") == "json":
        clean_text = re.sub(r"^```[a-zA-Z]*\n?", "", generated_text.strip())
        clean_text = re.sub(r"\n?```$", "", clean_text).strip()
        try:
            json.loads(clean_text)
        except Exception:
            json_valid = False
            
    return {
        "id": case["id"],
        "category": case["category"],
        "success": True,
        "ttft_ms": round(ttft_ms, 1),
        "tokens_per_sec": round(tok_per_sec, 1),
        "business_correct": business_correct,
        "json_valid": json_valid,
        "kw_score": round(kw_score, 2),
        "output_snippet": generated_text[:120].replace("\n", " ")
    }

def run_ab_benchmark():
    print("==================================================")
    print(f"🚀 启动 01/02/03 项目存档 A/B 测试 [Ling 3.0 (8930) vs Ollama ({OLLAMA_MODEL_NAME} @ 11434)]")
    print("==================================================")
    
    ling_results = []
    ollama_results = []
    
    for idx, case in enumerate(TEST_CASES):
        sys.stdout.write(f"\r[{idx+1}/{len(TEST_CASES)}] 测试用例: {case['id']} ({case['category']})...")
        sys.stdout.flush()
        
        # 1. 跑 Ling 3.0
        r_ling = evaluate_case_on_api(LING_API_URL, "ling-3.0-tiny", case)
        ling_results.append(r_ling)
        
        # 2. 跑 Ollama 依赖模型
        r_ollama = evaluate_case_on_api(OLLAMA_API_URL, OLLAMA_MODEL_NAME, case)
        ollama_results.append(r_ollama)
        
    print("\n✅ 所有用例测试完成！正在汇总生成 A/B 对比报告...")
    
    # 统计数据
    ling_valid = [r for r in ling_results if r["success"]]
    ollama_valid = [r for r in ollama_results if r["success"]]
    
    ling_acc = round(sum(1 for r in ling_valid if r["business_correct"]) / len(TEST_CASES) * 100, 1)
    ollama_acc = round(sum(1 for r in ollama_valid if r["business_correct"]) / len(TEST_CASES) * 100, 1)
    
    ling_ttft = round(sum(r["ttft_ms"] for r in ling_valid) / len(ling_valid), 1) if ling_valid else 0
    ollama_ttft = round(sum(r["ttft_ms"] for r in ollama_valid) / len(ollama_valid), 1) if ollama_valid else 0
    
    ling_speed = round(sum(r["tokens_per_sec"] for r in ling_valid) / len(ling_valid), 1) if ling_valid else 0
    ollama_speed = round(sum(r["tokens_per_sec"] for r in ollama_valid) / len(ollama_valid), 1) if ollama_valid else 0
    
    report_file = "/Users/yvoche/AI开发/073_成都建工/V3.0/docs/ab_test_ling3.0_vs_ollama_qwen.md"
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# 成都建工 V3.0 项目存档 (01/02/03) A/B 测试报告\n\n")
        f.write(f"测试对比: **Ling-3.0-tiny** (Port 8930) vs **Ollama Served ({OLLAMA_MODEL_NAME})** (Port 11434)\n\n")
        f.write("## 1. 核心性能指标总表\n\n")
        f.write("| 评估维度 | Ling-3.0-tiny (8930) | Ollama Serve (" + OLLAMA_MODEL_NAME + ") | A/B 对比结论 |\n")
        f.write("| --- | --- | --- | --- |\n")
        
        acc_win = "Ling-3.0-tiny" if ling_acc > ollama_acc else ("Ollama (" + OLLAMA_MODEL_NAME + ")" if ollama_acc > ling_acc else "持平")
        f.write(f"| **建工财税业务准确率** | {ling_acc}% | {ollama_acc}% | **{acc_win}** |\n")
        
        ttft_win = "Ling-3.0-tiny" if (ling_ttft > 0 and ling_ttft < ollama_ttft) else "Ollama Serve"
        f.write(f"| **平均首字延迟 (TTFT)** | {ling_ttft} ms | {ollama_ttft} ms | **{ttft_win}** |\n")
        
        speed_win = "Ling-3.0-tiny" if ling_speed > ollama_speed else "Ollama Serve"
        f.write(f"| **平均生成速度** | {ling_speed} tok/s | {ollama_speed} tok/s | **{speed_win}** |\n\n")
        
        f.write("## 2. 逐项测试明细对比\n\n")
        f.write("| 用例 ID | 测试类别 | Ling 3.0 输出断言 | Ollama (" + OLLAMA_MODEL_NAME + ") 输出断言 |\n")
        f.write("| --- | --- | --- | --- |\n")
        
        for l, o in zip(ling_results, ollama_results):
            l_status = f"✅ 正确 ({l.get('ttft_ms')}ms)" if l['business_correct'] else f"❌ 偏差 ({l.get('ttft_ms')}ms)"
            o_status = f"✅ 正确 ({o.get('ttft_ms')}ms)" if o['business_correct'] else (f"❌ 偏差 ({o.get('ttft_ms')}ms)" if o['success'] else f"⚠️ 未就绪 ({o.get('error')})")
            f.write(f"| {l['id']} | {l['category']} | {l_status} | {o_status} |\n")
            
    print(f"\n🎉 评测报告成功生成: {report_file}\n")
    print(f"Ling-3.0-tiny 业务准确率: {ling_acc}% | 首字延迟: {ling_ttft}ms | 速度: {ling_speed}tok/s")
    print(f"Ollama {OLLAMA_MODEL_NAME} 业务准确率: {ollama_acc}% | 首字延迟: {ollama_ttft}ms | 速度: {ollama_speed}tok/s")

if __name__ == "__main__":
    run_ab_benchmark()
