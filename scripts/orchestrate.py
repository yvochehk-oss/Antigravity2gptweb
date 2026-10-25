#!/usr/bin/env python3
"""
Desktop Agent Orchestrator — 端到端任务编排器
==============================================
主 Agent = 任意本地桌面 Agent，云端 = ChatGPT Web（GPT-5.6 SOL）

完整工作流：
  init → refine（可选）→ lock → run-task × N → 完成

每个 run-task 内部闭环：
  GPT 通过隧道插件直接读写本机工作目录 → 本地 Agent 核对文件变更清单并原地跑测试 →
  GPT 审查测试结果 → 裁决：APPROVED / NEEDS_FIX / BLOCKED

安全模型：无 GitHub 中转。每个任务开始前本地 Agent 先给工作目录做快照，
GPT 声称完成后以文件清单（sha256）差异作为已落盘的客观证据；零变更直接判 BLOCKED。
回滚使用 orchestrate.py restore 从快照恢复。
"""

import sys
import os
import re
import json
import time
import uuid
import shutil
import textwrap
import subprocess
import argparse
import hashlib
import platform
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

# =============================================================================
# 常量
# =============================================================================
ORCHESTRATOR_HOME = Path.home() / ".antigravity" / "orchestrator"

BRIDGE_BY_BROWSER = {
    "safari": "scripts/safari_chatgpt.py",
    "chrome": "scripts/chrome_chatgpt.py",
    "edge":   "scripts/chrome_chatgpt.py",
    "brave":  "scripts/chrome_chatgpt.py",
    "arc":    "scripts/chrome_chatgpt.py",
    "chromium": "scripts/chrome_chatgpt.py",
}

MAX_FIX_ATTEMPTS = 3          # 与 README/SKILL 契约一致：最多 3 轮
CMD_TIMEOUT = 30              # 本地命令超时（秒）

# =============================================================================
# 数据模型
# =============================================================================

@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    status: str = "pending"    # pending / coding / testing / approved / failed / blocked
    attempts: int = 0
    code_output: str = ""       # GPT 最后输出的代码（原始文本）
    test_commands: List[str] = field(default_factory=list)
    test_results: str = ""
    test_passed: Optional[bool] = None
    last_verdict: str = ""     # APPROVED / NEEDS_FIX / BLOCKED
    last_fix_note: str = ""
    completed_at: Optional[str] = None


