#!/usr/bin/env python3
"""Windows orchestrator: BrowserSkill first, legacy Node/CDP optional fallback.

The cloud Custom GPT owns GitHub mutations. This local controller only:
1) sends one atomic task to ChatGPT, 2) fast-forwards a clean checkout,
3) executes GPT-declared tests, and 4) returns evidence for review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home() / ".antigravity" / "orchestrator"
HERE = Path(__file__).resolve().parent
BSK_BRIDGE = HERE / "bsk_chatgpt.py"
CDP_BRIDGE = HERE / "chrome_chatgpt.js"
MAX_FIX_ATTEMPTS = 3


class OrchestratorError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.lstrip("\ufeff")
    for enc in ("utf-8-sig", "utf-16le", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def run(argv, *, cwd=None, timeout=60, shell=False):
    try:
        return subprocess.run(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, shell=shell
        )
    except subprocess.TimeoutExpired as exc:
        raise OrchestratorError(f"命令超时：{argv}") from exc
    except OSError as exc:
        raise OrchestratorError(f"命令启动失败：{argv}: {exc}") from exc


def git(args: list[str], cwd: str, label: str) -> str:
    p = run(["git", *args], cwd=cwd, timeout=45)
    if p.returncode != 0:
        detail = (decode(p.stderr) or decode(p.stdout)).strip()[:800]
        raise OrchestratorError(f"{label}: {detail}")
    return decode(p.stdout).strip()


def ensure_clean(cwd: str) -> None:
    if git(["status", "--porcelain"], cwd, "无法检查工作区"):
        raise OrchestratorError("本地工作区有未提交改动；拒绝 pull，避免覆盖用户工作。")


def ensure_branch(cwd: str, branch: str) -> None:
    git(["fetch", "origin", branch], cwd, "无法获取目标分支")
    if git(["branch", "--show-current"], cwd, "无法读取当前分支") == branch:
        return
    p = run(["git", "switch", branch], cwd=cwd, timeout=30)
    if p.returncode != 0:
        git(["switch", "--track", "-c", branch, f"origin/{branch}"], cwd, "无法切换到目标分支")


def sync_branch(cwd: str, branch: str) -> str:
    ensure_clean(cwd)
    git(["fetch", "origin", branch], cwd, "无法获取远端提交")
    git(["pull", "--ff-only", "origin", branch], cwd, "无法 fast-forward 拉取")
    head = git(["rev-parse", "HEAD"], cwd, "无法读取本地 HEAD")
    remote = git(["rev-parse", f"origin/{branch}"], cwd, "无法读取远端 HEAD")
    if head != remote:
        raise OrchestratorError(f"HEAD 不一致：local={head}, origin={remote}")
    return head


def state_path(name: str) -> Path:
    return HOME / f"{name}.state.json"


def work_dir(name: str) -> Path:
    return HOME / name


def save(state: dict[str, Any]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    work_dir(state["name"]).mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    state_path(state["name"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (work_dir(state["name"]) / "TASKS.json").write_text(
        json.dumps({
            "name": state["name"],
            "task_locked": state.get("task_locked", False),
            "current_task_id": state.get("current_task_id"),
            "tasks": state.get("tasks", []),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load(name: str) -> dict[str, Any]:
    path = state_path(name)
    if not path.exists():
        raise OrchestratorError(f"项目不存在：{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def bsk_ready() -> bool:
    exe = shutil.which("bsk") or shutil.which("bsk.exe")
    if not exe:
        return False
    try:
        p = run([exe, "status", "--json"], timeout=6)
        if p.returncode != 0:
            return False
        data = json.loads(decode(p.stdout))
        return bool(data.get("browsers", []))
    except (OrchestratorError, json.JSONDecodeError, TypeError, AttributeError):
        return False


def resolve_driver(requested: str) -> str:
    if requested == "bsk":
        if not (shutil.which("bsk") or shutil.which("bsk.exe")):
            raise OrchestratorError("--driver bsk 已指定，但 PATH 中找不到 bsk.exe。")
        if not bsk_ready():
            raise OrchestratorError(
                "--driver bsk 已指定，但 daemon/浏览器扩展尚未形成可用连接。"
            )
        return "bsk"
    if requested == "cdp":
        if not shutil.which("node"):
            raise OrchestratorError("--driver cdp 已指定，但找不到 Node.js。")
        return "cdp"
    if bsk_ready():
        return "bsk"
    if shutil.which("node"):
        return "cdp"
    raise OrchestratorError("既没有可用的 BrowserSkill 连接，也没有 Node.js/CDP 回退环境。")


def bridge(state: dict[str, Any], kind: str, prompt: str, *, timeout=360,
           evidence: str | None = None, level="L1", signature: str | None = None):
    driver = resolve_driver(state.get("driver", "auto"))
    common = [
        "--type", kind, "--prompt", prompt,
        "--target-url", state["chatgpt_url"],
        "--timeout", str(timeout),
        "--cwd", state["cwd"],
    ]
    if evidence:
        common += ["--evidence", evidence, "--level", level]
    if signature:
        common += ["--signature", signature]

    browser_profile = str(state.get("browser_profile", "") or "").strip()
    if browser_profile and driver != "bsk":
        raise OrchestratorError(
            "项目已锁定 BrowserSkill Profile，但当前解析到非 bsk 驱动；"
            "拒绝静默降级到 CDP，以免使用错误浏览器/Profile。"
        )

    if driver == "bsk":
        if browser_profile:
            common += ["--browser-profile", browser_profile]
        cmd = [sys.executable, str(BSK_BRIDGE), *common]
    else:
        cmd = ["node", str(CDP_BRIDGE), *common]

    p = run(cmd, cwd=state["cwd"], timeout=timeout + 90)
    return p.returncode, decode(p.stdout), decode(p.stderr), driver


def parse_tests(text: str) -> list[dict[str, str]]:
    blocks = re.findall(r"```(?:bash|cmd|powershell)?\s*\n([\s\S]*?)```", text, flags=re.I)
    tests = []
    for block in blocks or [text]:
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"\s*TEST:\s*(.+?)\s*$", line)
            if not m:
                continue
            expected = ""
            if i + 1 < len(lines):
                em = re.match(r"\s*EXPECTED:\s*(.+?)\s*$", lines[i + 1])
                if em:
                    expected = em.group(1).strip()
            tests.append({"command": m.group(1).strip(), "expected": expected})
    return tests


def parse_verdict(text: str) -> tuple[str, str]:
    clean = text.strip()
    if re.match(r"^APPROVED\b", clean):
        return "APPROVED", ""
    m = re.match(r"^(NEEDS_FIX|BLOCKED)\s*(?:\(|[-–—:])?\s*([\s\S]*?)(?:\))?\s*$", clean)
    if m:
        return m.group(1), m.group(2).strip()
    return "BLOCKED", f"无法解析 GPT 裁决：{clean[:240]}"


def run_tests(state: dict[str, Any], tests: list[dict[str, str]]) -> str:
    chunks = []
    for test in tests:
        p = run(test["command"], cwd=state["cwd"], timeout=120, shell=True)
        chunks.append(
            f"COMMAND: {test['command']}\n"
            f"EXPECTED: {test.get('expected', '')}\n"
            f"EXIT_CODE: {p.returncode}\n"
            f"STDOUT:\n{decode(p.stdout)[:5000]}\n"
            f"STDERR:\n{decode(p.stderr)[:5000]}"
        )
    return "\n\n---\n\n".join(chunks)


def task_prompt(state: dict[str, Any], task: dict[str, Any]) -> str:
    fix = task.get("last_fix_note", "")
    return (
        "【@GitHub 协同基线强制对齐】\n"
        f"1. 目标仓库：{state['repo_url']}\n"
        f"2. 目标分支：{state['branch']}\n"
        f"3. 最新提交基线（Parent Commit）：{state['commit_sha_head']}\n\n"
        f"【任务 {task['id']}/{len(state['tasks'])}】{task['title']}\n"
        f"{task.get('description', '')}\n"
        + (f"\n【需要修复】{fix}\n" if fix else "")
        + "\n请直接使用 GitHub 工具修改、提交并推送。回复只给测试：\n"
          "TEST: <命令>\nEXPECTED: <预期结果>\n"
    )


def review_prompt(state: dict[str, Any], task: dict[str, Any], evidence: str) -> str:
    return (
        f"【任务 {task['id']}】{task['title']}\n"
        f"【已同步远端提交】{state['commit_sha_head']}\n\n"
        f"【本地测试结果】\n{evidence}\n\n"
        "只输出 APPROVED、NEEDS_FIX(<原因>) 或 BLOCKED(<原因>)。"
    )


def execute_task(state: dict[str, Any], task: dict[str, Any]) -> bool:
    while task.get("attempts", 0) < MAX_FIX_ATTEMPTS:
        task["attempts"] = task.get("attempts", 0) + 1
        task["status"] = "fixing" if task["attempts"] > 1 else "coding"

        code, answer, events, driver = bridge(
            state, "task-code", task_prompt(state, task),
            timeout=360,
            signature=f"task-code:{state['name']}:{task['id']}:{task['attempts']}",
        )
        state["resolved_driver"] = driver
        task["last_bridge_events"] = events[-4000:]
        if code not in (0, 2):
            task.update(status="blocked", last_verdict="BLOCKED",
                        last_fix_note=f"GPT 调用失败 exit={code}")
            save(state)
            return False

        tests = parse_tests(answer)
        if not tests:
            task.update(status="blocked", last_verdict="BLOCKED",
                        last_fix_note="GPT 未提供有效 TEST/EXPECTED")
            save(state)
            return False

        try:
            state["commit_sha_head"] = sync_branch(state["cwd"], state["branch"])
        except OrchestratorError as exc:
            task.update(status="blocked", last_verdict="BLOCKED",
                        last_fix_note=f"无法安全同步远端提交：{exc}")
            save(state)
            return False

        task["status"] = "testing"
        task["test_commands"] = [x["command"] for x in tests]
        evidence = run_tests(state, tests)
        task["test_results"] = evidence
        task["test_passed"] = not bool(re.search(r"\nEXIT_CODE:\s*(?!0\b)\d+", "\n" + evidence))

        rcode, review, revents, driver = bridge(
            state, "task-review", review_prompt(state, task, evidence),
            timeout=180, evidence=evidence, level="L2",
            signature=f"task-review:{state['name']}:{task['id']}:{task['attempts']}",
        )
        state["resolved_driver"] = driver
        task["last_review_events"] = revents[-4000:]
        if rcode not in (0, 2):
            task.update(status="blocked", last_verdict="BLOCKED",
                        last_fix_note=f"GPT 审查失败 exit={rcode}")
            save(state)
            return False

        verdict, detail = parse_verdict(review)
        task["last_verdict"] = verdict
        task["last_fix_note"] = detail
        if verdict == "APPROVED":
            task["status"] = "approved"
            task["completed_at"] = now()
            save(state)
            return True
        if verdict == "BLOCKED":
            task["status"] = "blocked"
            save(state)
            return False
        save(state)

    task.update(status="failed", last_verdict="BLOCKED",
                last_fix_note=f"达到最大修复轮次 {MAX_FIX_ATTEMPTS}")
    save(state)
    return False


def parse_tasks(plan: str) -> list[dict[str, Any]]:
    rows = re.findall(r"^\s*(?:任务\s*)?(\d+)[.、:：\-\s]+(.+)$", plan, flags=re.M)
    return [{
        "id": i, "title": title.strip()[:120], "description": title.strip(),
        "status": "pending", "attempts": 0, "test_commands": [],
        "test_results": "", "test_passed": None, "last_verdict": "",
        "last_fix_note": "",
    } for i, (_, title) in enumerate(rows, start=1)]


def cmd_init(args) -> None:
    if state_path(args.name).exists():
        raise OrchestratorError(f"项目已存在：{args.name}")
    cwd = str(Path(args.cwd).resolve())
    ensure_branch(cwd, args.branch)
    head = sync_branch(cwd, args.branch)
    state = {
        "name": args.name, "created_at": now(), "updated_at": now(),
        "requirement": args.requirement, "chatgpt_url": args.target_url,
        "repo_url": args.repo, "branch": args.branch, "cwd": cwd,
        "driver": args.driver,
        "browser_profile": (args.browser_profile or "").strip(),
        "task_locked": False, "current_task_id": None,
        "tasks": [], "commit_sha_head": head,
        "plan_md_path": str(work_dir(args.name) / "PLAN.md"),
    }
    code, plan, events, driver = bridge(
        state, "plan",
        f"【项目需求】\n{args.requirement}\n请输出编号原子任务计划，每项必须可独立提交和验证。",
        timeout=300, signature=f"init:{args.name}"
    )
    if code not in (0, 2):
        raise OrchestratorError(f"方案生成失败 exit={code}: {events[-800:]}")
    state["resolved_driver"] = driver
    work_dir(args.name).mkdir(parents=True, exist_ok=True)
    Path(state["plan_md_path"]).write_text(
        f"# 项目计划：{args.name}\n\n{plan}\n", encoding="utf-8"
    )
    save(state)
    print(f"initialized {args.name} driver={driver} head={head}")


def cmd_lock(args) -> None:
    state = load(args.name)
    plan = Path(state["plan_md_path"]).read_text(encoding="utf-8")
    tasks = parse_tasks(plan)
    if not tasks:
        raise OrchestratorError("未能从 PLAN.md 解析编号任务。")
    state["tasks"] = tasks
    state["task_locked"] = True
    save(state)
    print(f"locked {len(tasks)} tasks")


def cmd_run_task(args) -> None:
    state = load(args.name)
    if not state.get("task_locked"):
        raise OrchestratorError("请先执行 lock。")
    if args.task_id:
        task = next((t for t in state["tasks"] if str(t["id"]) == str(args.task_id)), None)
    else:
        task = next((t for t in state["tasks"] if t.get("status") == "pending"), None)
    if not task:
        raise OrchestratorError("没有匹配的待执行任务。")
    state["current_task_id"] = task["id"]
    raise SystemExit(0 if execute_task(state, task) else 1)


def cmd_status(args) -> None:
    state = load(args.name)
    print(json.dumps({
        "name": state["name"], "branch": state["branch"],
        "head": state["commit_sha_head"],
        "driver": state.get("resolved_driver", state.get("driver")),
        "browser_profile": state.get("browser_profile", ""),
        "tasks": [
            {"id": t["id"], "title": t["title"], "status": t["status"],
             "verdict": t.get("last_verdict", "")}
            for t in state.get("tasks", [])
        ],
    }, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Antigravity Windows orchestrator")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init")
    x.add_argument("--name", required=True)
    x.add_argument("--requirement", required=True)
    x.add_argument("--target-url", required=True)
    x.add_argument("--repo", required=True)
    x.add_argument("--cwd", required=True)
    x.add_argument("--branch", required=True)
    x.add_argument("--driver", choices=("auto", "bsk", "cdp"), default="auto")
    x.add_argument(
        "--browser-profile",
        default=os.environ.get("BSK_BROWSER_PROFILE", ""),
        help="BrowserSkill instance_id 或唯一 label；指定后禁止静默回退到 CDP。",
    )
    x.set_defaults(func=cmd_init)

    x = sub.add_parser("lock")
    x.add_argument("--name", required=True)
    x.set_defaults(func=cmd_lock)

    x = sub.add_parser("run-task")
    x.add_argument("--name", required=True)
    x.add_argument("--task-id")
    x.set_defaults(func=cmd_run_task)

    x = sub.add_parser("status")
    x.add_argument("--name", required=True)
    x.set_defaults(func=cmd_status)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except OrchestratorError as exc:
        sys.stderr.write(f"[orchestrate] {exc}\n")
        raise SystemExit(1)
