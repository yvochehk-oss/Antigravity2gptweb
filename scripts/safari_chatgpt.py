#!/usr/bin/env python3
"""
Safari ChatGPT Cognitive-Control Bridge (v4.0 Evidence-Integrity)
-----------------------------------------------------------------
在 v3.0 基础上对证据完整性做了三项关键改造：

P0-1.【Tab 精确绑定】：禁止"首个含 chatgpt.com 的 Tab"启发式。
     调用方必须通过 --target-url 显式指定目标 Tab，URL 完全匹配。
P0-2.【基线比对 + 验证】：发送前记录最后消息指纹，发送后强制验证
     "用户消息 +1 已提交" 且 "助手新回合已产生" 才进入轮询稳定阶段。
     杜绝模拟 Enter 未生效却返回旧回复的伪证风险。
P0-3.【结构化退出码 + json-stdout 错误输出】：
     0  正常完成
     2  超时（拿到部分内容）
     3  超时（无内容）
     4  Safari / AppleScript / JS 失败
     5  基线采集失败
     6  用户消息未真正提交
     7  助手新回合未产生
     10 目标 Tab 不存在
     12 熔断器已开
     正常完成时 stdout 仍输出回答文本；所有错误/阶段事件走 stderr JSON。

P1-A.【osascript 单次 timeout=30】，单次卡死不再永久挂住整个调用。
P1-B.【熔断器时间窗口化 + 原子写 + 文件锁】：
     state 改为 {sig: [count, first_seen_epoch]}，超过 1h 自动重置；
     写入走 tmp + os.replace，加 fcntl.flock(LOCK_EX)。
P2. 【Secret 脱敏扩充】：AWS / Slack / PEM / 数据库连接串 / 通用 *_KEY=
     / JWT (eyJ) 等。邮箱、手机号暂不强制（由数据分类策略决定）。

调用范例见 SKILL.md。
"""

import sys
import os
import re
import time
import json
import errno
import fcntl
import tempfile
import subprocess
import argparse
import hashlib
from typing import Optional, Dict, Any, Tuple

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
EXIT_CONCURRENT      = 13  # 同一 signature 已被另一进程占用（拒绝排队）

EXIT_CODE_NAME = {
    EXIT_OK: "OK",
    EXIT_TIMEOUT_PARTIAL: "TIMEOUT_PARTIAL",
    EXIT_TIMEOUT_EMPTY: "TIMEOUT_EMPTY",
    EXIT_SAFARI_FAIL: "SAFARI_FAIL",
    EXIT_BASELINE_FAIL: "BASELINE_FAIL",
    EXIT_SUBMIT_FAIL: "SUBMIT_FAIL",
    EXIT_NO_NEW_TURN: "NO_NEW_TURN",
    EXIT_NO_TAB: "NO_TAB",
    EXIT_CIRCUIT_OPEN: "CIRCUIT_OPEN",
    EXIT_CONCURRENT: "CONCURRENT",
}

# =============================================================================
# 熔断器状态文件
# =============================================================================
CIRCUIT_STATE_FILE = "/tmp/safari_chatgpt_circuit_breaker.json"
CIRCUIT_WINDOW_SEC = 3600           # 1 小时窗口
CIRCUIT_MAX_RETRIES = 3

# 单次 osascript / Safari JS 调用超时
OSASCRIPT_TIMEOUT_SEC = 30

# Safari 连续失败容忍次数
SAFARI_CONSEC_FAIL_LIMIT = 5