@dataclass
class ProjectState:
    name: str
    created_at: str
    updated_at: str
    requirement: str
    chatgpt_url: str
    cwd: str                    # 本地工作目录（GPT 经隧道插件直读写）
    browser: str                 # safari / chrome / edge / ...
    bridge_script: str           # 实际调用的 bridge 脚本相对路径
    plan_md_path: str
    tasks_json_path: str
    task_locked: bool = False
    current_task_id: Optional[int] = None
    tasks: List[Task] = field(default_factory=list)
    workspace_manifest: Dict[str, str] = field(default_factory=dict)  # 相对路径 → sha256 或 large:size
    snapshots: List[str] = field(default_factory=list)                # 快照目录列表（回滚用）

    def save(self) -> None:
        path = Path(self.plan_md_path)
        # PLAN.md 独立保存
        plan_path = path.parent / "PLAN.md"
        tasks_path = path
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            (plan_path.read_text() if plan_path.exists() else ""),
            encoding="utf-8"
        )
        tasks_path.write_text(
            json.dumps({"tasks": [asdict(t) for t in self.tasks],
                        "name": self.name,
                        "requirement": self.requirement,
                        "task_locked": self.task_locked,
                        "current_task_id": self.current_task_id},
                       ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        state_path = ORCHESTRATOR_HOME / f"{self.name}.state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @staticmethod
    def load(name: str) -> "ProjectState":
        state_path = ORCHESTRATOR_HOME / f"{name}.state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"项目 {name!r} 不存在，请先 run init")
        with open(state_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.pop("browser_profile", None)  # 旧版本状态文件兼容：该字段已随旧驱动一并移除
        for _legacy in ("repo_url", "branch", "commit_sha_init", "commit_sha_head"):
            d.pop(_legacy, None)  # 旧 GitHub 流程字段，统一版已移除
        tasks = [Task(**t) for t in d.pop("tasks", [])]
        d["tasks"] = tasks
        return ProjectState(**d)


# =============================================================================
# 工具函数
# =============================================================================

def _run(args: List[str], cwd: str = ".", timeout: int = CMD_TIMEOUT,
         capture: bool = True) -> subprocess.CompletedProcess:
    """安全的 subprocess.run 封装"""
    return subprocess.run(
        args, cwd=cwd, capture_output=capture,
        text=True, timeout=timeout, check=False
    )


def _resolve_bridge(browser: str) -> str:
    browser_clean = browser.lower()
    base = Path(__file__).parent.parent.resolve()

    script = BRIDGE_BY_BROWSER.get(browser_clean)
    if not script:
        raise ValueError(f"未知 browser: {browser!r}，可选: {list(BRIDGE_BY_BROWSER.keys())}")

    # Windows uses the dependency-free Node CDP bridge. macOS keeps the
    # AppleScript Safari bridge and Python Chromium bridge unchanged.
    if platform.system() == "Windows" and browser_clean != "safari":
        return str(base / "scripts/chrome_chatgpt.js")
    return str(base / script)


_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                ".antigravity", ".mypy_cache", ".pytest_cache"}
_IGNORE_SUFFIXES = (".pyc",)


def _iter_workspace_files(cwd: str):
    """枚举工作目录内受管文件（相对路径用 posix 分隔）。"""
    base = Path(cwd)
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if any(part in _IGNORE_DIRS for part in rel.parts):
            continue
        if p.suffix in _IGNORE_SUFFIXES:
            continue
        yield p, rel.as_posix()


def _workspace_manifest(cwd: str) -> Dict[str, str]:
    """相对路径 → 内容指纹。>20MB 的文件只记大小，避免大文件哈希开销。"""
    manifest: Dict[str, str] = {}
    for p, rel in _iter_workspace_files(cwd):
        try:
            size = p.stat().st_size
            if size > 20 * 1024 * 1024:
                manifest[rel] = f"large:{size}"
            else:
                manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
    return manifest


def _manifest_diff(old: Dict[str, str], new: Dict[str, str]):
    added = sorted(set(new) - set(old))
    deleted = sorted(set(old) - set(new))
    modified = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    return added, modified, deleted


def _changed_summary(changed, limit: int = 30) -> str:
    added, modified, deleted = changed
    lines = []
    for tag, items in (("新增", added), ("修改", modified), ("删除", deleted)):
        for rel in items[:limit]:
            lines.append(f"{tag}: {rel}")
        if len(items) > limit:
            lines.append(f"{tag}: ……另 {len(items) - limit} 个")
    return "\n".join(lines) if lines else "（无变化）"


def _make_snapshot(name: str, cwd: str, label: str) -> Path:
    """把工作目录当前状态复制到 ORCHESTRATOR_HOME/<name>/snapshots/<label>-<时间戳>。"""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    dest = ORCHESTRATOR_HOME / name / "snapshots" / f"{label}-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    for p, rel in _iter_workspace_files(cwd):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return dest


# =============================================================================
# 代码块解析（GPT 输出格式）
# =============================================================================

_CODE_BLOCK_RE = re.compile(
    r"```(\w*)\n(?:<filepath:\s*(.+?)>\n)?(.*?)```",
    re.DOTALL
)
_TEST_BLOCK_RE = re.compile(
    r"```bash\n(?:(TEST:\s*.+?)\nEXPECTED:\s*(.+?)\n)```",
    re.DOTALL
)


def parse_code_blocks(text: str) -> List[Tuple[Optional[str], str, str]]:
    """解析 GPT 输出中的代码块。
    返回: [(filepath_or_None, language, code)]
    filepath 格式: "src/auth/jwt.py" 或 "NEW: src/auth/jwt.py"
    """
    results = []
    for m in _CODE_BLOCK_RE.finditer(text):
        lang = m.group(1) or ""
        filepath = m.group(2) or None
        code = m.group(3)
        results.append((filepath, lang, code))
    return results


def parse_test_commands(text: str) -> List[Tuple[str, str]]:
    """解析 GPT 输出中的测试命令。
    返回: [(command, expected_outcome)]
    """
    results = []
    for m in _TEST_BLOCK_RE.finditer(text):
        results.append((m.group(1).replace("TEST:", "").strip(),
                         m.group(2).strip()))
    return results


def parse_verdict(text: str) -> Tuple[str, str]:
    """解析 GPT 的审查裁决。
    返回: (verdict, detail)  verdict in {APPROVED, NEEDS_FIX, BLOCKED}
    """
    text = text.strip()
    if text.startswith("APPROVED"):
        return "APPROVED", ""
    m_fix = re.match(r"NEEDS_FIX\s*[-–—:]?\s*(.*)", text, re.DOTALL)
    if m_fix:
        return "NEEDS_FIX", m_fix.group(1).strip()
    m_block = re.match(r"BLOCKED\s*[-–—:]?\s*(.*)", text, re.DOTALL)
    if m_block:
        return "BLOCKED", m_block.group(1).strip()
    return "BLOCKED", f"无法解析 GPT 裁决（可能是格式不符）：{text[:200]}"


# =============================================================================
# 核心：调用 bridge
# =============================================================================

def _bridge_call(type_: str, prompt: str,
                 target_url: str,
                 bridge_script: str,
                 cwd: str,
                 evidence: Optional[str] = None,
                 level: str = "L1",
                 timeout: int = 240,
                 signature: Optional[str] = None,
                 extra_args: Optional[List[str]] = None) -> Tuple[int, str]:
    """调用 bridge，返回 (exit_code, stdout)"""
    runtime = ["node", bridge_script] if bridge_script.endswith(".js") else [sys.executable, bridge_script]
    args = runtime + [
        "--type", type_,
        "--prompt", prompt,
        "--target-url", target_url,
        "--cwd", cwd,
        "--timeout", str(timeout),
    ]
    if evidence:
        args += ["--evidence", evidence]
    if signature:
        args += ["--signature", signature]
    if extra_args:
        args += extra_args
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 60)
    if r.returncode != 0 and r.stderr:
        print(r.stderr[-3000:], file=sys.stderr, end="" if r.stderr.endswith("\n") else "\n")
    return r.returncode, r.stdout


# =============================================================================
# 子命令：init
# =============================================================================

def cmd_init(args) -> int:
    name = args.name
    requirement = args.requirement
    target_url = args.target_url
    cwd = os.path.abspath(os.path.expanduser(args.cwd or "."))
    browser = args.browser

    if (ORCHESTRATOR_HOME / f"{name}.state.json").exists():
        print(f"[orchestrate] 项目 {name!r} 已存在，用 resume 继续。")
        return 0

    bridge_script = _resolve_bridge(browser)
    project_dir = ORCHESTRATOR_HOME / name
    plan_path = project_dir / "PLAN.md"
    tasks_path = project_dir / "TASKS.json"

    print(f"[init] 创建项目 {name!r}")
    print(f"  → 工作目录 : {cwd}")
    print(f"  → ChatGPT  : {target_url}")
    print(f"  → 浏览器    : {browser}")

    # 本地 Agent 只做验收、不写业务代码；GPT 经隧道插件直接读写工作目录。
    if not Path(cwd).is_dir():
        raise RuntimeError(f"工作目录不存在：{cwd}")
    snapshot = _make_snapshot(name, cwd, "init")
    manifest = _workspace_manifest(cwd)
    print(f"  → 初始快照 : {snapshot}")

    # 生成初始 PLAN.md（让 GPT 推演方案）
    print(f"[init] 请求 GPT-5.6 生成初始方案（--type plan）…")
    plan_prompt = (
        f"【项目需求】\n{requirement}\n\n"
        "请给出一个详细的、可执行的项目计划。\n"
        "要求：\n"
        "1. 将需求分解为 3-10 个可独立交付、可单独测试的原子任务\n"
        "2. 每个任务说明：做什么 + 验收标准 + 预期测试命令\n"
        "3. 考虑任务间的依赖关系和推荐执行顺序\n"
        "4. 注明哪些任务可以并行\n"
        "5. 最后给出一个总体风险评估"
    )
    ec, plan_text = _bridge_call(
        "plan", plan_prompt, target_url, bridge_script, cwd,
        timeout=300, signature=f"init:{name}"
    )
    if ec != 0:
        print(f"[init] ⚠ GPT 调用失败（exit={ec}），跳过方案生成，稍后请手动 refine")
        plan_text = f"# 项目计划（待 GPT 生成）\n\n需求：{requirement}\n\n（GPT 调用失败，请运行 `orchestrate.py refine` 重新生成）"

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(f"# 项目计划：{name}\n\n## 需求\n{requirement}\n\n## GPT 初始方案\n\n{plan_text}\n", encoding="utf-8")
    tasks_path.write_text(json.dumps({"tasks": [], "name": name,
                                       "requirement": requirement,
                                       "task_locked": False,
                                       "current_task_id": None},
                                      ensure_ascii=False, indent=2),
                          encoding="utf-8")

    now = datetime.utcnow().isoformat()
    state = ProjectState(
        name=name, created_at=now, updated_at=now,
        requirement=requirement, chatgpt_url=target_url,
        cwd=cwd,
        browser=browser, bridge_script=bridge_script,
        plan_md_path=str(plan_path), tasks_json_path=str(tasks_path),
        task_locked=False, tasks=[],
        workspace_manifest=manifest, snapshots=[str(snapshot)],
    )
    state.save()

    print(f"\n[init] ✅ 项目 {name!r} 已初始化")
    print(f"  → 方案文件 : {plan_path}")
    print(f"  → 状态文件 : {ORCHESTRATOR_HOME / f'{name}.state.json'}")
    print(f"\n请打开 {plan_path} 查看初始方案，然后：")
    print(f"  1. 审核方案，如需修改 → orchestrate.py refine --name {name}")
    print(f"  2. 确认无误 → orchestrate.py lock --name {name}")
    print(f"  3. 开始执行 → orchestrate.py run-task --name {name} --autonomous")
    return 0


# =============================================================================
# 子命令：refine
# =============================================================================

def cmd_refine(args) -> int:
    name = args.name
    state = ProjectState.load(name)

    # 读取当前 PLAN.md
    plan_path = Path(state.plan_md_path)
    current = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""

    # 构建 refine prompt
    if args.feedback:
        refine_prompt = (
            f"【当前方案】\n{current}\n\n"
            f"【用户反馈】\n{args.feedback}\n\n"
            "请根据用户反馈修改方案，输出完整的更新后方案（包含所有任务）。"
        )
    else:
        refine_prompt = (
            f"【项目需求】\n{state.requirement}\n\n"
            "请重新生成详细方案。"
        )

    print(f"[refine] 请求 GPT 更新方案…")
    ec, new_plan = _bridge_call(
        "plan", refine_prompt, state.chatgpt_url, state.bridge_script,
        state.cwd, timeout=300, signature=f"refine:{name}",
    )
    if ec != 0:
        print(f"[refine] ⚠ GPT 调用失败（exit={ec}）")
        return ec

    # 覆盖 PLAN.md
    header = f"# 项目计划：{name}\n\n## 需求\n{state.requirement}\n\n## GPT 更新方案\n\n"
    plan_path.write_text(header + new_plan, encoding="utf-8")

    now = datetime.utcnow().isoformat()
    state.updated_at = now
    state.save()

    print(f"[refine] ✅ 方案已更新到 {plan_path}")
    print(f"\n{new_plan[:500]}…" if len(new_plan) > 500 else f"\n{new_plan}")
    return 0


# =============================================================================
# 子命令：lock
# =============================================================================

def cmd_lock(args) -> int:
    name = args.name
    state = ProjectState.load(name)

    if state.task_locked:
        print(f"[lock] 项目 {name!r} 已锁定，直接开始执行。")
        return 0

    plan_path = Path(state.plan_md_path)
    if not plan_path.exists():
        print(f"[lock] ❌ 方案文件不存在：{plan_path}，请先 run init / refine")
        return 1

    plan_text = plan_path.read_text(encoding="utf-8")

    # 用 GPT 把 plan 解析成任务列表
    print("[lock] 让 GPT 解析方案为原子任务列表…")
    parse_prompt = (
        f"【项目计划原文】\n{plan_text}\n\n"
        "请将上述计划解析为 JSON 数组，每个任务包含：\n"
        '  "id": 整数序号\n'
        '  "title": 任务简短标题（20字内）\n'
        '  "description": 详细任务描述\n'
        "只输出纯 JSON，不要解释，不要 markdown 标记。\n"
        "示例格式：[{\"id\":1,\"title\":\"迁移用户模型\",\"description\":\"...\"}]"
    )
    ec, json_text = _bridge_call(
        "raw", parse_prompt, state.chatgpt_url, state.bridge_script,
        state.cwd, timeout=120, signature=f"lock-parse:{name}",
    )
    if ec != 0:
        print(f"[lock] ⚠ GPT 解析失败（exit={ec}），请手动编辑 TASKS.json")
        tasks = _manual_parse_tasks(plan_text)
    else:
        # 尝试提取 JSON
        try:
            # 去掉 markdown 代码块包装
            json_text = re.sub(r"^```(?:json)?\s*", "", json_text.strip())
            json_text = re.sub(r"\s```$", "", json_text)
            tasks_data = json.loads(json_text)
            tasks = [Task(id=t["id"], title=t["title"],
                          description=t.get("description", ""))
                     for t in tasks_data]
        except Exception as e:
            print(f"[lock] ⚠ JSON 解析失败：{e}，回落到手动解析")
            tasks = _manual_parse_tasks(plan_text)

    if not tasks:
        print("[lock] ❌ 未找到任何任务，请检查方案内容")
        return 1

    state.tasks = tasks
    state.task_locked = True

    # 计划锁定仅更新 Orchestrator 私有状态；禁止本地 Agent commit/push。
    now = datetime.utcnow().isoformat()
    state.updated_at = now
    state.save()

    print(f"[lock] ✅ 计划已锁定，{len(tasks)} 个任务：")
    for t in tasks:
        print(f"  [{t.id}] {t.title}")
    print(f"\n  → 开始执行：orchestrate.py run-task --name {name} --autonomous")
    return 0


def _manual_parse_tasks(plan_text: str) -> List[Task]:
    """当 GPT 解析失败时，从 PLAN.md 文本中手动提取任务"""
    tasks = []
    # 尝试匹配 ## 任务编号 或 - [x] 格式
    lines = plan_text.splitlines()
    id_counter = 1
    for line in lines:
        m = re.match(r"#{1,3}\s*\[?(\d+|[一二三四五六七八九十]+)\]?\s*(.+)", line)
        if m:
            title = m.group(2).strip()[:50]
            tasks.append(Task(id=id_counter, title=title, description=""))
            id_counter += 1
    if not tasks:
        # 兜底：按段落分割
        paras = [p.strip() for p in re.split(r"\n(?=\s*-|\s*\d+\.)", plan_text) if p.strip()]
        for i, p in enumerate(paras[:20]):
            title = p.split("\n")[0].strip()[:50]
            tasks.append(Task(id=i+1, title=title, description=p))
    return tasks


# =============================================================================
# 核心：单任务闭环执行
# =============================================================================

def execute_task(state: ProjectState, task: Task,
                 autonomous: bool = False,
                 max_attempts: int = MAX_FIX_ATTEMPTS) -> bool:
    """
    执行单个任务的完整闭环。
    返回: 是否成功完成（APPROVED）
    """
    print(f"\n{'='*60}")
    print(f"[Task {task.id}/{len(state.tasks)}] {task.title}")
    print(f"{'='*60}")

    task.status = "coding"
    task.attempts += 1

    # 动笔前先快照 + 记录文件清单，作为本轮变更核验的基线
    snap = _make_snapshot(state.name, state.cwd, f"task{task.id}-a{task.attempts}")
    state.snapshots.append(str(snap))
    manifest_before = _workspace_manifest(state.cwd)
    state.save()

    # 构造任务 prompt
    task_prompt = (
        f"【任务 {task.id}/{len(state.tasks)}】{task.title}\n\n"
        f"【详细描述】\n{task.description or '(见上方任务标题)'}\n\n"
        "【工作目录（本机）】\n"
        f"  {state.cwd}\n\n"
        "请使用你的隧道插件直接读写上述本机目录中的文件完成此任务。"
        "本地 Agent 不会写入任何代码，只负责核对文件变更与运行测试。严格只输出：\n\n"
        "1. 测试命令（必须提供，至少一个）：\n"
        "   ```bash\n"
        "   TEST: <命令>\n"
        "   EXPECTED: <预期结果描述>\n"
        "   ```\n\n"
        "注意：\n"
        "- 测试命令必须可直接在项目根目录执行\n"
        "- EXPECTED 描述应简洁明确（如 'all tests pass', 'exit 0', 'no errors'）\n"
        "- 在提交完成前不要输出测试命令\n"
        "- 不要粘贴代码、文件内容或额外说明\n"
        "- 我会核对本地文件变更并把真实测试结果反馈给你"
    )

    ec, code_output = _bridge_call(
        "task-code", task_prompt, state.chatgpt_url,
        state.bridge_script, state.cwd,
        signature=f"task-code:{state.name}:{task.id}",
        timeout=360,
    )
    if ec != 0:
        print(f"[Task {task.id}] ⚠ GPT 代码生成失败（exit={ec}）")
        task.status = "failed"
        task.last_verdict = "BLOCKED"
        task.last_fix_note = f"GPT 调用失败 exit={ec}"
        state.save()
        return False

    test_cmds = parse_test_commands(code_output)

    if not test_cmds:
        print(f"[Task {task.id}] ⚠ GPT 未提供测试命令，要求重新生成")
        # 让 GPT 补充测试命令
        retry_prompt = (
            f"【任务 {task.id}】{task.title}\n"
            "请确认已通过隧道插件直接修改本机工作目录中的文件，完成本任务代码。"
            "你忘记提供测试命令了。请仅补充至少一个测试命令，格式：\n"
            "```bash\n"
            "TEST: <命令>\n"
            "EXPECTED: <预期结果>\n"
            "```"
        )
        ec_retry, retry_output = _bridge_call(
            "task-code", retry_prompt, state.chatgpt_url,
            state.bridge_script, state.cwd,
            signature=f"task-code-retry:{state.name}:{task.id}",
            timeout=180,
        )
        if ec_retry == 0:
            test_cmds = parse_test_commands(retry_output)
        if not test_cmds:
            task.status = "failed"
            task.last_verdict = "BLOCKED"
            task.last_fix_note = "GPT 未提供有效的测试命令"
            state.save()
            return False

    # GPT 经隧道插件直接改本机文件；本地以文件清单差异作为已落盘的客观证据。
    manifest_after = _workspace_manifest(state.cwd)
    changed = _manifest_diff(manifest_before, manifest_after)
    state.workspace_manifest = manifest_after
    state.save()
    if not any(changed):
        task.status = "blocked"
        task.last_verdict = "BLOCKED"
        task.last_fix_note = "GPT 声称完成，但工作目录无任何文件变化"
        state.save()
        print(f"[Task {task.id}] 🚫 {task.last_fix_note}")
        return False
    change_summary = _changed_summary(changed)
    print(f"[Task {task.id}] ✅ 已核对文件变更：\n{change_summary}")

    # 运行测试
    task.status = "testing"
    task.test_commands = [cmd for cmd, _ in test_cmds]

    all_test_results = []
    all_passed = True

    for cmd, expected in test_cmds:
        print(f"[Task {task.id}] 🔬 运行测试：{cmd}")
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=120, cwd=state.cwd
        )
        passed = (r.returncode == 0)
        if not passed:
            all_passed = False
        result = (
            f"COMMAND: {cmd}\n"
            f"EXPECTED: {expected}\n"
            f"EXIT_CODE: {r.returncode}\n"
            f"STDOUT:\n{r.stdout[:2000]}\n"
            f"STDERR:\n{r.stderr[:2000]}"
        )
        all_test_results.append(result)
        status_icon = "✅" if passed else "❌"
        print(f"[Task {task.id}] {status_icon} exit={r.returncode} "
              f"({cmd[:60]})")

    task.test_results = "\n\n---\n\n".join(all_test_results)
    task.test_passed = all_passed

    # GPT 审查测试结果并决定下一步
    review_prompt = (
        f"【任务 {task.id}】{task.title}\n\n"
        f"【本轮文件变更（本地核验）】\n{change_summary}\n\n"
        f"【本地 Agent 测试结果】\n{task.test_results}\n\n"
        "请根据测试结果决定下一步行动。严格按以下格式输出：\n\n"
        "1. APPROVED - 测试全部通过，代码符合要求，任务完成\n"
        "2. NEEDS_FIX: <具体问题> - 测试失败或有 bug，需要修复。请说明：\n"
        "   - 哪里出错了\n"
        "   - 如何修复（给出具体代码改动或思路）\n"
        "3. BLOCKED: <原因> - 遇到无法自动解决的问题（如缺少依赖、环境问题、需求不明确）\n\n"
        "只输出一行裁决，不要解释，不要多余文字。"
    )

    ec2, verdict_text = _bridge_call(
        "task-review", review_prompt, state.chatgpt_url,
        state.bridge_script, state.cwd,
        evidence=task.test_results,
        level="L2",
        signature=f"task-review:{state.name}:{task.id}",
        timeout=180,
    )
    if ec2 != 0:
        print(f"[Task {task.id}] ⚠ GPT 审查调用失败（exit={ec2}），默认标记失败")
        task.last_verdict = "BLOCKED"
        task.last_fix_note = f"GPT 审查调用失败 exit={ec2}"
        task.status = "failed"
        state.save()
        return False

    verdict, detail = parse_verdict(verdict_text)
    task.last_verdict = verdict
    task.last_fix_note = detail

    print(f"[Task {task.id}] 🔍 GPT 裁决：{verdict}")
    if detail:
        print(f"           说明：{detail[:300]}")

    if verdict == "APPROVED":
        task.status = "approved"
        task.completed_at = datetime.utcnow().isoformat()
        state.save()
        print(f"[Task {task.id}] ✅ 任务通过审查，已完成！")
        return True

    if verdict == "BLOCKED":
        task.status = "blocked"
        state.save()
        print(f"[Task {task.id}] 🚫 任务被阻塞，需要人工介入")
        print(f"           原因：{detail}")
        return False

    # NEEDS_FIX：进入自动修复循环
    print(f"[Task {task.id}] 🔧 需要修复，进入自动修复循环...")
    return _auto_fix_loop(state, task, code_output, test_cmds, max_attempts)


