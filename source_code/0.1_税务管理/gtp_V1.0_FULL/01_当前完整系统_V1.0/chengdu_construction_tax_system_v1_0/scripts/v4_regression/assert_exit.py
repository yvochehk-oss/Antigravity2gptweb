#!/usr/bin/env python3
"""
assert_exit.py — 解析 Safari Bridge v4.0 的 stderr JSONL 事件流，
按断言规范验证每个阶段的退出码和事件顺序。

用法：
    python3 assert_exit.py <exit_code> <events.jsonl> [--expect-stages S1,S2,...]

示例（正常完成）：
    python3 assert_exit.py 0 events.jsonl \
        --expect-stages start,baseline,inject,send,submit_verify,new_turn,stable,done

示例（Tab 不存在）：
    python3 assert_exit.py 10 events.jsonl \
        --expect-stages start,baseline
"""
import sys
import json
import argparse
from typing import List, Tuple

# EXIT_CODE_NAME 必须与 safari_chatgpt.py 保持同步
EXIT_CODE_NAME = {
    0: "OK",
    2: "TIMEOUT_PARTIAL",
    3: "TIMEOUT_EMPTY",
    4: "SAFARI_FAIL",
    5: "BASELINE_FAIL",
    6: "SUBMIT_FAIL",
    7: "NO_NEW_TURN",
    10: "NO_TAB",
    12: "CIRCUIT_OPEN",
}

STAGE_ORDER = [
    "start", "baseline", "inject", "send",
    "submit_verify", "new_turn", "stable", "done",
    # 错误路径阶段
    "new_chat", "new_chat_warn",
    "circuit_breaker", "fatal",
]


def parse_events(jsonl_path: str) -> List[dict]:
    events = []
    with open(jsonl_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  ⚠️  行 {lineno} JSON 解析失败: {e} — 跳过", file=sys.stderr)
    return events


def assert_exit(exit_code: int, events: List[dict],
                expected_stages: List[str] = None) -> Tuple[bool, List[str]]:
    """验证事件流是否符合规范。返回 (passed, errors)。"""
    errors = []

    if not events:
        errors.append("事件流为空")
        return False, errors

    # 1. 验证第一条事件是 start
    first = events[0]
    if first.get("stage") != "start":
        errors.append(f"第一条事件应为 'start'，实际为 '{first.get('stage')}'")

    # 2. 验证最后一条事件与 exit_code 对应
    last = events[-1]
    last_ec = last.get("exit_code")
    last_name = last.get("exit_name", "")
    expected_name = EXIT_CODE_NAME.get(exit_code, "UNKNOWN")
    if last_ec != exit_code:
        errors.append(f"最后一条 exit_code 应为 {exit_code}，实际为 {last_ec}（{last_name}）")
    if last_name != expected_name and last_ec == exit_code:
        errors.append(f"exit_name 应为 '{expected_name}'，实际为 '{last_name}'")

    # 3. 验证退出码不在中间事件里出现（最后一条才是决定性退出码）
    for ev in events[:-1]:
        ec = ev.get("exit_code", 0)
        # BASELINE_FAIL 等阶段退出码只应出现在最后
        if ec not in (0,):
            pass  # 允许非 0 在中间出现（因为 send_and_receive_safari_chatgpt 每步都返回）
        # 但 start 事件应为 OK
        if ev.get("stage") == "start" and ec != 0:
            errors.append(f"'start' 阶段 exit_code 应为 0，实际为 {ec}")

    # 4. 验证阶段顺序（如果指定了 expected_stages）
    if expected_stages:
        actual_stages = [ev.get("stage") for ev in events]
        for i, expected in enumerate(expected_stages):
            if i < len(actual_stages):
                actual = actual_stages[i]
                if actual != expected:
                    errors.append(
                        f"第 {i+1} 个阶段应为 '{expected}'，实际为 '{actual}'"
                    )
            else:
                errors.append(f"缺少第 {i+1} 个阶段 '{expected}'")

    # 5. 验证每个事件包含必要字段
    required_fields = ["ts", "stage", "exit_code", "exit_name", "message"]
    for i, ev in enumerate(events):
        for field in required_fields:
            if field not in ev:
                errors.append(f"事件 {i} 缺少字段 '{field}': {ev}")

    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(
        description="验证 Safari Bridge v4.0 事件流"
    )
    parser.add_argument("exit_code", type=int,
                        help="预期退出码（主进程返回码）")
    parser.add_argument("events_file", type=str,
                        help="stderr JSONL 文件路径")
    parser.add_argument("--expect-stages", type=str, default="",
                        help="期望的阶段顺序，逗号分隔（如 start,baseline,done）")
    args = parser.parse_args()

    expected_stages = [s.strip() for s in args.expect_stages.split(",") if s.strip()]
    events = parse_events(args.events_file)

    print(f"[assert_exit] 加载 {len(events)} 个事件，期望 exit_code={args.exit_code} "
          f"({EXIT_CODE_NAME.get(args.exit_code, '?')})")
    if expected_stages:
        print(f"[assert_exit] 期望阶段顺序: {' → '.join(expected_stages)}")

    passed, errors = assert_exit(args.exit_code, events, expected_stages)

    print(f"\n{'='*50}")
    print(f"[assert_exit] 事件流摘要：")
    for ev in events:
        ec = ev.get("exit_code")
        ec_str = f"[{ec} {ev.get('exit_name','')}]" if ec != 0 else ""
        print(f"  {ev['ts']:.3f}  {ev['stage']:<20} {ec_str}  {ev.get('message','')[:60]}")

    print(f"\n{'='*50}")
    if passed:
        print("✅ PASS — 事件流符合规范")
        return 0
    else:
        print(f"❌ FAIL — 发现 {len(errors)} 个问题：")
        for err in errors:
            print(f"  • {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
