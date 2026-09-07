#!/usr/bin/env python3
"""
Chengdu Construction V3.0 - Project Archives (01/02/03) A/B Benchmark
Compares Ling-3.0-tiny (Port 8930) vs iFlytek Spark X2.5 across 60 real construction, tax, four-flow, facts review, and citation tasks.
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, List
import httpx

# 引入基础评测集定义
sys.path.insert(0, os.path.dirname(__file__))
from run_ab_benchmark import BENCHMARK_CASES, evaluate_single_case

LING_API_URL = os.environ.get("LING_API_URL", "http://127.0.0.1:8930/v1/chat/completions")
SPARK_API_URL = os.environ.get("SPARK_API_URL", "http://127.0.0.1:8931/v1/chat/completions")
SPARK_MODEL_NAME = "Spark-X2.5-4B"

def run_benchmark_on_endpoint(api_url: str, model_alias: str) -> Dict[str, Any]:
    print(f"\n=======================================================")
    print(f"🚀 Running Benchmark on Endpoint: {api_url} [{model_alias}]")
    print(f"=======================================================")

    results = []
    with httpx.Client(timeout=90.0) as client:
        for idx, case in enumerate(BENCHMARK_CASES):
            sys.stdout.write(f"\r[{idx+1}/{len(BENCHMARK_CASES)}] Evaluating {case['id']} ({case['category']})...")
            sys.stdout.flush()
            
            # 兼容 evaluate_single_case 方法
            try:
                payload = {
                    "model": model_alias,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一个专业的建筑工程与财税智能分析专家。"
                                + (" 必须以纯JSON输出，不要输出Markdown代码围栏。" if case["format"] == "json" else "")
                            ),
                        },
                        {"role": "user", "content": case["prompt"]},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 512,
                    "stream": True,
                }
                if case["format"] == "json":
                    payload["response_format"] = {"type": "json_object"}

                start_time = time.perf_counter()
                first_token_time = None
                generated_text = ""
                token_count = 0

                with client.stream("POST", api_url, json=payload) as response:
                    if response.status_code != 200:
                        results.append({
                            "id": case["id"],
                            "category": case["category"],
                            "success": False,
                            "error": f"HTTP {response.status_code}",
                            "ttft_ms": 0,
                            "tokens_per_sec": 0,
                            "business_correct": False,
                            "hallucination": True
                        })
                        continue

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

                total_time = time.perf_counter() - start_time
                ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else total_time * 1000
                gen_time = total_time - (ttft_ms / 1000)
                tok_per_sec = token_count / gen_time if gen_time > 0 and token_count > 0 else 0

                # 简单业务匹配
                kw_hits = sum(1 for kw in case.get("expected_keywords", []) if kw.lower() in generated_text.lower())
                expected_kws = case.get("expected_keywords", [])
                kw_score = kw_hits / len(expected_kws) if expected_kws else 1.0
                business_correct = kw_score >= 0.4

                results.append({
                    "id": case["id"],
                    "category": case["category"],
                    "success": True,
                    "ttft_ms": round(ttft_ms, 1),
                    "tokens_per_sec": round(tok_per_sec, 1),
                    "business_correct": business_correct,
                    "kw_score": round(kw_score, 2),
                    "output_snippet": generated_text[:120].replace("\n", " ")
                })
            except Exception as e:
                results.append({
                    "id": case["id"],
                    "category": case["category"],
                    "success": False,
                    "error": str(e),
                    "ttft_ms": 0,
                    "tokens_per_sec": 0,
                    "business_correct": False
                })

    print("\n✅ Evaluation completed.")
    valid_results = [r for r in results if r["success"]]
    correct_count = sum(1 for r in valid_results if r["business_correct"])
    accuracy_pct = round(correct_count / len(BENCHMARK_CASES) * 100, 2)
    avg_ttft = sum(r["ttft_ms"] for r in valid_results) / len(valid_results) if valid_results else 0
    avg_speed = sum(r["tokens_per_sec"] for r in valid_results) / len(valid_results) if valid_results else 0

    return {
        "model_alias": model_alias,
        "endpoint": api_url,
        "total_cases": len(BENCHMARK_CASES),
        "valid_cases": len(valid_results),
        "business_accuracy_pct": accuracy_pct,
        "avg_ttft_ms": round(avg_ttft, 1),
        "avg_tokens_per_sec": round(avg_speed, 1),
        "results": results
    }

def main():
    print("=== 成都建工 V3.0 项目存档资料 (01/02/03) A/B 双模型对比评测 [Ling-3.0-tiny vs Spark-X2.5-4B] ===")
    
    # 评测 Ling 3.0
    ling_summary = run_benchmark_on_endpoint(LING_API_URL, "ling-3.0-tiny")
    
    # 评测 讯飞星火 X2.5-4B
    spark_summary = run_benchmark_on_endpoint(SPARK_API_URL, SPARK_MODEL_NAME)
    
    report_file = "/Users/yvoche/AI开发/073_成都建工/V3.0/docs/ab_test_ling3.0_vs_spark_x2.5_4b.md"
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# 成都建工 V3.0 项目存档资料 (01/02/03) A/B 测试报告\n\n")
        f.write("测试模型: **Ling-3.0-tiny** (Port 8930) vs **星火 Spark-X2.5-4B** (Port 8931)\n\n")
        f.write("## 1. 综合性能对比表\n\n")
        f.write("| 评估指标 | Ling-3.0-tiny | Spark-X2.5-4B | 优胜项 |\n")
        f.write("| --- | --- | --- | --- |\n")
        
        l_acc = ling_summary["business_accuracy_pct"]
        s_acc = spark_summary["business_accuracy_pct"]
        acc_winner = "Ling-3.0-tiny" if l_acc > s_acc else ("Spark-X2.5-4B" if s_acc > l_acc else "持平")
        f.write(f"| **建工财税业务准确率** | {l_acc}% | {s_acc}% | {acc_winner} |\n")
        
        l_ttft = ling_summary["avg_ttft_ms"]
        s_ttft = spark_summary["avg_ttft_ms"]
        ttft_winner = "Ling-3.0-tiny" if (l_ttft > 0 and l_ttft < s_ttft) else "Spark-X2.5-4B"
        f.write(f"| **平均首字延迟 (TTFT)** | {l_ttft} ms | {s_ttft} ms | {ttft_winner} |\n")
        
        l_sp = ling_summary["avg_tokens_per_sec"]
        s_sp = spark_summary["avg_tokens_per_sec"]
        sp_winner = "Ling-3.0-tiny" if l_sp > s_sp else "Spark-X2.5-4B"
        f.write(f"| **平均生成速度** | {l_sp} tok/s | {s_sp} tok/s | {sp_winner} |\n")
        
    print(f"\n🎉 测试完成！双模型对比报告已成功输出至: {report_file}")

if __name__ == "__main__":
    main()