def _auto_fix_loop(state: ProjectState, task: Task,
                   original_code: str,
                   test_cmds: List[Tuple[str, str]],
                   max_attempts: int) -> bool:
    """
    自动修复循环：GPT 经隧道插件原地修复 → Agent 核对变更并测试 → GPT 审查
    """
    fix_note = task.last_fix_note
    code_output = original_code

    for attempt in range(task.attempts, max_attempts):
        task.attempts = attempt + 1
        task.status = "fixing"
        print(f"\n[Task {task.id}] 🔧 自动修复轮次 {task.attempts}/{max_attempts}")
        print(f"           修复提示：{fix_note[:200]}")

        snap = _make_snapshot(state.name, state.cwd, f"task{task.id}-fix{task.attempts}")
        state.snapshots.append(str(snap))
        manifest_before_fix = _workspace_manifest(state.cwd)
        state.save()

        fix_prompt = (
            f"【任务 {task.id}】{task.title}\n\n"
            f"【测试结果】\n{task.test_results}\n\n"
            f"【你的审查反馈】{fix_note}\n\n"
            "请通过隧道插件直接修改本机工作目录中的文件进行修复。不要输出代码；"
            "仅在 bash 代码块中输出测试命令：\nTEST: ...\nEXPECTED: ..."
        )

        ec, new_code = _bridge_call(
            "task-code", fix_prompt, state.chatgpt_url,
            state.bridge_script, state.cwd,
            signature=f"task-fix:{state.name}:{task.id}:{task.attempts}",
            timeout=360,
        )
        if ec != 0:
            print(f"[Task {task.id}] ⚠ 修复调用失败（exit={ec}）")
            task.status = "failed"
            task.last_verdict = "BLOCKED"
            task.last_fix_note = f"修复调用失败 exit={ec}"
            state.save()
            return False

        code_output = new_code

        new_test_cmds = parse_test_commands(new_code)
        if new_test_cmds:
            test_cmds = new_test_cmds  # 如果 GPT 更新了测试命令

        if not test_cmds:
            task.status = "blocked"
            task.last_verdict = "BLOCKED"
            task.last_fix_note = "GPT 修复后未提供有效测试命令"
            state.save()
            return False

        manifest_after_fix = _workspace_manifest(state.cwd)
        changed_fix = _manifest_diff(manifest_before_fix, manifest_after_fix)
        state.workspace_manifest = manifest_after_fix
        state.save()
        if not any(changed_fix):
            task.status = "blocked"
            task.last_verdict = "BLOCKED"
            task.last_fix_note = "GPT 声称已修复，但工作目录无任何文件变化"
            state.save()
            print(f"[Task {task.id}] 🚫 {task.last_fix_note}")
            return False
        change_summary = _changed_summary(changed_fix)
        print(f"[Task {task.id}] ✅ 已核对修复变更：\n{change_summary}")

        # 重新运行测试
        task.status = "testing"
        all_test_results = []
        all_passed = True

        for cmd, expected in test_cmds:
            print(f"[Task {task.id}] 🔬 重新测试：{cmd}")
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=120, cwd=state.cwd
            )
            passed = (r.returncode == 0)
            if not passed:
                all_passed = False
            result = (
                f"COMMAND: {cmd}\n"
                f"EXPECTED: {expected}\n"
                f"EXIT_CODE: {r.returncode}\n"
                f"STDOUT:\n{r.stdout[:2000]}\n"
                f"STDERR:\n{r.stderr[:2000]}"
            )
            all_test_results.append(result)
            status_icon = "✅" if passed else "❌"
            print(f"[Task {task.id}] {status_icon} exit={r.returncode}")

        task.test_results = "\n\n---\n\n".join(all_test_results)
        task.test_passed = all_passed

        # GPT 重新审查
        review_prompt = (
            f"【任务 {task.id}】{task.title}\n\n"
            f"【修复轮次】{task.attempts}/{max_attempts}\n\n"
            f"【本轮修复变更（本地核验）】\n{change_summary}\n\n"
            f"【本地 Agent 测试结果】\n{task.test_results}\n\n"
            "请根据测试结果决定下一步：\n"
            "APPROVED - 通过\n"
            "NEEDS_FIX: <问题> - 继续修复\n"
            "BLOCKED: <原因> - 无法自动解决"
        )

        ec_review, verdict_text = _bridge_call(
            "task-review", review_prompt, state.chatgpt_url,
            state.bridge_script, state.cwd,
            evidence=task.test_results,
            level="L2",
            signature=f"task-review:{state.name}:{task.id}:fix{task.attempts}",
            timeout=180,
        )
        if ec_review != 0:
            print(f"[Task {task.id}] ⚠ 审查调用失败")
            continue

        verdict, detail = parse_verdict(verdict_text)
        task.last_verdict = verdict
        task.last_fix_note = detail
        state.save()

        print(f"[Task {task.id}] 🔍 GPT 裁决：{verdict}")
        if detail:
            print(f"           {detail[:200]}")

        if verdict == "APPROVED":
            task.status = "approved"
            task.completed_at = datetime.utcnow().isoformat()
            state.save()
            print(f"[Task {task.id}] ✅ 修复成功，任务完成！")
            return True

        if verdict == "BLOCKED":
            task.status = "blocked"
            state.save()
            print(f"[Task {task.id}] 🚫 任务被阻塞")
            return False

        # NEEDS_FIX：继续下一轮
        fix_note = detail
        print(f"[Task {task.id}] 🔄 继续修复...")

    # 达到最大尝试次数
    task.status = "failed"
    task.last_verdict = "BLOCKED"
    task.last_fix_note = f"达到最大修复次数 {max_attempts}"
    state.save()
    print(f"[Task {task.id}] ❌ 修复失败：达到最大尝试次数")
    return False