# =============================================================================
# 结构化错误输出（stderr JSON）
# =============================================================================
def emit_event(stage: str, exit_code: int, message: str, **extra) -> None:
    """把阶段事件与错误以结构化 JSON 输出到 stderr，stdout 留给回答文本。"""
    payload = {
        "ts": time.time(),
        "stage": stage,
        "exit_code": exit_code,
        "exit_name": EXIT_CODE_NAME.get(exit_code, "UNKNOWN"),
        "message": message,
    }
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)



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
# Secret 脱敏 (扩充自 v3.0)
# =============================================================================
_GH_TOKEN_RE  = re.compile(r'(gh[pousr]_[A-Za-z0-9_]{16,})')
_OPENAI_RE    = re.compile(r'(sk-[A-Za-z0-9_-]{20,})')
_BEARER_RE    = re.compile(r'(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})', re.IGNORECASE)
_PASSWORD_RE  = re.compile(r'(password\s*[:=]\s*["\']?)([^"\'\s]+)(["\']?)', re.IGNORECASE)
_AWS_RE       = re.compile(r'((?:AKIA|ASIA)[0-9A-Z]{16})')
_SLACK_RE     = re.compile(r'\b(xox[abprs]-[A-Za-z0-9-]{10,})\b')
_PEM_RE       = re.compile(r'-----BEGIN [A-Z ]+PRIVATE KEY-----')
_DBCONN_RE    = re.compile(r'(?P<scheme>(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s"\'<>]+)', re.IGNORECASE)
_GENKEY_RE    = re.compile(r'((?:[A-Z][A-Z0-9_]*_?(?:KEY|SECRET|TOKEN))\s*[:=]\s*["\']?)([A-Za-z0-9._/+-]{16,})(["\']?)')
_JWT_RE       = re.compile(r'(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_\-+/=]{4,})')

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
    text = _OPENAI_RE.sub('[REDACTED_API_KEY]', text)
    text = _BEARER_RE.sub(r'\1[REDACTED_AUTH_TOKEN]', text)
    text = _PASSWORD_RE.sub(r'\1[REDACTED_PASSWORD]\3', text)
    text = _AWS_RE.sub('[REDACTED_AWS_KEY]', text)
    text = _SLACK_RE.sub('[REDACTED_SLACK_TOKEN]', text)
    text = _PEM_RE.sub('[REDACTED_PRIVATE_KEY]', text)
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
# 熔断器（时间窗口 + 原子写 + 文件锁）
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


class _CircuitLock:
    """对 CIRCUIT_STATE_FILE 的进程级排他锁，确保 read-modify-write 原子性。
    macOS 上 fcntl.flock(LOCK_EX) 是文件级 advisory lock，跨进程生效。
    """

    def __init__(self):
        self.fd = None

    def __enter__(self):
        # 阻塞式 LOCK_EX：保证拿到锁后才进入临界区
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
                          normalize: bool = True, max_retries: int = CIRCUIT_MAX_RETRIES) -> bool:
    """时间窗口内同类签名累计超过阈值则抛 CircuitOpenError。
    整个 RMW 在单把 LOCK_EX 内完成，并发安全。
    """
    if not failure_signature:
        return True
    if normalize:
        failure_signature = hashlib.sha256(normalize_failure_signature(failure_signature).encode('utf-8')).hexdigest()[:16]

    now = time.time()
    with _CircuitLock():
        # 读
        state = _read_circuit_state_unlocked()
        entry = state.get(failure_signature)
        if entry and isinstance(entry, list) and len(entry) == 2:
            count, first_seen = entry
            if now - first_seen > CIRCUIT_WINDOW_SEC:
                count = 0
                first_seen = now
        else:
            count, first_seen = 0, now

        # 改
        count += 1
        state[failure_signature] = [count, first_seen]

        # 写
        _atomic_write_state_unlocked(state)

    if count > max_retries:
        emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN,
                   f"熔断器触发：签名 {failure_signature} 在 {CIRCUIT_WINDOW_SEC}s 内累计 {count} 次",
                   signature=failure_signature, count=count)
        raise CircuitOpenError(
            f"同一故障特征在 {CIRCUIT_WINDOW_SEC}s 内已连续出现 {count} 次（>{max_retries}），"
            f"已触发自动熔断。请转由人工排查后重置熔断器。"
        )
    return True


def reset_circuit_breaker() -> None:
    """清空熔断计数器（--new 或人工重置）。"""
    try:
        if os.path.exists(CIRCUIT_STATE_FILE):
            os.remove(CIRCUIT_STATE_FILE)
    except Exception:
        pass


# =============================================================================
# 并发调用互斥（防同 Tab 互相覆写输入）
# =============================================================================
# 锁文件位置：/tmp/safari_chatgpt_invocation_lock_{sig}.lock
# 锁内容：{pid, started_at, target_url, signature, host}
# 算法：
#   1. 用 signature 哈希做锁文件名（避免名字冲突）
#   2. LOCK_EX | LOCK_NB 非阻塞拿锁
#   3. 拿不到时读锁内 JSON 看 holder pid 是否还活着
#      - 活着 → raise ConcurrentInvocationError（拒绝排队）
#      - 已死 → 接管锁（OS 级别 LOCK_EX 已被释放；写入新 holder JSON）
#   4. 释放：LOCK_UN + 关 fd；进程崩溃由 OS 自动释放

