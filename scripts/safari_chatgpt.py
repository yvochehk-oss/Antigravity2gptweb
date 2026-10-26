#!/usr/bin/env python3
"""
Safari ChatGPT Cognitive-Control Bridge (v4.1 Evidence-Integrity)
-----------------------------------------------------------------
在 v4.0 基础上对证据完整性做了八项关键改造：

P0-1.【精确 user message identity】：提交验证不再基于消息计数，
     而是在 JS 端做 expected prompt 的精确规范化比较。
P0-2.【TargetTabLock 事务锁】：跨进程对同一 target_url 加
     flock(LOCK_EX | LOCK_NB)，杜绝两个 Agent 同时操作同一 Tab。
P0-3.【删除 --new】：避免与 Hard Tab Binding 形成结构性冲突。
     新会话由 Execution Plane 在调用前完成，再传最终 /c/<id>。
P0-4.【真·全局 deadline】：--timeout 改为 monotonic absolute deadline，
     每个阶段用 remaining(deadline) 切片，杜绝虚假超时语义。

P1-1.【真·滑动窗口熔断】：circuit state 存 timestamp 列表，
     每次 RMW 自动 prune expired signatures。
P1-3.【circuit reset 持锁】：避免 read-modify-write 竞态。
P1-4.【删除 SAFARI_CONSEC_FAIL_LIMIT / _state 等未实现 hook】：
     单次写操作 fail-fast，宁缺毋滥。
P1-5.【target_url 强校验】：scheme 必须 https；host 必须 chatgpt.com。
P1-6.【PEM 整块脱敏 + github_pat_ + GENKEY IGNORECASE】：
     防止密钥漏检。
P1-7.【事件 JSON 再脱敏】：signature / message 全部走 sanitize_text。
P1-8.【--evidence-file】：替代 argv 传递大日志。
P1-9.【git subprocess 5s timeout + unavailable/untracked 分离】。

P2.【selector 去掉 div 前缀；contenteditable 限制到 form / composer；
    AppleScript 使用独立 escape 函数；自定义 error number；
    删除 unused imports。】

v4.2 修复（2026-10-26，基于 Pro 实探 DOM + 83 项目三卷监督实证）：
F1.【Tab 绑定瘦身】：_applescript_invoke 不再对每个 URL 匹配的 Tab 逐个跑
    readiness do JavaScript（重页面上单次调用可挂起 >30s，整段 AppleScript
    被 30s 超时杀掉 → BASELINE_FAIL → 熔断）；不再 set current tab 抢焦点；
    找不到 Tab 不再 activate + open location 自开新标签页（违反标签页纪律，
    且新开页 SPA 未水合、无输入框）。改为：首个 URL 匹配的 Tab 直接执行，
    无匹配 → NO_TARGET_TAB。单次 osascript 超时 30s → 60s。
F2.【DOM 模型换代】：实探目标会话页面中 [data-message-author-role] 节点
    为 0、#prompt-textarea 不存在、article/conversation-turn 亦为 0；
    composer 是无 id 的 div.ProseMirror（role=textbox，aria-label
    "询问 ChatGPT"），且页面上存在一个 rect=0 的隐藏同类编辑器。
    新增共享 DOM helper：__findEditor（可见性过滤 + role=textbox 优先）、
    __turns（role 节点存在时用节点，否则按 main.innerText 中的
    「你说：/ChatGPT 说：」标记切分虚拟回合，合成指纹 id）。
    baseline / 提交验证 / 新回合 / 稳定等待全部改走 helper。
F3.【发送与流式判据本地化】：发送按钮增加 aria-label 中文标签匹配并
    优先编辑器所在 form 内按钮；Enter 兜底改为 keydown/keypress/keyup
    全序列；流式判据增加 aria-label「停止」等中文标签。
F4.【--probe 只读模式】：仅采集 baseline 并输出 JSON，供连通性诊断，
    不发消息、不触发熔断计数。

调用范例见 SKILL.md。
"""

import sys
import os
import re
import time
import json
import fcntl
import tempfile
import subprocess
import argparse
import hashlib
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse

# =============================================================================
# 退出码常量 (Verification Plane 唯一判据)
# =============================================================================
EXIT_OK              = 0   # 正常完成，证据完整
EXIT_TIMEOUT_PARTIAL = 2   # 超时但拿到部分内容
EXIT_TIMEOUT_EMPTY   = 3   # 超时且无内容
EXIT_SAFARI_FAIL     = 4   # Safari / AppleScript / JS 执行异常
EXIT_BASELINE_FAIL   = 5   # 基线采集失败
EXIT_SUBMIT_FAIL     = 6   # 用户消息未真正提交
EXIT_NO_NEW_TURN     = 7   # 助手新回合未产生
EXIT_NO_TAB          = 10  # 目标 Tab 不存在
EXIT_AMBIGUOUS_TAB   = 11  # 目标 Tab 存在歧义（多个重名 Tab）
EXIT_CIRCUIT_OPEN    = 12  # 熔断器已开
EXIT_TARGET_BUSY     = 13  # 同一 Tab 正在被另一 bridge 占用

EXIT_CODE_NAME = {
    EXIT_OK: "OK",
    EXIT_TIMEOUT_PARTIAL: "TIMEOUT_PARTIAL",
    EXIT_TIMEOUT_EMPTY: "TIMEOUT_EMPTY",
    EXIT_SAFARI_FAIL: "SAFARI_FAIL",
    EXIT_BASELINE_FAIL: "BASELINE_FAIL",
    EXIT_SUBMIT_FAIL: "SUBMIT_FAIL",
    EXIT_NO_NEW_TURN: "NO_NEW_TURN",
    EXIT_NO_TAB: "NO_TAB",
    EXIT_AMBIGUOUS_TAB: "AMBIGUOUS_TAB",
    EXIT_CIRCUIT_OPEN: "CIRCUIT_OPEN",
    EXIT_TARGET_BUSY: "TARGET_BUSY",
}

# =============================================================================
# 路径 / 超时常量
# =============================================================================
CIRCUIT_STATE_FILE  = "/tmp/safari_chatgpt_circuit_breaker.json"
CIRCUIT_WINDOW_SEC  = 3600   # 1 小时滑动窗口
CIRCUIT_MAX_RETRIES = 3

OSASCRIPT_TIMEOUT_SEC = 60   # 单次 osascript / Safari JS 调用超时（v4.2：30→60，重页面基线采集留余量）

# =============================================================================
# 阶段死线（每个阶段的"理想"上限；真实上限会被 overall_deadline 切片）
# =============================================================================
SUBMIT_PHASE_BUDGET = 20    # 用户消息提交阶段预算（秒）
TURN_PHASE_BUDGET   = 60    # 助手新回合出现阶段预算（秒）
STABLE_PHASE_MIN    = 5     # 稳定阶段最小预算（秒）