# =============================================================================
# 子命令：run-task
# =============================================================================

def cmd_run_task(args) -> int:
    name = args.name
    task_id = args.task_id
    max_attempts = args.max_attempts or MAX_FIX_ATTEMPTS
    state = ProjectState.load(name)

    if not state.task_locked:
        print(f"[run-task] ❌ 计划未锁定，请先 run lock --name {name}")
        return 1

    if not state.tasks:
        print(f"[run-task] ❌ 没有找到任务，请 run lock --name {name}")
        return 1

    if task_id:
        pending = [t for t in state.tasks if t.id == task_id]
    else:
        pending = [t for t in state.tasks if t.status == "pending"]

    if not pending:
        done = [t for t in state.tasks if t.status == "approved"]
        blocked = [t for t in state.tasks if t.status in ("blocked", "failed")]
        print(f"[run-task] 所有任务已处理完毕：")
        print(f"  ✅ 完成: {len(done)} | ❌ 失败/阻塞: {len(blocked)}")
        if blocked:
            for t in blocked:
                print(f"       [{t.id}] {t.title} ({t.status}): {t.last_fix_note[:80]}")
        return 0

    task = pending[0]
    state.current_task_id = task.id
    state.save()

    ok = execute_task(state, task, max_attempts=max_attempts)

    # 继续下一个 pending 任务（除非 autonomous=False）
    if not args.autonomous:
        state.save()
        return 0 if ok else 1

    # autonomous 模式：自动跑完所有
    for t in state.tasks:
        if t.status not in ("pending", "coding", "testing", "approved"):
            continue
        if t.id == task.id:
            continue
        state.current_task_id = t.id
        state.save()
        ok = execute_task(state, t, max_attempts=max_attempts)
        if not ok and not args.continue_on_fail:
            print(f"[run-all] 任务 {t.id} 失败，停止（--continue-on-fail 未设置）")
            return 1

    # 最终统计
    approved = sum(1 for t in state.tasks if t.status == "approved")
    failed = sum(1 for t in state.tasks if t.status in ("failed", "blocked"))
    print(f"\n{'='*60}")
    print(f"[run-all] 完成！✅ {approved}/{len(state.tasks)} 任务成功，"
          f"❌ {failed} 失败")
    if failed:
        for t in state.tasks:
            if t.status in ("failed", "blocked"):
                print(f"  [{t.id}] {t.title}: {t.last_fix_note[:100]}")
    return 0 if failed == 0 else 1