def _invocation_lock_path(signature: str) -> str:
    sig_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/safari_chatgpt_invocation_lock_{sig_hash}.lock"


def _pid_alive(pid: int) -> bool:
    """检查 pid 是否仍在运行。Send signal 0 不真的发信号，只判断进程是否存在。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但属于另一用户（理论上不会发生）
        return True
    except OSError:
        return False


class SafariInvocationLock:
    """签名级并发互斥。默认拒绝排队（fail-fast），需要排队可用 allow_concurrent=True。
    使用：
        with SafariInvocationLock(signature, target_url) as held:
            ...safari operations...
        # 释放
    """

    def __init__(self, signature: str, target_url: str,
                 allow_concurrent: bool = False):
        self.signature = signature
        self.target_url = target_url
        self.allow_concurrent = allow_concurrent
        self.path = _invocation_lock_path(signature)
        self.fd = None
        self.acquired = False

    def __enter__(self):
        if not self.signature:
            # 没有 signature 时不持锁；允许全机单实例的情况由调用方决定
            return self

        if self.allow_concurrent:
            # 用户显式 opt-in 并发，仅记录并发审计信息，不加 LOCK_EX
            return self

        # 1. 先读现有锁内 holder（不阻塞，只读一次）
        holder_info = self._peek_holder()
        if holder_info:
            holder_pid = holder_info.get("pid", -1)
            if _pid_alive(holder_pid):
                # 进程仍活着，且并发未允许 → 拒绝
                raise ConcurrentInvocationError(
                    f"signature '{self.signature}' 正在被 PID={holder_pid} 占用"
                    f"（started_at={holder_info.get('started_at')}）。"
                    f"若确认对端已死请清理锁文件：{self.path}"
                )
            # else: holder 已死，下面接管

        # 2. 非阻塞 LOCK_EX
        try:
            self.fd = os.open(self.path,
                              os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            # 另一进程在我们 peek 之后抢先拿到了锁
            if self.fd is not None:
                try: os.close(self.fd)
                except: pass
            raise ConcurrentInvocationError(
                f"signature '{self.signature}' 锁竞争失败（{e}）"
            )

        # 3. 写入 holder JSON
        info = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "target_url": self.target_url,
            "signature": self.signature,
            "host": os.uname().nodename,
        }
        try:
            os.ftruncate(self.fd, 0)
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, json.dumps(info).encode("utf-8"))
        except Exception:
            pass  # holder JSON 写入失败不阻断持有；只是审计信息缺失

        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.acquired or self.fd is None:
            return False
        try:
            # 先释放 flock；否则 truncate/lseek 在持锁状态下对其他进程不可见
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            # 清空 holder JSON，避免后续 _peek_holder 误以为前持有者还活着
            # 关键修复：否则同进程再次 acquire 会因为 os.kill(self_pid, 0)==True 而 self-deadlock
            try:
                os.ftruncate(self.fd, 0)
            except Exception:
                pass
        finally:
            try: os.close(self.fd)
            except: pass
            # 删除锁文件。允许进程崩溃路径下次靠 _pid_alive() 复活检测；
            # 正常路径下次 acquire 看到无文件直接走 _peek_holder=None 快路径。
            try: os.remove(self.path)
            except FileNotFoundError: pass
            except Exception: pass
        return False

    def _peek_holder(self) -> Optional[Dict[str, Any]]:
        """读取锁文件 holder JSON（不持锁）。失败返回 None。"""
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return None


# =============================================================================
# Git 上下文（计划过期守卫）
# =============================================================================
def get_git_head_context(cwd: Optional[str] = None) -> str:
    try:
        res = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=cwd
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            status_res = subprocess.run(
                ['git', 'status', '--porcelain'],
                capture_output=True, text=True, cwd=cwd
            )
            dirty = " (dirty)" if status_res.stdout.strip() else " (clean)"
            return f"Git: {sha}{dirty}"
    except Exception:
        pass
    return "Git: untracked"


# =============================================================================
# Safari JS 执行（精确 Tab 绑定 + 单次超时 + 失败计数）
# =============================================================================
class SafariError(RuntimeError):
    pass


class NoTargetTabError(SafariError):
    """目标 Tab 不存在（URL 精确匹配失败）。映射为 EXIT_NO_TAB。"""
    pass


class ConcurrentInvocationError(SafariError):
    """同 signature 已被另一进程占用。映射为 EXIT_CONCURRENT。
    默认行为是拒绝排队（fail-fast），避免两个进程同 Tab 互相覆写对方输入。
    """
    pass


class AmbiguousTargetTabError(SafariError):
    """目标 Tab 存在多处匹配（歧义）。映射为 EXIT_AMBIGUOUS_TAB。"""
    pass


def _applescript_invoke(js_code: str, target_url: str, timeout: int) -> str:
    """构造并执行 AppleScript，向精确 URL 匹配的 Tab 注入 JS。
    采用临时文件 + POSIX file 零转义读取，彻底杜绝大 payload 截断与字符串转义崩溃。
    """
    base_url = target_url.rstrip('?#/ ').split('?')[0]
    escaped_base_url = base_url.replace('\\', '\\\\').replace('"', '\\"')
    
    # 提取会话 UUID（支持 Custom GPT URL 与标准 /c/ 路径自适应）
    uuid_match = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', target_url)
    uuid_part = uuid_match.group(0) if uuid_match else base_url

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(js_code)
        tmp_js_path = f.name

    applescript = f'''
    tell application "Safari"
        set targetTab to missing value
        set targetWin to missing value
        set foundReady to false
        repeat with w in windows
            repeat with t in tabs of w
                set u to (URL of t as text)
                if (u starts with "{escaped_base_url}" or u contains "{uuid_part}") then
                    try
                        tell t
                            set isReady to (do JavaScript "!!(document.querySelector('#prompt-textarea') || document.querySelector('div[contenteditable=\\\"true\\\"]'))")
                        end tell
                        if isReady is true or isReady is "true" then
                            set targetTab to t
                            set targetWin to w
                            set foundReady to true
                            exit repeat
                        end if
                    end try
                    if targetTab is missing value then
                        set targetTab to t
                        set targetWin to w
                    end if
                end if
            end repeat
            if foundReady is true then exit repeat
        end repeat

        if targetTab is missing value then
            error "NO_TARGET_TAB"
        end if

        set current tab of targetWin to targetTab
        set jsStr to (read (POSIX file "{tmp_js_path}") as «class utf8»)
        tell targetTab
            return do JavaScript jsStr
        end tell
    end tell
    '''
    try:
        res = subprocess.run(
            ['osascript', '-'],
            input=applescript, capture_output=True, text=True, timeout=timeout
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
        if "NO_TARGET_TAB" in err:
            raise NoTargetTabError(f"未在 Safari 中找到 URL 精确匹配的 Tab: {target_url}")
        raise SafariError(f"AppleScript 执行失败: {err.strip()}")

    return res.stdout.rstrip("\r\n")


def execute_safari_js(js_code: str, target_url: str,
                      timeout: int = OSASCRIPT_TIMEOUT_SEC,
                      _state: Optional[Dict[str, Any]] = None) -> str:
    """执行 JS 并返回原始文本。Safari 抛错由调用方决定如何重试/计数。"""
    return _applescript_invoke(js_code, target_url, timeout)


# =============================================================================
# 基线比对与回合验证
# =============================================================================
_BASELINE_JS = r"""
(() => {
    const msgs = document.querySelectorAll("div[data-message-author-role]");
    const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
    const lastRole = last ? last.getAttribute('data-message-author-role') : null;
    const lastText = last ? last.innerText.trim() : "";
    // 指纹：避免传输大文本，仅保留长度与前后片段
    const fpSource = lastText;
    const fp = fpSource.length + ":" +
        fpSource.slice(0, 64) + "|" +
        fpSource.slice(-64);
    return JSON.stringify({
        count: msgs.length,
        lastRole: lastRole,
        lastTextLen: lastText.length,
        lastFp: fp,
    });
})()
"""


def capture_baseline(target_url: str, safari_state: Dict[str, Any]) -> Dict[str, Any]:
    """发送前抓取最后一条消息快照。
    捕获 SafariError 与 NoTargetTabError，让上层正确映射退出码。
    """
    try:
        raw = execute_safari_js(_BASELINE_JS, target_url, _state=safari_state)
    except NoTargetTabError:
        raise
    except SafariError as e:
        raise SafariError(f"基线阶段 Safari 调用失败: {e}") from e

    try:
        b = json.loads(raw)
    except json.JSONDecodeError as e:
        # JSON 损坏不应让进程以 traceback 终止，转为结构化错误
        raise SafariError(f"基线 JSON 解析失败: {e}; raw={raw[:200]}") from e

    if not isinstance(b, dict) or "count" not in b:
        raise SafariError(f"基线字段缺失或类型错误: {b}")
    return b


def _fetch_snapshot(target_url: str, safari_state: Dict[str, Any],
                    js_override: Optional[str] = None) -> Dict[str, Any]:
    """统一抓取函数：默认执行基线 JS，但允许调用方注入专用 JS（提交验证 / 回合验证 / 稳定等待）。
    返回 dict。任何 JSON 错误转化为 SafariError，不让 Python traceback 外泄。
    """
    js = js_override if js_override is not None else _BASELINE_JS
    try:
        raw = execute_safari_js(js, target_url, _state=safari_state)
    except SafariError:
        # 透传：NoTargetTabError/SafariError 都让上层分类
        raise
    try:
        snap = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SafariError(f"snapshot JSON 解析失败: {e}; raw={raw[:200]}") from e
    if not isinstance(snap, dict):
        raise SafariError(f"snapshot 类型错误: {type(snap).__name__}")
    return snap


def wait_for_user_message_committed(target_url: str, baseline_count: int,
                                    prompt_prefix: str, timeout: int,
                                    safari_state: Dict[str, Any]) -> Dict[str, Any]:
    """等待用户消息真的提交：消息数 >= baseline_count + 1。
    支持快速生成场景（助手已开始或已完成回复），带 2s 自动补发重试机制。
    """
    js = r"""
    (() => {
        const msgs = document.querySelectorAll("div[data-message-author-role]");
        const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
        const role = last ? last.getAttribute('data-message-author-role') : null;
        const text = last ? last.innerText.trim() : "";
        return JSON.stringify({
            count: msgs.length,
            lastRole: role,
            lastTextPrefix: text.slice(0, 80),
            lastTextLen: text.length,
        });
    })()
    """
    js_retry_send = r"""
    (() => {
        const btn = document.querySelector("#composer-submit-button") ||
                    document.querySelector("button[data-testid='send-button']");
        if (btn && btn.getAttribute('aria-disabled') !== 'true' && !btn.disabled) {
            btn.click();
            return "RETRY_CLICK_OK";
        }
        return "NO_BTN";
    })()
    """
    deadline = time.time() + timeout
    last_snapshot = None
    retry_sent = False
    while time.time() < deadline:
        snap = _fetch_snapshot(target_url, safari_state, js_override=js)
        last_snapshot = snap
        # 场景 1：用户消息是最新消息且前缀匹配（无论 count 是 baseline+1 还是就地追加）
        if (snap.get("lastRole") == "user"
                and snap.get("lastTextPrefix", "").startswith(prompt_prefix[:60])):
            return snap
        # 场景 2：助手响应极快，新消息数已 >= baseline_count + 2，或最新已是 assistant
        if (snap.get("count") >= baseline_count + 2
                or (snap.get("count") >= baseline_count + 1 and snap.get("lastRole") == "assistant")):
            return snap

        if not retry_sent and (deadline - time.time()) < (timeout - 2.0):
            try:
                execute_safari_js(js_retry_send, target_url, _state=safari_state)
            except Exception:
                pass
            retry_sent = True

        time.sleep(0.5)
    raise SafariError(
        f"用户消息提交超时（>{timeout}s）。最终快照={last_snapshot}"
    )


def wait_for_assistant_new_turn(target_url: str, baseline_count: int,
                                timeout: int,
                                safari_state: Dict[str, Any]) -> Dict[str, Any]:
    """等待助手新回复开始：总消息数 >= baseline_count + 2（或最后一条是 assistant 且文本 > 0）。
    """
    js = r"""
    (() => {
        const msgs = document.querySelectorAll("div[data-message-author-role]");
        const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
        const role = last ? last.getAttribute('data-message-author-role') : null;
        const text = last ? last.innerText.trim() : "";
        return JSON.stringify({
            count: msgs.length,
            lastRole: role,
            lastTextLen: text.length,
        });
    })()
    """
    deadline = time.time() + timeout
    last_snapshot = None
    while time.time() < deadline:
        snap = _fetch_snapshot(target_url, safari_state, js_override=js)
        last_snapshot = snap
        if ((snap.get("count") >= baseline_count + 2 or (snap.get("count") >= baseline_count + 1 and snap.get("lastRole") == "assistant"))
                and snap.get("lastTextLen", 0) > 0):
            return snap
        time.sleep(0.5)
    raise SafariError(
        f"助手新回合未产生（>{timeout}s）。最终快照={last_snapshot}"
    )


def wait_for_assistant_stable(target_url: str, min_expected_total_msgs: int,
                              wait_timeout: int,
                              safari_state: Dict[str, Any]) -> Tuple[str, bool]:
    """等待助手回复稳定：无 stop 按钮且连续 2 次采样文本一致。
    返回 (text, is_complete)。is_complete=False 表示超时退出。
    """
    js = r"""
    (() => {
        const stopBtn = document.querySelector("button[data-testid='stop-button']") ||
                        document.querySelector("button[aria-label='停止回答']");
        const allMsgs = document.querySelectorAll("div[data-message-author-role]");
        const asstMsgs = document.querySelectorAll("div[data-message-author-role='assistant']");
        const last = asstMsgs.length > 0 ? asstMsgs[asstMsgs.length - 1] : null;
        const text = last ? last.innerText.trim() : "";
        return JSON.stringify({
            totalCount: allMsgs.length,
            asstCount: asstMsgs.length,
            isStreaming: !!stopBtn,
            textLen: text.length,
            text: text,
        });
    })()
    """
    start = time.time()
    last_text = ""
    stable_count = 0
    while time.time() - start < wait_timeout:
        snap = _fetch_snapshot(target_url, safari_state, js_override=js)

        # 校验消息总数未被清空
        if snap.get("totalCount", 0) < min_expected_total_msgs:
            time.sleep(0.5)
            continue

        text = snap.get("text", "")
        streaming = snap.get("isStreaming", False)
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
# Payload 格式化（与 v3.0 兼容）
# =============================================================================
def format_evidence_payload(task_type: str, context_text: str,
                            evidence_data: Optional[str] = None,
                            level: str = "L1",
                            cwd: Optional[str] = None) -> str:
    git_ctx = get_git_head_context(cwd)
    context_text = sanitize_text(context_text)
    evidence_data = sanitize_text(evidence_data or "")

    if level == "L0":
        evidence_snippet = "[L0 Status Summary Only]"
    elif level == "L1":
        lines = evidence_data.strip().splitlines()
        if len(lines) > 40:
            # v4.0：前 5 行（通常是 setup/header）+ 后 35 行（Traceback 关键段）
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
    return context_text


# =============================================================================
# 主流：基线 → 注入 → 验证提交 → 验证新回合 → 稳定等待
# =============================================================================
def send_and_receive_safari_chatgpt(prompt: str, target_url: str,
                                    wait_timeout: int = 180,
                                    new_chat: bool = False,
                                    submit_deadline: int = 20,
                                    turn_deadline: Optional[int] = None) -> Tuple[int, str]:
    """返回 (exit_code, answer_text)。"""
    if turn_deadline is None:
        turn_deadline = min(90, max(45, wait_timeout // 2))
    safari_state = {"consec_fail": 0}
    emit_event("start", EXIT_OK, "Safari ChatGPT Cognitive-Control Bridge v4.0 启动",
               target_url=target_url, wait_timeout=wait_timeout)

    if new_chat:
        emit_event("new_chat", EXIT_OK, "触发新会话并重置熔断器")
        reset_circuit_breaker()
        try:
            execute_safari_js(
                """
                (() => {
                    const link = document.querySelector("a[href='/']");
                    if (link) { link.click(); return "CLICKED_HOME"; }
                    return 'NO_HOME_LINK';
                })()
                """,
                target_url, _state=safari_state,
            )
        except SafariError as e:
            emit_event("new_chat_warn", EXIT_OK, f"新会话点击失败（继续）：{e}")
        time.sleep(1.5)

    # ---- Step 1：基线 ----
    try:
        baseline = capture_baseline(target_url, safari_state)
    except NoTargetTabError as e:
        emit_event("baseline", EXIT_NO_TAB,
                   f"目标 Tab 不存在: {e}", target_url=target_url)
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("baseline", EXIT_BASELINE_FAIL, f"基线采集失败: {e}")
        return EXIT_BASELINE_FAIL, ""
    emit_event("baseline", EXIT_OK, "基线采集成功",
               count=baseline.get("count"),
               lastRole=baseline.get("lastRole"),
               lastFp=baseline.get("lastFp"))

    # ---- Step 2：注入 prompt ----
    js_inject = f"""
    (() => {{
        const el = document.querySelector('#prompt-textarea') ||
                   document.querySelector("div[contenteditable='true']");
        if (!el) return "ERR_NO_INPUT";
        el.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, {json.dumps(prompt)});
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return "OK";
    }})()
    """
    try:
        res = execute_safari_js(js_inject, target_url, _state=safari_state)
    except NoTargetTabError as e:
        emit_event("inject", EXIT_NO_TAB, f"目标 Tab 在注入阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框注入失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    if res != "OK":
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框定位失败: {res}")
        return EXIT_SAFARI_FAIL, ""
    time.sleep(0.6)

    # ---- Step 3：触发发送 ----
    js_send = """
    (() => {
        const submitBtn = document.querySelector("#composer-submit-button") ||
                          document.querySelector("button[data-testid='send-button']") ||
                          document.querySelector("button[aria-label='发送提示']") ||
                          document.querySelector("button[aria-label='Send prompt']");
        if (submitBtn && !submitBtn.disabled) {
            submitBtn.click();
            return "CLICKED_SUBMIT";
        }
        const el = document.querySelector('#prompt-textarea') ||
                   document.querySelector("div[contenteditable='true']");
        if (el) {
            const ke = new KeyboardEvent('keydown', {
                bubbles: true, cancelable: true,
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13
            });
            el.dispatchEvent(ke);
            return "DISPATCHED_ENTER";
        }
        return "ERR_NO_SEND";
    })()
    """
    try:
        send_res = execute_safari_js(js_send, target_url, _state=safari_state)
    except NoTargetTabError as e:
        emit_event("send", EXIT_NO_TAB, f"目标 Tab 在发送阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("send", EXIT_SAFARI_FAIL, f"发送触发失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    emit_event("send", EXIT_OK, f"发送触发结果: {send_res}")

    # ---- Step 4：验证用户消息真的提交 ----
    try:
        user_snap = wait_for_user_message_committed(
            target_url, baseline["count"], prompt, submit_deadline, safari_state
        )
    except NoTargetTabError as e:
        emit_event("submit_verify", EXIT_NO_TAB, f"目标 Tab 在提交验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("submit_verify", EXIT_SUBMIT_FAIL,
                   f"用户消息未真正提交（Enter/Click 未生效）: {e}")
        return EXIT_SUBMIT_FAIL, ""
    emit_event("submit_verify", EXIT_OK, "用户消息已提交",
               newCount=user_snap.get("count"),
               newLastRole=user_snap.get("lastRole"))

    # ---- Step 5：等待助手新回合开始 ----
    try:
        turn_snap = wait_for_assistant_new_turn(
            target_url, baseline["count"], turn_deadline, safari_state
        )
    except NoTargetTabError as e:
        emit_event("new_turn", EXIT_NO_TAB, f"目标 Tab 在回合验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("new_turn", EXIT_NO_NEW_TURN, f"助手新回合未产生: {e}")
        return EXIT_NO_NEW_TURN, ""
    expected_assistant_count = turn_snap["count"]
    emit_event("new_turn", EXIT_OK, "助手新回合已开始",
               assistantCount=expected_assistant_count,
               firstTextLen=turn_snap.get("lastTextLen"))

    # ---- Step 6：等待助手回复稳定 ----
    remaining = max(5, wait_timeout - submit_deadline - turn_deadline)
    try:
        text, complete = wait_for_assistant_stable(
            target_url, expected_assistant_count, remaining, safari_state
        )
    except NoTargetTabError as e:
        emit_event("stable", EXIT_NO_TAB, f"目标 Tab 在稳定等待阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except SafariError as e:
        emit_event("stable", EXIT_SAFARI_FAIL, f"稳定等待期间 Safari 异常: {e}")
        return EXIT_SAFARI_FAIL, ""

    if complete:
        emit_event("done", EXIT_OK,
                   f"捕获到最终生成内容（字数={len(text)}）",
                   charCount=len(text))
        return EXIT_OK, text
    else:
        if text:
            emit_event("timeout", EXIT_TIMEOUT_PARTIAL,
                       f"等待超时（>{wait_timeout}s），返回当前部分内容",
                       charCount=len(text))
            return EXIT_TIMEOUT_PARTIAL, text
        else:
            emit_event("timeout", EXIT_TIMEOUT_EMPTY,
                       f"等待超时（>{wait_timeout}s）且无任何内容")
            return EXIT_TIMEOUT_EMPTY, ""


# =============================================================================
# CLI 入口
# =============================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Safari ChatGPT Cognitive-Control Bridge v4.0 (Evidence-Integrity)"
    )
    p.add_argument("--prompt", type=str, required=True, help="提示词或上下文")
    p.add_argument("--target-url", type=str, required=True,
                   help="目标 ChatGPT 会话的完整 URL（必须精确匹配）")
    p.add_argument("--type", type=str, default="raw",
                   choices=["raw", "plan", "feedback", "review"],
                   help="交互协议类型")
    p.add_argument("--evidence", type=str, default=None,
                   help="本地验证证据或报错摘要")
    p.add_argument("--level", type=str, default="L1",
                   choices=["L0", "L1", "L2", "L3"],
                   help="渐进式证据等级 (Progressive Disclosure)")
    p.add_argument("--signature", type=str, default=None,
                   help="调用签名（同时用于熔断器与并发互斥）。"
                        "不传则仅按 target-url 互斥；传空字符串则禁用所有互斥（不推荐）。")
    p.add_argument("--allow-concurrent", action="store_true",
                   help="允许同一 signature 多进程并发执行（覆盖默认拒绝行为）。"
                        "使用场景：纯只读 poll 或确认不会触碰同一 Tab。")
    p.add_argument("--new", action="store_true", help="是否开辟全新干净会话")
    p.add_argument("--timeout", type=int, default=180, help="总超时时间（秒）")
    p.add_argument("--cwd", type=str, default=None,
                   help="git rev-parse / git status 的工作目录（默认当前进程 cwd）")
    return p


def main() -> int:
    args = _build_parser().parse_args()

    # 0. 并发互斥：在做任何 Safari 动作之前就拒绝排队。
    #    默认 signature = sha256(target_url)[:16] —— 同 URL 自动互斥。
    signature = args.signature
    if signature is None:
        signature = hashlib.sha256(args.target_url.encode("utf-8")).hexdigest()[:16]
    elif signature == "":
        signature = None  # 用户显式禁用签名互斥

    invocation_lock = SafariInvocationLock(
        signature=signature or "",
        target_url=args.target_url,
        allow_concurrent=args.allow_concurrent,
    )
    try:
        with invocation_lock:
            return _main_locked(args, signature)
    except ConcurrentInvocationError as e:
        # 顶层兜底：用 emit_event + 退出码，让下游可结构化处理
        emit_event("concurrent", EXIT_CONCURRENT,
                   f"并发调用拒绝: {e}",
                   signature=signature or "",
                   target_url=args.target_url,
                   allow_concurrent=args.allow_concurrent)
        return EXIT_CONCURRENT


def _main_locked(args, signature: str) -> int:
    # 1. 熔断器（如指定 signature）
    if signature:
        try:
            check_circuit_breaker(signature)
        except CircuitOpenError as e:
            print("", file=sys.stdout)  # stdout 留空，避免污染下游
            return EXIT_CIRCUIT_OPEN

    # 2. 格式化 Payload
    payload = format_evidence_payload(
        args.type, args.prompt, args.evidence, level=args.level, cwd=args.cwd
    )

    # 3. 发送并自动取回
    try:
        exit_code, answer = send_and_receive_safari_chatgpt(
            prompt=payload,
            target_url=args.target_url,
            wait_timeout=args.timeout,
            new_chat=args.new,
        )
    except CircuitOpenError as e:
        emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN, str(e))
        return EXIT_CIRCUIT_OPEN
    except NoTargetTabError as e:
        # 顶层兜底：理论上 send_and_receive_safari_chatgpt 已映射，这里只防意外的调用路径
        emit_event("fatal", EXIT_NO_TAB, f"未捕获的 NoTargetTabError: {e}")
        return EXIT_NO_TAB
    except SafariError as e:
        emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 Safari 异常: {e}")
        return EXIT_SAFARI_FAIL
    except json.JSONDecodeError as e:
        # JSONDecodeError 不应让 Python traceback 终止进程；转化为结构化事件 + SAFARI_FAIL
        emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 JSONDecodeError: {e}")
        return EXIT_SAFARI_FAIL
    except Exception as e:  # 最后兜底：任何编程错误也不让 traceback 污染 stderr
        emit_event("fatal", EXIT_SAFARI_FAIL,
                   f"未预期异常 ({type(e).__name__}): {e}")
        return EXIT_SAFARI_FAIL

    # 4. stdout 仅输出回答文本（下游解析用）
    if answer:
        print(answer)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())