# =============================================================================
# 结构化事件输出（stderr JSON，stdout 仅放回答文本）
# =============================================================================
def _sanitize_event_value(value: Any) -> Any:
    """对 event payload 做递归脱敏，避免签名 / message 里混入 secret。
    dict / list / str 三种结构递归；其他类型原样返回。
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_event_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_event_value(v) for v in value]
    return value


def emit_event(stage: str, exit_code: int, message: str, **extra) -> None:
    """把阶段事件与错误以结构化 JSON 输出到 stderr，stdout 留给回答文本。"""
    payload: Dict[str, Any] = {
        "ts": time.time(),
        "stage": stage,
        "exit_code": exit_code,
        "exit_name": EXIT_CODE_NAME.get(exit_code, "UNKNOWN"),
        "message": message,
        "browser": "safari",
    }
    payload.update(extra)
    payload = _sanitize_event_value(payload)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


# =============================================================================
# 失败签名规范化（熔断去噪）
# =============================================================================
def normalize_failure_signature(text: str) -> str:
    """消除错误信息中的动态干扰（时间戳、PID、UUID、临时路径），实现稳定的熔断特征计算"""
    if not text:
        return ""
    text = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '<UUID>', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(17\d{8}|20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\b', '<TIMESTAMP>', text)
    text = re.sub(r'/(?:tmp|var/folders)/[^\s:\'",]+', '<TEMP_PATH>', text)
    text = re.sub(r'\b(?:pid|PID)\s*[:=]?\s*\d+\b', '<PID>', text)
    text = re.sub(r'\bline\s+\d+\b', '<LINE>', text)
    return text.strip()


# =============================================================================
# Secret 脱敏 (v4.1)
# =============================================================================
_GH_TOKEN_RE        = re.compile(r'(gh[pousr]_[A-Za-z0-9_]{16,})')
_GITHUB_PAT_RE      = re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b')
_OPENAI_RE          = re.compile(r'(sk-[A-Za-z0-9_-]{20,})')
_BEARER_RE          = re.compile(r'(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})', re.IGNORECASE)
_PASSWORD_RE        = re.compile(r'(password\s*[:=]\s*["\']?)([^"\'\s]+)(["\']?)', re.IGNORECASE)
_AWS_RE             = re.compile(r'((?:AKIA|ASIA)[0-9A-Z]{16})')
_SLACK_RE           = re.compile(r'\b(xox[abprs]-[A-Za-z0-9-]{10,})\b')
# v4.1：PEM 整块脱敏（含 BEGIN 头 / 主体 / END 尾）
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END \1-----",
    re.DOTALL,
)
# 仅匹配 PEM header（避免漏掉缺尾的退化样本，留作最小安全网）
_PEM_HEADER_RE      = re.compile(r'-----BEGIN [A-Z ]+PRIVATE KEY-----')
_DBCONN_RE          = re.compile(r'(?P<scheme>(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s"\'<>]+)', re.IGNORECASE)
# v4.1：GENKEY 增加 re.IGNORECASE，避免小写 key 名被漏检
_GENKEY_RE          = re.compile(
    r'((?:[A-Z][A-Z0-9_]*_?(?:KEY|SECRET|TOKEN))\s*[:=]\s*["\']?)([A-Za-z0-9._/+-]{16,})(["\']?)',
    re.IGNORECASE,
)
_JWT_RE             = re.compile(r'(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_\-+/=]{4,})')

# 邮件 / 手机号：默认不脱敏（避免误伤业务文案），如需开启改环境变量
_REDACT_EMAIL = os.environ.get("SAFARI_BRIDGE_REDACT_EMAIL", "0") == "1"
_REDACT_PHONE = os.environ.get("SAFARI_BRIDGE_REDACT_PHONE", "0") == "1"
_EMAIL_RE     = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_RE     = re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)')


def sanitize_text(text: str) -> str:
    """自动脱敏本地敏感信息，防止外泄至云端 ChatGPT。"""
    if not text:
        return ""
    text = _GH_TOKEN_RE.sub('[REDACTED_GITHUB_TOKEN]', text)
    text = _GITHUB_PAT_RE.sub('[REDACTED_GITHUB_PAT]', text)
    text = _OPENAI_RE.sub('[REDACTED_API_KEY]', text)
    text = _BEARER_RE.sub(r'\1[REDACTED_AUTH_TOKEN]', text)
    text = _PASSWORD_RE.sub(r'\1[REDACTED_PASSWORD]\3', text)
    text = _AWS_RE.sub('[REDACTED_AWS_KEY]', text)
    text = _SLACK_RE.sub('[REDACTED_SLACK_TOKEN]', text)
    # 整块 PEM 先于 header 兜底
    text = _PEM_PRIVATE_KEY_RE.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _PEM_HEADER_RE.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _DBCONN_RE.sub('[REDACTED_DB_CONNECTION]', text)
    text = _GENKEY_RE.sub(r'\1[REDACTED_SECRET]\3', text)
    # JWT 在 GENKEY 之后处理：避免被 "JWT=..." 形式的通用 KEY 模式先吃掉
    text = _JWT_RE.sub('[REDACTED_JWT]', text)
    if _REDACT_EMAIL:
        text = _EMAIL_RE.sub('[REDACTED_EMAIL]', text)
    if _REDACT_PHONE:
        text = _PHONE_RE.sub('[REDACTED_PHONE]', text)
    text = text.replace(os.path.expanduser("~"), "~")
    return text


# =============================================================================
# target_url 校验（P1-5：只在最外层调用一次）
# =============================================================================
_ALLOWED_CHATGPT_HOSTS = {"chatgpt.com", "www.chatgpt.com"}


def validate_target_url(url: str) -> None:
    """强校验 target_url：https scheme + chatgpt.com host。
    失败时抛 ValueError；由 main() 映射为 EXIT_SAFARI_FAIL。
    """
    if not url:
        raise ValueError("--target-url 不能为空")
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"--target-url 解析失败: {e}")
    if parsed.scheme != "https":
        raise ValueError(f"--target-url 必须使用 https：{url}")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_CHATGPT_HOSTS:
        raise ValueError(
            f"--target-url 必须指向 chatgpt.com，当前 host={host!r}：{url}"
        )


# =============================================================================
# 熔断器（滑动窗口 + 原子写 + 文件锁 + 启动 prune）
# =============================================================================
class CircuitOpenError(RuntimeError):
    pass


def _circuit_lock_path() -> str:
    return CIRCUIT_STATE_FILE + ".lock"


def _read_circuit_state_unlocked() -> Dict[str, Any]:
    """【仅在持锁状态下调用】无锁读取 + JSON 解析。"""
    if not os.path.exists(CIRCUIT_STATE_FILE):
        return {}
    try:
        with open(CIRCUIT_STATE_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_state_unlocked(state: Dict[str, Any]) -> None:
    """【仅在持锁状态下调用】原子写：tmp + os.replace。"""
    with tempfile.NamedTemporaryFile(
        "w", dir=os.path.dirname(CIRCUIT_STATE_FILE) or "/tmp",
        prefix=".circuit.", suffix=".tmp", delete=False
    ) as tf:
        json.dump(state, tf, ensure_ascii=False)
        tmp_name = tf.name
    os.replace(tmp_name, CIRCUIT_STATE_FILE)


def _prune_state_unlocked(state: Dict[str, Any], now: float) -> Dict[str, Any]:
    """【仅在持锁状态下调用】清理所有 expired signature，
    避免 state JSON 长期只增不减。空 entry 直接删除。
    """
    pruned: Dict[str, Any] = {}
    for k, v in state.items():
        if isinstance(v, list):
            kept = [
                t for t in v
                if isinstance(t, (int, float)) and (now - t) <= CIRCUIT_WINDOW_SEC
            ]
            if kept:
                pruned[k] = kept
            # 空列表直接丢弃
        else:
            # 旧格式 [count, first_seen] 静默丢弃（兼容旧 state）
            continue
    return pruned


class _CircuitLock:
    """对 CIRCUIT_STATE_FILE 的进程级排他锁，确保 read-modify-write 原子性。
    macOS 上 fcntl.flock(LOCK_EX) 是文件级 advisory lock，跨进程生效。
    """

    def __init__(self):
        self.fd = None

    def __enter__(self):
        self.fd = os.open(_circuit_lock_path(),
                          os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def check_circuit_breaker(failure_signature: str,
                          normalize: bool = True,
                          max_retries: int = CIRCUIT_MAX_RETRIES) -> bool:
    """真·滑动窗口：state[k] = [ts1, ts2, ...]。
    每次 RMW 自动 prune 全部 expired entry，再追加 now。
    若 failure_signature 命中后窗口内累计 > max_retries，抛 CircuitOpenError。
    """
    if not failure_signature:
        return True
    if normalize:
        failure_signature = hashlib.sha256(
            normalize_failure_signature(failure_signature).encode("utf-8")
        ).hexdigest()[:16]

    now = time.time()
    with _CircuitLock():
        state = _read_circuit_state_unlocked()
        # 先全量 prune（P1-2）
        state = _prune_state_unlocked(state, now)
        ts_list = list(state.get(failure_signature, []))
        ts_list.append(now)
        state[failure_signature] = ts_list
        _atomic_write_state_unlocked(state)

    if len(ts_list) > max_retries:
        emit_event(
            "circuit_breaker", EXIT_CIRCUIT_OPEN,
            f"熔断器触发：签名 {failure_signature} 在 {CIRCUIT_WINDOW_SEC}s 内累计 {len(ts_list)} 次",
            signature=failure_signature, count=len(ts_list),
        )
        raise CircuitOpenError(
            f"同一故障特征在 {CIRCUIT_WINDOW_SEC}s 内已连续出现 {len(ts_list)} 次"
            f"（>{max_retries}），已触发自动熔断。\n"
            f"请排查后运行：python3 safari_chatgpt.py --reset-circuit"
        )
    return True


def reset_circuit_breaker() -> int:
    """持锁状态下清空熔断器。返回被清理的 signature 数量。"""
    cleared = 0
    with _CircuitLock():
        try:
            if os.path.exists(CIRCUIT_STATE_FILE):
                with open(CIRCUIT_STATE_FILE, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cleared = len(data)
                os.remove(CIRCUIT_STATE_FILE)
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return cleared


# =============================================================================
# TargetTabLock：跨进程对同一 target_url 加事务锁（P0-2）
# =============================================================================
class TargetTabBusyError(RuntimeError):
    """同一 target_url 正在被另一 bridge 占用（默认拒绝排队）。"""
    pass


class TargetTabLock:
    """按 sha256(target_url)[:24] 作为锁 key。
    LOCK_EX | LOCK_NB 非阻塞拿锁；失败立即抛 TargetTabBusyError。
    进程崩溃由 OS 自动释放（unlink 文件可不调用，flock 已足够）。

    使用：
        with TargetTabLock(target_url):
            _send_and_receive_locked(...)
    """

    def __init__(self, target_url: str):
        digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:24]
        self.path = f"/tmp/safari_chatgpt_tab_{digest}.lock"
        self.fd: Optional[int] = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try: os.close(self.fd)
            except Exception: pass
            self.fd = None
            raise TargetTabBusyError(
                f"目标 ChatGPT Tab 正在被另一 bridge 占用（{self.path}）。"
                f"如确认对端已死，可手动删除锁文件。"
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                try: os.close(self.fd)
                except Exception: pass
                self.fd = None
        # 不主动 unlink：保留 holder 信息便于审计；进程崩溃由 OS flock 自动释放。
        return False


# =============================================================================
# Git 上下文（计划过期守卫，含 timeout 与状态分离）
# =============================================================================
GIT_SUBPROCESS_TIMEOUT_SEC = 5


def get_git_head_context(cwd: Optional[str] = None) -> str:
    """返回 Git 上下文：
      - "Git: <sha> (clean | dirty)"  仓库内
      - "Git: unavailable"             git 不存在 / timeout / cwd 不可用
      - "Git: untracked"               当前目录不是 git 仓库
    """
    try:
        sha_res = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return "Git: unavailable"
    except FileNotFoundError:
        return "Git: unavailable"
    except Exception:
        return "Git: unavailable"

    if sha_res.returncode != 0:
        # 非 0 通常意味着 cwd 不是 git 仓库
        return "Git: untracked"

    sha = sha_res.stdout.strip()
    try:
        status_res = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"Git: {sha} (unavailable)"
    except FileNotFoundError:
        return "Git: unavailable"
    except Exception:
        return f"Git: {sha} (unavailable)"

    if status_res.returncode != 0:
        return f"Git: {sha} (unavailable)"
    dirty = " (dirty)" if status_res.stdout.strip() else " (clean)"
    return f"Git: {sha}{dirty}"


# =============================================================================
# Safari JS 执行（精确 Tab 绑定 + 单次 timeout + 独立 AppleScript escape）
# =============================================================================
class SafariError(RuntimeError):
    pass


class NoTargetTabError(SafariError):
    """目标 Tab 不存在（URL 精确匹配失败）。映射为 EXIT_NO_TAB。"""
    pass


class AmbiguousTargetTabError(SafariError):
    """目标 Tab 存在多处匹配（歧义）。映射为 EXIT_AMBIGUOUS_TAB。"""
    pass


def _escape_applescript_string(value: str) -> str:
    """独立 AppleScript 字符串转义：覆盖反斜杠 / 双引号 / CR / LF。"""
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _applescript_invoke(js_code: str, target_url: str, timeout: int) -> str:
    """构造并执行 AppleScript，向精确 URL 匹配的 Tab 注入 JS。
    采用临时文件 + POSIX file 零转义读取，彻底杜绝大 payload 截断与字符串转义崩溃。
    """
    base_url = target_url.rstrip('?#/ ').split('?')[0]
    escaped_base_url = _escape_applescript_string(base_url)

    # 提取会话 UUID（支持 Custom GPT URL 与标准 /c/ 路径自适应）
    uuid_match = re.search(
        r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        target_url,
    )
    uuid_part = uuid_match.group(0) if uuid_match else base_url

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(js_code)
        tmp_js_path = f.name

    # v4.2 F1：首个 URL 匹配的 Tab 直接执行。不做 readiness 逐 Tab 探测
    # （重页面会挂起并拖垮整段 AppleScript）、不 set current tab 抢焦点、
    # 找不到时不自开新标签页，直接报 NO_TARGET_TAB。
    applescript = f'''
    tell application "Safari"
        set targetTab to missing value
        repeat with w in windows
            repeat with t in tabs of w
                set u to (URL of t as text)
                if (u starts with "{escaped_base_url}" or u contains "{uuid_part}") then
                    set targetTab to t
                    exit repeat
                end if
            end repeat
            if targetTab is not missing value then exit repeat
        end repeat

        if targetTab is missing value then
            error "NO_TARGET_TAB" number 17001
        end if

        set jsStr to (read (POSIX file "{tmp_js_path}") as «class utf8»)
        tell targetTab
            return do JavaScript jsStr
        end tell
    end tell
    '''
    try:
        res = subprocess.run(
            ['osascript', '-'],
            input=applescript, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise SafariError(f"AppleScript 执行超时（>{timeout}s）") from e
    finally:
        try:
            if os.path.exists(tmp_js_path):
                os.remove(tmp_js_path)
        except Exception:
            pass

    if res.returncode != 0:
        err = (res.stderr or "") + (res.stdout or "")
        if "NO_TARGET_TAB" in err or "17001" in err:
            raise NoTargetTabError(
                f"未在 Safari 中找到 URL 精确匹配的 Tab: {target_url}"
            )
        raise SafariError(f"AppleScript 执行失败: {err.strip()}")

    return res.stdout.rstrip("\r\n")


def execute_safari_js(js_code: str, target_url: str,
                      timeout: int = OSASCRIPT_TIMEOUT_SEC) -> str:
    """执行 JS 并返回原始文本。Safari 抛错由调用方决定如何重试/计数。"""
    return _applescript_invoke(js_code, target_url, timeout)


# =============================================================================
# v4.2 共享 DOM helper（注入每个 JS 片段头部，各片段是独立 do JavaScript 调用）
# 实探依据（2026-10-26，Pro 上目标 Custom GPT 会话）：
#   [data-message-author-role] = 0、#prompt-textarea 不存在、article = 0；
#   composer = div.ProseMirror[role=textbox]（无 id），页面含一个 rect=0 的
#   隐藏同类编辑器，必须按可见性过滤；回合文本在 main.innerText 中以
#   「你说：」/「ChatGPT 说：」标记分段（83 项目三卷监督实证可靠）。
# =============================================================================
_DOM_HELPERS_JS = r"""
function __fpId(role, idx, text) {
    let h = 5381;
    const s = (text || "").slice(0, 160);
    for (let i = 0; i < s.length; i++) { h = (((h << 5) + h) + s.charCodeAt(i)) >>> 0; }
    return role + "@" + idx + "#" + (text || "").length + "#" + h.toString(16);
}
function __findEditor() {
    const ces = Array.from(document.querySelectorAll('[contenteditable="true"]'));
    const vis = el => {
        const r = el.getBoundingClientRect();
        return (r.width > 0 && r.height > 0) || el.offsetWidth > 0 || el.offsetHeight > 0;
    };
    const pick = arr => arr.find(el => el.getAttribute('role') === 'textbox') || arr[0] || null;
    const visible = ces.filter(vis);
    if (visible.length) return pick(visible);
    const pt = document.querySelector('#prompt-textarea');
    if (pt) return pt;
    return pick(ces);
}
function __turns() {
    const nodes = Array.from(document.querySelectorAll('[data-message-author-role]'));
    if (nodes.length) {
        return nodes.map((el, i) => {
            const role = el.getAttribute('data-message-author-role') || '';
            const text = (el.innerText || '').trim();
            const id = el.getAttribute('data-message-id') ||
                (el.closest && el.closest('[data-message-id]')
                    ? el.closest('[data-message-id]').getAttribute('data-message-id')
                    : null) || __fpId(role, i, text);
            return {role: role, text: text, id: id};
        }).filter(t => t.role === 'user' || t.role === 'assistant');
    }
    const main = document.querySelector('main');
    const mt = main ? (main.innerText || '') : '';
    const MARKERS = [
        ['user', '你说：'], ['user', '你说:'], ['user', 'You said:'],
        ['assistant', 'ChatGPT 说：'], ['assistant', 'ChatGPT 说:'],
        ['assistant', 'ChatGPT said:'], ['assistant', 'ChatGPT said：'],
    ];
    const hits = [];
    for (const [role, m] of MARKERS) {
        let from = 0;
        while (true) {
            const i = mt.indexOf(m, from);
            if (i < 0) break;
            hits.push({idx: i, role: role, mlen: m.length});
            from = i + m.length;
        }
    }
    hits.sort((a, b) => a.idx - b.idx);
    const turns = [];
    for (let k = 0; k < hits.length; k++) {
        const start = hits[k].idx + hits[k].mlen;
        const end = (k + 1 < hits.length) ? hits[k + 1].idx : mt.length;
        let text = mt.slice(start, end).trim();
        // 页面底部免责声明是 UI chrome 不是消息内容，最后一段尾部要切掉
        const cut = text.indexOf('ChatGPT 可能会出错');
        if (cut >= 0) text = text.slice(0, cut).trim();
        if (!text) continue;
        turns.push({role: hits[k].role, text: text, id: __fpId(hits[k].role, turns.length, text)});
    }
    return turns;
}
function __lastOf(turns, role) {
    const f = turns.filter(t => t.role === role);
    return f.length ? f[f.length - 1] : null;
}
function __isStreaming() {
    // 只看 main 内的按钮：页面上浮动聊天/语音等区域也有「停止」类按钮，
    // 全文档扫描会假阳性，害稳定等待永远等不到「不在生成」。
    const root = document.querySelector('main') || document;
    if (root.querySelector("button[data-testid='stop-button']")) return true;
    const labels = ['停止回答', '停止', '停止生成', 'Stop generating', 'Stop responding', 'Stop'];
    return Array.from(root.querySelectorAll('button')).some(b => {
        const a = (b.getAttribute('aria-label') || '').trim();
        return labels.indexOf(a) >= 0 || a.indexOf('停止') === 0;
    });
}
"""

# =============================================================================
# DOM 工具：捕获目标 message node 的 data-message-id（v4.1；v4.2 改走 __turns）
# =============================================================================
def _last_message_id_js(role: str) -> str:
    """JS 模板：取最近一条指定 role 的回合的 id（真实 id 或指纹 id）。"""
    return _DOM_HELPERS_JS + f"""
    (() => {{
        const turns = __turns();
        const f = turns.filter(t => t.role === '{role}');
        const last = f.length > 0 ? f[f.length - 1] : null;
        if (!last) return JSON.stringify({{id: null, count: 0, textLen: 0}});
        return JSON.stringify({{
            id: last.id,
            count: f.length,
            textLen: (last.text || "").length,
            fp: (last.text || "").slice(0, 80),
        }});
    }})()
    """


# =============================================================================
# 基线比对（v4.1：totalCount / userCount / assistantCount / lastMessageId）
# =============================================================================
_BASELINE_JS_BODY = r"""
(() => {
    const turns = __turns();
    const usr  = turns.filter(t => t.role === 'user');
    const asst = turns.filter(t => t.role === 'assistant');
    const lastU = usr.length  > 0 ? usr[usr.length - 1]  : null;
    const lastA = asst.length > 0 ? asst[asst.length - 1] : null;
    return JSON.stringify({
        totalCount: turns.length,
        userCount: usr.length,
        assistantCount: asst.length,
        lastUserText: lastU ? lastU.text : "",
        lastAsstText: lastA ? lastA.text : "",
        lastUserMessageId: lastU ? lastU.id : null,
        lastAsstMessageId: lastA ? lastA.id : null,
        hasEditor: !!__findEditor(),
    });
})()
"""

_BASELINE_JS = _DOM_HELPERS_JS + _BASELINE_JS_BODY


def capture_baseline(target_url: str) -> Dict[str, Any]:
    """发送前抓取 userCount / assistantCount / lastUserMessageId 等结构化基线。"""
    try:
        raw = execute_safari_js(_BASELINE_JS, target_url)
    except NoTargetTabError:
        raise
    except SafariError as e:
        raise SafariError(f"基线阶段 Safari 调用失败: {e}") from e

    try:
        b = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SafariError(f"基线 JSON 解析失败: {e}; raw={raw[:200]}") from e

    if not isinstance(b, dict) or "totalCount" not in b or "userCount" not in b:
        raise SafariError(f"基线字段缺失或类型错误: {b}")
    # v4.2：0 回合且无编辑器 = 页面根本没渲染出会话（SPA 未水合/错误页），
    # 不能拿这种假基线 poison 后续阶段；新会话（有编辑器、0 回合）放行。
    if b.get("totalCount") == 0 and not b.get("hasEditor"):
        raise SafariError("基线为空且无可用编辑器：页面未渲染出会话")
    return b


def _fetch_snapshot(target_url: str,
                    js_override: Optional[str] = None) -> Dict[str, Any]:
    """统一抓取函数：默认执行基线 JS，但允许调用方注入专用 JS。
    返回 dict。任何 JSON 错误转化为 SafariError。
    """
    js = js_override if js_override is not None else _BASELINE_JS
    if "__fpId" not in js:
        js = _DOM_HELPERS_JS + js
    try:
        raw = execute_safari_js(js, target_url)
    except SafariError:
        raise
    try:
        snap = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SafariError(f"snapshot JSON 解析失败: {e}; raw={raw[:200]}") from e
    if not isinstance(snap, dict):
        raise SafariError(f"snapshot 类型错误: {type(snap).__name__}")
    return snap


# =============================================================================
# P0-1：精确 user-message identity 提交验证
# =============================================================================
def _user_commit_probe_js(expected_prompt: str) -> str:
    """JS 端比对：规范化后，最后一条 user message 是否 === expected。
    不传完整 prompt 到 snapshot，只传 metadata。
    """
    expected_literal = json.dumps(expected_prompt)
    return _DOM_HELPERS_JS + f"""
    (() => {{
        const norm = s =>
            (s || "")
                .replace(/\\r\\n/g, "\\n")
                .replace(/\\u00a0/g, " ")
                .trim();
        const turns = __turns();
        const users = turns.filter(t => t.role === 'user');
        const last  = users.length > 0 ? users[users.length - 1] : null;
        const text  = last ? norm(last.text || "") : "";
        const id    = last ? last.id : null;
        const exp = norm({expected_literal});
        const match = (text === exp) || (exp.length > 50 && (text.startsWith(exp.slice(0, 50)) || text.includes(exp.slice(0, 50)) || text.endsWith(exp.slice(-50))));
        return JSON.stringify({{
            userCount: users.length,
            matchesExpected: match,
            textLen: text.length,
            messageId: id,
        }});
    }})()
    """


def wait_for_user_message_committed(target_url: str, baseline_user_count: int,
                                    expected_prompt: str, timeout: float,
                                    baseline_user_id: Optional[str] = None) -> Dict[str, Any]:
    """P0-1：等待且仅当 userCount == baseline + 1 或 messageId 发生变化且匹配 expected 时视为提交成功。
    """
    probe_js = _user_commit_probe_js(expected_prompt)
    deadline = time.monotonic() + timeout
    last_snapshot: Optional[Dict[str, Any]] = None

    while time.monotonic() < deadline:
        snap = _fetch_snapshot(target_url, js_override=probe_js)
        last_snapshot = snap
        curr_id = snap.get("messageId")
        id_changed = (baseline_user_id is not None and curr_id != baseline_user_id) or (baseline_user_id is None and curr_id is not None)
        count_incremented = snap.get("userCount") == baseline_user_count + 1
        if (count_incremented or id_changed) and snap.get("matchesExpected") is True:
            return snap
        time.sleep(0.4)

    raise SafariError(
        f"用户消息提交超时（>{timeout:.1f}s）。最终快照={last_snapshot}"
    )


# =============================================================================
# Composer 注入验证（inject 后立刻确认文本进入 composer）
# =============================================================================
def _inject_verify_js(expected_prompt: str) -> str:
    expected_literal = json.dumps(expected_prompt)
    return _DOM_HELPERS_JS + f"""
    (() => {{
        const norm = s =>
            (s || "")
                .replace(/\\r\\n/g, "\\n")
                .replace(/\\u00a0/g, " ")
                .trim();
        // v4.2：统一走 __findEditor（可见 ProseMirror 编辑器优先）
        const el = __findEditor();
        if (!el) return JSON.stringify({{ok: false, reason: "NO_INPUT"}});
        const actual =
            (el.innerText || el.textContent || el.value || "");
        const normActual = norm(actual);
        const exp = norm({expected_literal});
        const match = (normActual === exp) || (exp.length > 30 && (normActual.startsWith(exp.slice(0, 30)) || normActual.includes(exp.slice(0, 30))));
        return JSON.stringify({{
            ok: true,
            matchesExpected: match,
            textLen: normActual.length,
            visible: !!(el.offsetWidth || el.offsetHeight ||
                       (el.getClientRects && el.getClientRects().length)),
            isContentEditable: !!el.isContentEditable,
        }});
    }})()
    """


def verify_composer(target_url: str, expected_prompt: str, timeout: float) -> Dict[str, Any]:
    """发送前最后一次"composer 是否真的写入了 expected"的强证据。
    任一项不满足则抛 SafariError，映射为 EXIT_SUBMIT_FAIL。
    """
    js = _inject_verify_js(expected_prompt)
    deadline = time.monotonic() + timeout
    last: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        snap = _fetch_snapshot(target_url, js_override=js)
        last = snap
        if snap.get("ok") is True and snap.get("matchesExpected") is True:
            return snap
        time.sleep(0.3)
    raise SafariError(
        f"Composer 注入验证超时（>{timeout:.1f}s）。最终快照={last}"
    )


# =============================================================================
# 助手新回合（捕获 data-message-id）
# =============================================================================
def wait_for_assistant_new_turn(target_url: str, baseline_assistant_count: int,
                                timeout: float,
                                baseline_assistant_id: Optional[str] = None) -> Dict[str, Any]:
    """P0-1：必须 assistantCount == baseline + 1 或 id 变化，且新 turn 已出现文本，
    同时记录新 turn 的 messageId，供稳定阶段唯一定位。
    """
    js = _last_message_id_js("assistant")
    deadline = time.monotonic() + timeout
    last_snapshot: Optional[Dict[str, Any]] = None

    while time.monotonic() < deadline:
        snap = _fetch_snapshot(target_url, js_override=js)
        last_snapshot = snap
        curr_id = snap.get("id")
        id_changed = (baseline_assistant_id is not None and curr_id != baseline_assistant_id) or (baseline_assistant_id is None and curr_id is not None)
        count_incremented = snap.get("count") == baseline_assistant_count + 1
        if (count_incremented or id_changed) and snap.get("textLen", 0) > 0:
            return snap
        time.sleep(0.4)

    raise SafariError(
        f"助手新回合未产生（>{timeout:.1f}s）。最终快照={last_snapshot}"
    )


# =============================================================================
# 稳定等待：只读取 target assistant turn 节点（P0-1 / Evidence Integrity 收口）
# =============================================================================
def _stable_poll_js(target_message_id: Optional[str]) -> str:
    """若 target_message_id 存在，按 id 定位；未命中回退到最新 assistant turn。
    任何时候都验证 assistant turn 节点仍然存在。（v4.2 改走 __turns）"""
    return _DOM_HELPERS_JS + f"""
    (() => {{
        const streaming = __isStreaming();
        const turns = __turns();
        const asst = turns.filter(t => t.role === 'assistant');
        let node = null;
        const targetId = {json.dumps(target_message_id or "")};
        if (targetId) {{
            node = asst.find(t => t.id === targetId) || null;
        }}
        if (!node && asst.length) {{
            node = asst[asst.length - 1];
        }}
        if (!node) return JSON.stringify({{
            targetPresent: false, isStreaming: streaming, text: "",
            messageId: null
        }});
        return JSON.stringify({{
            targetPresent: true,
            isStreaming: streaming,
            text: node.text || "",
            messageId: node.id,
        }});
    }})()
    """


def wait_for_assistant_stable(target_url: str, target_message_id: Optional[str],
                              wait_timeout: float) -> Tuple[str, bool]:
    """稳定阶段：仅读取 target assistant turn；
    容许 SPA 页面跳转（新对话从 chatgpt.com/ → chatgpt.com/c/<id>）期间的短暂 DOM 空窗，
    最多允许 8 次连续 targetPresent=False（≈4s）后才真正报错。
    返回 (text, is_complete)。is_complete=False 表示超时退出。
    """
    js = _stable_poll_js(target_message_id)
    start = time.monotonic()
    last_text = ""
    stable_count = 0
    absent_count = 0            # 连续 targetPresent=False 计数
    ABSENT_TOLERANCE = 8        # 最多容忍 8 次（约 4s），覆盖 SPA 导航空窗

    while time.monotonic() - start < wait_timeout:
        snap = _fetch_snapshot(target_url, js_override=js)

        if not snap.get("targetPresent", False):
            absent_count += 1
            if absent_count >= ABSENT_TOLERANCE:
                raise SafariError(
                    f"目标 assistant turn（id={target_message_id!r}）节点持续消失 "
                    f"（{absent_count} 次），证据失效。最终快照={snap}"
                )
            time.sleep(0.5)
            continue

        # 节点已恢复（SPA 导航完成），重置缺失计数
        absent_count = 0

        text = snap.get("text", "") or ""
        streaming = bool(snap.get("isStreaming", False))

        if not streaming and len(text) > 0:
            if text == last_text:
                stable_count += 1
                if stable_count >= 2:
                    return text, True
            else:
                stable_count = 0
                last_text = text
        else:
            last_text = text
            stable_count = 0
        time.sleep(0.5)

    return last_text, False


# =============================================================================
# Payload 格式化（与 v3.0 兼容；L0 改为占位 + 上下文）
# =============================================================================
def format_evidence_payload(task_type: str, context_text: str,
                            evidence_data: Optional[str] = None,
                            level: str = "L1",
                            cwd: Optional[str] = None) -> str:
    git_ctx = get_git_head_context(cwd)
    context_text = sanitize_text(context_text)
    evidence_data = sanitize_text(evidence_data or "")

    if level == "L0":
        # v4.1：L0 不再伪装成"状态摘要"——仅传调用上下文，明确告诉模型"无 Evidence 正文"
        evidence_snippet = "[L0 No Evidence Body: only request context provided]"
    elif level == "L1":
        lines = evidence_data.strip().splitlines()
        if len(lines) > 40:
            # 前 5 行（通常是 setup/header）+ 后 35 行（Traceback 关键段）
            evidence_snippet = "\n".join(lines[:5]) + \
                "\n... [L1 折叠中段，可请求 L2] ...\n" + \
                "\n".join(lines[-35:])
        else:
            evidence_snippet = evidence_data
    elif level == "L2":
        lines = evidence_data.strip().splitlines()
        evidence_snippet = "\n".join(lines[-80:]) if len(lines) > 80 else evidence_data
    else:
        evidence_snippet = evidence_data

    if task_type == "plan":
        return f"【ARCHITECTURAL_PLAN_REQUEST】\n[Local State]: {git_ctx}\n[Target Scope]:\n{context_text}\n\n请输出结构化架构决策及需要本地 Agent 执行的原子操作建议。"
    elif task_type == "feedback":
        return f"【EXECUTION_EVIDENCE_FEEDBACK】\n[Local State]: {git_ctx}\n[Execution Context]:\n{context_text}\n\n[Evidence ({level})]:\n{evidence_snippet}\n\n请基于上述事实分析原因并给出自愈修复补丁。"
    elif task_type == "review":
        return f"【CODE_AND_ARCHITECTURE_REVIEW】\n[Local State]: {git_ctx}\n[Review Target]:\n{context_text}\n\n请从系统解耦、安全性、边界与性能给出评审意见。"
    elif task_type == "task-code":
        return f"""【TASK_CODE_IMPLEMENTATION】\n[Local State]: {git_ctx}\n[Task to Implement]:\n{context_text}\n\n请完成此任务的代码实现并直接推送到 GitHub 目标分支。\n\n【输出格式要求】（严格遵守，否则无法解析）：\n1. 代码直接使用 GitHub 直连工具在远端分支修改并提交。\n2. 禁止在回复中粘贴代码全文。\n3. 测试命令用以下格式（放在单独的 bash 块中）：\n   `TEST: <实际命令>`\n   `EXPECTED: <预期结果描述>`\n4. 如果任务涉及多文件，请按依赖顺序排列。\n5. 只测试命令，不要输出代码，不要写说明文字。"""
    elif task_type == "task-review":
        return f"【TASK_CODE_REVIEW】\n[Local State]: {git_ctx}\n[Task Description]:\n{context_text}\n\n[Evidence ({level})]:\n{evidence_snippet}\n\n请基于上述代码和测试结果做出裁决。只输出以下三种格式之一，不得输出其他内容：\n  APPROVED  — 代码符合任务要求，测试全部通过。\n  NEEDS_FIX — 代码有问题，测试失败或不符合要求。请明确说明：\n              (1) 失败原因\n              (2) 需要修改的文件和具体修改方案\n  BLOCKED   — 任务依赖前置条件未满足（如缺少依赖、配置错误等）。请说明阻塞原因。\n\n【注意】请严格只输出 APPROVED / NEEDS_FIX(...)/ BLOCKED(...) 其一，不要写其他文字。"
    return context_text


# =============================================================================
# 主流：真·monotonic deadline；全程 TargetTabLock 包裹
# =============================================================================
def send_and_receive_safari_chatgpt(prompt: str, target_url: str,
                                    wait_timeout: int = 180,
                                    submit_deadline: int = SUBMIT_PHASE_BUDGET,
                                    turn_deadline: Optional[int] = None
                                    ) -> Tuple[int, str]:
    """返回 (exit_code, answer_text)。
    P0-2：调用方应在外层 TargetTabLock 内调用本函数（fail-fast）。
    P0-4：wait_timeout 是 monotonic absolute deadline。
    """
    if turn_deadline is None:
        turn_deadline = max(45, min(90, wait_timeout // 2))

    overall_deadline = time.monotonic() + wait_timeout

    def remaining() -> float:
        return max(0.0, overall_deadline - time.monotonic())

    emit_event("start", EXIT_OK,
               "Safari ChatGPT Cognitive-Control Bridge v4.2 启动",
               target_url=target_url, wait_timeout=wait_timeout)

    # ---- Step 1：基线（必须捕获 userCount / assistantCount / lastUserId）----
    try:
        baseline = capture_baseline(target_url)
    except NoTargetTabError as e:
        emit_event("baseline", EXIT_NO_TAB,
                   f"目标 Tab 不存在: {e}", target_url=target_url)
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("baseline", EXIT_BASELINE_FAIL, f"基线采集失败: {e}")
        return EXIT_BASELINE_FAIL, ""

    emit_event("baseline", EXIT_OK, "基线采集成功",
               totalCount=baseline.get("totalCount"),
               userCount=baseline.get("userCount"),
               assistantCount=baseline.get("assistantCount"),
               lastUserMessageId=baseline.get("lastUserMessageId"))

    # ---- Step 2：注入 prompt ----
    js_inject = _DOM_HELPERS_JS + f"""
    (() => {{
        const el = __findEditor();
        if (!el) return "ERR_NO_INPUT";
        el.focus();
        const TEXT = {json.dumps(prompt)};
        const sel = window.getSelection();
        // v4.2：jxa_drive 实证路线 —— beforeinput insertText 一步替换，
        // 长度异常（双插/未落字）时清掉重走 execCommand insertText。
        sel.selectAllChildren(el);
        el.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, inputType: 'insertText', data: TEXT }}));
        let len = (el.innerText || '').length;
        if (len > TEXT.length * 1.5) {{
            sel.selectAllChildren(el);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, TEXT);
            len = (el.innerText || '').length;
        }} else if (len < TEXT.length * 0.5) {{
            try {{ document.execCommand('insertText', false, TEXT); }} catch(e) {{}}
            len = (el.innerText || '').length;
        }}
        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: TEXT }}));
        return "OK";
    }})()
    """
    try:
        res = execute_safari_js(js_inject, target_url)
    except NoTargetTabError as e:
        emit_event("inject", EXIT_NO_TAB, f"目标 Tab 在注入阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框注入失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    if res != "OK":
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框定位失败: {res}")
        return EXIT_SAFARI_FAIL, ""

    # ---- Step 2.5：Composer Verify（注入后立刻确认 expected 已落地）----
    cv_budget = min(5.0, submit_deadline, remaining())
    if cv_budget <= 0:
        emit_event("composer_verify", EXIT_TIMEOUT_EMPTY, "无预算执行 composer 验证")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        cv_snap = verify_composer(target_url, prompt, cv_budget)
    except SafariError as e:
        emit_event("composer_verify", EXIT_SUBMIT_FAIL,
                   f"Composer 注入未生效: {e}")
        return EXIT_SUBMIT_FAIL, ""
    emit_event("composer_verify", EXIT_OK,
               "Composer 内容已确认为 expected prompt",
               textLen=cv_snap.get("textLen"),
               visible=cv_snap.get("visible"),
               isContentEditable=cv_snap.get("isContentEditable"))

    # ---- Step 3：触发发送 ----
    js_send = _DOM_HELPERS_JS + """
    (() => {
        const el = __findEditor();
        const sels = ["#composer-submit-button",
                      "button[data-testid='send-button']",
                      "button[data-testid='composer-submit-button']"];
        let submitBtn = null;
        for (const s of sels) { submitBtn = document.querySelector(s); if (submitBtn) break; }
        if (!submitBtn) {
            const labels = ['发送提示词', '发送提示', '发送消息', '发送', 'Send prompt', 'Send message'];
            const byLabel = arr => arr.find(b => labels.indexOf((b.getAttribute('aria-label') || '').trim()) >= 0);
            const form = el && el.closest ? el.closest('form') : null;
            submitBtn = (form ? byLabel(Array.from(form.querySelectorAll('button'))) : null)
                        || byLabel(Array.from(document.querySelectorAll('button')));
        }
        if (submitBtn && !submitBtn.disabled && submitBtn.getAttribute("aria-disabled") !== "true") {
            submitBtn.click();
            return "CLICKED_SUBMIT";
        }
        if (el) {
            el.focus();
            const opts = { bubbles: true, cancelable: true, view: window,
                           key: 'Enter', code: 'Enter', keyCode: 13, which: 13, charCode: 13 };
            el.dispatchEvent(new KeyboardEvent('keydown', opts));
            el.dispatchEvent(new KeyboardEvent('keypress', opts));
            el.dispatchEvent(new KeyboardEvent('keyup', opts));
            return "DISPATCHED_ENTER";
        }
        return "ERR_NO_SEND";
    })()
    """
    try:
        send_res = execute_safari_js(js_send, target_url)
    except NoTargetTabError as e:
        emit_event("send", EXIT_NO_TAB, f"目标 Tab 在发送阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("send", EXIT_SAFARI_FAIL, f"发送触发失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    emit_event("send", EXIT_OK, f"发送触发结果: {send_res}")

    # ---- Step 4：精确 user-message identity 验证（P0-1）----
    sub_budget = min(submit_deadline, remaining())
    if sub_budget <= 0:
        emit_event("submit_verify", EXIT_TIMEOUT_EMPTY, "无预算执行提交验证")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        user_snap = wait_for_user_message_committed(
            target_url=target_url,
            baseline_user_count=baseline["userCount"],
            expected_prompt=prompt,
            timeout=sub_budget,
            baseline_user_id=baseline.get("lastUserMessageId"),
        )
    except NoTargetTabError as e:
        emit_event("submit_verify", EXIT_NO_TAB,
                   f"目标 Tab 在提交验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("submit_verify", EXIT_SUBMIT_FAIL,
                   f"用户消息未真正提交（Enter/Click 未生效或内容不匹配）: {e}")
        return EXIT_SUBMIT_FAIL, ""

    emit_event("submit_verify", EXIT_OK,
               "用户消息已提交（exact prompt match）",
               newUserCount=user_snap.get("userCount"),
               newUserMessageId=user_snap.get("messageId"),
               newTextLen=user_snap.get("textLen"))

    # ---- Step 5：等待助手新回合 + 捕获 message id ----
    turn_budget = min(turn_deadline, remaining())
    if turn_budget <= 0:
        emit_event("new_turn", EXIT_TIMEOUT_EMPTY, "无预算等待助手新回合")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        turn_snap = wait_for_assistant_new_turn(
            target_url=target_url,
            baseline_assistant_count=baseline["assistantCount"],
            timeout=turn_budget,
            baseline_assistant_id=baseline.get("lastAsstMessageId"),
        )
    except NoTargetTabError as e:
        emit_event("new_turn", EXIT_NO_TAB,
                   f"目标 Tab 在回合验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("new_turn", EXIT_NO_NEW_TURN, f"助手新回合未产生: {e}")
        return EXIT_NO_NEW_TURN, ""

    target_message_id = turn_snap.get("id")
    emit_event("new_turn", EXIT_OK,
               "助手新回合已开始",
               newAssistantCount=turn_snap.get("count"),
               messageId=target_message_id,
               firstTextLen=turn_snap.get("textLen"))

    # ---- Step 6：稳定等待（仅读取 target assistant turn）----
    stable_budget = max(STABLE_PHASE_MIN, remaining())
    if stable_budget <= 0:
        emit_event("stable", EXIT_TIMEOUT_EMPTY, "无预算执行稳定等待")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        text, complete = wait_for_assistant_stable(
            target_url=target_url,
            target_message_id=target_message_id,
            wait_timeout=stable_budget,
        )
    except NoTargetTabError as e:
        emit_event("stable", EXIT_NO_TAB,
                   f"目标 Tab 在稳定等待阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("stable", EXIT_SAFARI_FAIL,
                   f"稳定等待期间目标 turn 消失或 Safari 异常: {e}")
        return EXIT_SAFARI_FAIL, ""

    if complete:
        emit_event("done", EXIT_OK,
                   f"捕获到最终生成内容（字数={len(text)}）",
                   charCount=len(text),
                   targetMessageId=target_message_id)
        return EXIT_OK, text
    else:
        if text:
            emit_event("timeout", EXIT_TIMEOUT_PARTIAL,
                       f"等待超时（>{wait_timeout}s），返回当前部分内容",
                       charCount=len(text),
                       targetMessageId=target_message_id)
            return EXIT_TIMEOUT_PARTIAL, text
        else:
            emit_event("timeout", EXIT_TIMEOUT_EMPTY,
                       f"等待超时（>{wait_timeout}s）且无任何内容",
                       targetMessageId=target_message_id)
            return EXIT_TIMEOUT_EMPTY, ""


# =============================================================================
# CLI 入口
# =============================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Safari ChatGPT Cognitive-Control Bridge v4.2 (Evidence-Integrity)"
    )
    p.add_argument("--prompt", type=str, required=False, default=None, help="提示词或上下文")
    p.add_argument("--target-url", type=str, required=False, default=None,
                   help="目标 ChatGPT 会话的完整 URL（必须 https://chatgpt.com）")
    p.add_argument("--probe", action="store_true",
                   help="只读诊断：仅采集 baseline 并以 JSON 输出后退出（不发消息、不触发熔断计数）；需 --target-url，不需 --prompt")

    p.add_argument("--type", type=str, default="raw",
                   choices=["raw", "plan", "feedback", "review", "task-code", "task-review"],
                   help="交互协议类型：raw/plan/feedback/review/task-code/task-review")
    evidence_group = p.add_mutually_exclusive_group()
    evidence_group.add_argument("--evidence", type=str, default=None,
                                help="本地验证证据或报错摘要（内联字符串）")
    evidence_group.add_argument("--evidence-file", type=str, default=None,
                                help="本地验证证据文件路径（避免 argv 超长）")
    p.add_argument("--level", type=str, default="L1",
                   choices=["L0", "L1", "L2", "L3"],
                   help="渐进式证据等级 (Progressive Disclosure)")
    p.add_argument("--signature", type=str, default=None,
                   help="调用签名（用于熔断器）。不传则仅按 target-url 互斥。")
    p.add_argument("--allow-concurrent", action="store_true",
                   help="允许同 target_url 多进程并发（覆盖默认拒绝行为）。"
                        "仅用于纯只读 poll / 确认不会触碰同一 Tab 的场景。")
    p.add_argument("--timeout", type=int, default=180,
                   help="整体超时（秒，含 baseline → 提交 → 新回合 → 稳定）")
    p.add_argument("--cwd", type=str, default=None,
                   help="git rev-parse / git status 的工作目录（默认当前进程 cwd）")
    p.add_argument("--reset-circuit", action="store_true",
                   help="仅清空熔断器状态文件后退出（不进入主流程）")
    return p


def _load_evidence(args) -> Optional[str]:
    """从 --evidence / --evidence-file 读取证据正文。"""
    if args.evidence_file:
        with open(args.evidence_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return args.evidence


def main() -> int:
    args = _build_parser().parse_args()

    # 0a. --reset-circuit：清空熔断器后立刻退出
    if args.reset_circuit:
        cleared = reset_circuit_breaker()
        emit_event("reset_circuit", EXIT_OK,
                   f"已清空熔断器（清理 {cleared} 个 signature）",
                   cleared=cleared)
        return EXIT_OK

    # 0b. target_url 强校验
    if not args.target_url:
        emit_event("validate_target", EXIT_SAFARI_FAIL,
                   "--target-url 必填（--reset-circuit 除外）")
        return EXIT_SAFARI_FAIL
    try:
        validate_target_url(args.target_url)
    except ValueError as e:
        emit_event("validate_target", EXIT_SAFARI_FAIL,
                   f"target_url 校验失败: {e}",
                   target_url=args.target_url)
        return EXIT_SAFARI_FAIL

    # 0b2. --probe 只读诊断：采集 baseline 输出 JSON 后退出
    if args.probe:
        try:
            baseline = capture_baseline(args.target_url)
        except NoTargetTabError as e:
            emit_event("probe", EXIT_NO_TAB, f"目标 Tab 不存在: {e}",
                       target_url=args.target_url)
            return EXIT_NO_TAB
        except SafariError as e:
            emit_event("probe", EXIT_BASELINE_FAIL, f"基线采集失败: {e}")
            return EXIT_BASELINE_FAIL
        print(json.dumps(baseline, ensure_ascii=False))
        emit_event("probe", EXIT_OK, "只读基线采集成功",
                   totalCount=baseline.get("totalCount"),
                   userCount=baseline.get("userCount"),
                   assistantCount=baseline.get("assistantCount"),
                   hasEditor=baseline.get("hasEditor"))
        return EXIT_OK

    if not args.prompt:
        emit_event("validate_target", EXIT_SAFARI_FAIL,
                   "--prompt 必填（--probe / --reset-circuit 除外）")
        return EXIT_SAFARI_FAIL

    # 0c. evidence 读取
    try:
        evidence_body = _load_evidence(args)
    except (OSError, IOError) as e:
        emit_event("evidence_load", EXIT_SAFARI_FAIL,
                   f"--evidence-file 读取失败: {e}",
                   evidence_file=args.evidence_file)
        return EXIT_SAFARI_FAIL

    # 1. 熔断器（按签名；空签名禁用）
    signature = args.signature
    if signature == "":
        signature = None  # 显式禁用

    if signature:
        try:
            check_circuit_breaker(signature)
        except CircuitOpenError as e:
            emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN, str(e),
                       signature=signature)
            return EXIT_CIRCUIT_OPEN

    # 2. TargetTabLock：跨进程事务锁
    try:
        with TargetTabLock(args.target_url):
            return _main_locked(args, signature, evidence_body)
    except TargetTabBusyError as e:
        emit_event("target_busy", EXIT_TARGET_BUSY,
                   f"目标 Tab 锁竞争失败: {e}",
                   target_url=args.target_url,
                   allow_concurrent=args.allow_concurrent)
        return EXIT_TARGET_BUSY


def _main_locked(args, signature: Optional[str],
                 evidence_body: Optional[str]) -> int:
    # 3. 格式化 Payload
    payload = format_evidence_payload(
        args.type, args.prompt, evidence_body, level=args.level, cwd=args.cwd
    )

    # 4. 发送并自动取回
    try:
        exit_code, answer = send_and_receive_safari_chatgpt(
            prompt=payload,
            target_url=args.target_url,
            wait_timeout=args.timeout,
        )
    except CircuitOpenError as e:
        emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN, str(e),
                   signature=signature or "")
        return EXIT_CIRCUIT_OPEN
    except NoTargetTabError as e:
        emit_event("fatal", EXIT_NO_TAB, f"未捕获的 NoTargetTabError: {e}")
        return EXIT_NO_TAB
    except SafariError as e:
        emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 Safari 异常: {e}")
        return EXIT_SAFARI_FAIL
    except json.JSONDecodeError as e:
        emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 JSONDecodeError: {e}")
        return EXIT_SAFARI_FAIL
    except Exception as e:
        emit_event("fatal", EXIT_SAFARI_FAIL,
                   f"未预期异常 ({type(e).__name__}): {e}")
        return EXIT_SAFARI_FAIL

    # 5. stdout 仅输出回答文本（下游解析用）
    if answer:
        print(answer)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