# =============================================================================
# 子命令：status
# =============================================================================

def cmd_status(args) -> int:
    name = args.name
    state = ProjectState.load(name)

    print(f"\n项目: {state.name}")
    print(f"需求: {state.requirement[:80]}")
    print(f"工作目录: {state.cwd}")
    print(f"浏览器: {state.browser}")
    print(f"快照: {len(state.snapshots)} 份" + (f"（最近：{state.snapshots[-1]}）" if state.snapshots else ""))
    print(f"锁定: {'是' if state.task_locked else '否'}")
    print(f"当前任务: {state.current_task_id or '无'}")
    print(f"\n任务列表（共 {len(state.tasks)} 个）：")
    status_icon = {
        "pending":   "⏳ pending",
        "coding":    "🔨 coding",
        "testing":   "🧪 testing",
        "approved":  "✅ approved",
        "failed":    "❌ failed",
        "blocked":   "🚫 blocked",
    }
    for t in state.tasks:
        icon = status_icon.get(t.status, t.status)
        fix_info = f"  ← {t.last_fix_note[:50]}" if t.last_fix_note else ""
        print(f"  [{t.id:2d}] {icon}  {t.title}{fix_info}")
    return 0


# =============================================================================
# 子命令：restore
# =============================================================================

def cmd_restore(args) -> int:
    """从指定快照恢复工作目录（只覆盖快照内文件；快照后新增的文件保留并提示）。"""
    state = ProjectState.load(args.name)
    snap = Path(args.snapshot)
    if not snap.is_dir():
        print(f"[restore] ❌ 快照不存在：{snap}")
        return 1
    cwd = Path(state.cwd)
    restored = 0
    for src in sorted(snap.rglob("*")):
        if src.is_file():
            rel = src.relative_to(snap)
            dst = cwd / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
    state.workspace_manifest = _workspace_manifest(state.cwd)
    state.save()
    print(f"[restore] ✅ 已从快照恢复 {restored} 个文件 → {state.cwd}")
    print("[restore] 注意：快照之后新增的文件未删除，如需处理请人工核对。")
    return 0


# =============================================================================
# CLI 入口
# =============================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Desktop Agent Orchestrator — 端到端任务编排器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            工作流示例：
              orchestrate.py init --name myproj --requirement "迁移到 FastAPI" \\
                --target-url "https://chatgpt.com/c/xxx" --cwd /path/to/project
              orchestrate.py refine --name myproj --feedback "第3步太复杂"
              orchestrate.py lock --name myproj
              orchestrate.py run-task --name myproj
              orchestrate.py run-task --name myproj --autonomous  # 全自动跑完
              orchestrate.py status --name myproj
              orchestrate.py restore --name myproj --snapshot <快照目录>
        """)
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="初始化项目（生成初始方案）")
    p_init.add_argument("--name", required=True, help="项目名称（英文，唯一标识）")
    p_init.add_argument("--requirement", required=True, help="项目需求描述")
    p_init.add_argument("--target-url", required=True, help="ChatGPT Tab URL")
    p_init.add_argument("--cwd", default=None, help="本地工作目录（默认当前目录；GPT 经隧道插件直读写此目录）")
    p_init.add_argument("--browser", default="safari",
                        choices=["safari", "chrome", "edge", "brave", "arc", "chromium"],
                        help="浏览器类型（默认 safari）")

    # refine
    p_refine = sub.add_parser("refine", help="更新/重新生成方案")
    p_refine.add_argument("--name", required=True, help="项目名称")
    p_refine.add_argument("--feedback", default=None,
                          help="用户反馈（如：第3步太复杂，能否拆成2步）")

    # lock
    p_lock = sub.add_parser("lock", help="锁定方案（仅解析并保存本地任务状态，不修改工作目录）")
    p_lock.add_argument("--name", required=True, help="项目名称")

    # run-task
    p_run = sub.add_parser("run-task", help="执行任务（单任务闭环或全自动化）")
    p_run.add_argument("--name", required=True, help="项目名称")
    p_run.add_argument("--task-id", type=int, default=None,
                       help="指定任务 ID（默认：下一个 pending 任务）")
    p_run.add_argument("--autonomous", action="store_true",
                       help="自动执行所有 pending 任务，不等待用户确认")
    p_run.add_argument("--continue-on-fail", action="store_true",
                       help="autonomous 模式下，任务失败也继续下一个")
    p_run.add_argument("--max-attempts", type=int, default=None,
                       help=f"单任务最大修复轮次（默认 {MAX_FIX_ATTEMPTS}）")

    # status
    p_st = sub.add_parser("status", help="查看项目状态")
    p_st.add_argument("--name", required=True, help="项目名称")

    # restore
    p_rs = sub.add_parser("restore", help="从快照恢复工作目录")
    p_rs.add_argument("--name", required=True, help="项目名称")
    p_rs.add_argument("--snapshot", required=True, help="快照目录路径（status 中可查）")

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd == "init":
        return cmd_init(args)
    elif args.cmd == "refine":
        return cmd_refine(args)
    elif args.cmd == "lock":
        return cmd_lock(args)
    elif args.cmd == "run-task":
        return cmd_run_task(args)
    elif args.cmd == "status":
        return cmd_status(args)
    elif args.cmd == "restore":
        return cmd_restore(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
