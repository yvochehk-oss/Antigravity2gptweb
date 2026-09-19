#!/usr/bin/env python3
"""
Browser-Skill (bsk) ChatGPT Cognitive-Control Bridge (v4.2 Evidence-Integrity)
------------------------------------------------------------------------------
基于 Tencent browser-skill (`bsk`) CLI 与浏览器扩展的高阶控制面驱动适配器。

核心优势：
  1. 【免调试端口】：无须 --remote-debugging-port，直接复用日常已登录 Chrome/Edge 浏览器；
  2. 【优雅风控处置】：内置 bsk request-help，遇到 Cloudflare Turnstile / 人机验证自动唤醒人工并等待恢复；
  3. 【语义化稳定驱动】：支持借用已有标签页或在 Agent Window 中直接执行；
  4. 【证据完整性对齐】：完全对齐 P0/P1 退出码规范、TargetTabLock 事务锁与结构化 JSONL 事件流。

调用范例见 SKILL.md。
"""

import sys
import os
import re
import time
import json
import tempfile

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None
import subprocess
import argparse
import hashlib
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse

# =============================================================================
# 退出码常量 (Verification Plane 唯一判据，与 safari_chatgpt.py 100% 对齐)
# =============================================================================
EXIT_OK              = 0
EXIT_TIMEOUT_PARTIAL = 2
EXIT_TIMEOUT_EMPTY   = 3
EXIT_SAFARI_FAIL     = 4   # 通用浏览器通道失败（保持错误码数值统一）
EXIT_BASELINE_FAIL   = 5
EXIT_SUBMIT_FAIL     = 6
EXIT_NO_NEW_TURN     = 7
EXIT_NO_TAB          = 10
EXIT_AMBIGUOUS_TAB   = 11
EXIT_CIRCUIT_OPEN    = 12
EXIT_TARGET_BUSY     = 13

EXIT_CODE_NAME = {
    EXIT_OK: "OK",
    EXIT_TIMEOUT_PARTIAL: "TIMEOUT_PARTIAL",
    EXIT_TIMEOUT_EMPTY: "TIMEOUT_EMPTY",
    EXIT_SAFARI_FAIL: "BROWSER_FAIL",
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
CIRCUIT_STATE_FILE   = os.path.join(
    tempfile.gettempdir(), "bsk_chatgpt_circuit_breaker.json"
)
CIRCUIT_WINDOW_SEC   = 3600
CIRCUIT_MAX_RETRIES  = 3

BSK_CLI_TIMEOUT_SEC  = 30      # 单次 bsk CLI evaluate / navigate 超时
SUBMIT_PHASE_BUDGET  = 20
TURN_PHASE_BUDGET    = 60
STABLE_PHASE_MIN     = 5
GIT_SUBPROCESS_TIMEOUT_SEC = 5

_BROWSER_NAME = "bsk"


# =============================================================================
# Secret 脱敏 (与 safari_chatgpt.py / chrome_chatgpt.py 100% 对齐)
# =============================================================================
_GH_TOKEN_RE        = re.compile(r'(gh[pousr]_[A-Za-z0-9_]{16,})')
_GITHUB_PAT_RE      = re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b')
_OPENAI_RE          = re.compile(r'(sk-[A-Za-z0-9_-]{20,})')
_BEARER_RE          = re.compile(r'(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})', re.IGNORECASE)
_PASSWORD_RE        = re.compile(r'(password\s*[:=]\s*["\']?)([^"\'\s]+)(["\']?)', re.IGNORECASE)
_AWS_RE             = re.compile(r'((?:AKIA|ASIA)[0-9A-Z]{16})')
_SLACK_RE           = re.compile(r'\b(xox[abprs]-[A-Za-z0-9-]{10,})\b')
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END \1-----",
    re.DOTALL,
)
_PEM_HEADER_RE      = re.compile(r'-----BEGIN [A-Z ]+PRIVATE KEY-----')
_DBCONN_RE          = re.compile(r'(?P<scheme>(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s"\'<>]+)', re.IGNORECASE)
_GENKEY_RE          = re.compile(
    r'((?:[A-Z][A-Z0-9_]*_?(?:KEY|SECRET|TOKEN))\s*[:=]\s*["\']?)([A-Za-z0-9._/+-]{16,})(["\']?)',
    re.IGNORECASE,
)
_JWT_RE             = re.compile(r'(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_\-+/=]{4,})')

_REDACT_EMAIL = os.environ.get("BSK_BRIDGE_REDACT_EMAIL", "0") == "1"
_REDACT_PHONE = os.environ.get("BSK_BRIDGE_REDACT_PHONE", "0") == "1"
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
    text = _PEM_PRIVATE_KEY_RE.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _PEM_HEADER_RE.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _DBCONN_RE.sub('[REDACTED_DB_CONNECTION]', text)
    text = _GENKEY_RE.sub(r'\1[REDACTED_SECRET]\3', text)
    text = _JWT_RE.sub('[REDACTED_JWT]', text)
    if _REDACT_EMAIL:
        text = _EMAIL_RE.sub('[REDACTED_EMAIL]', text)
    if _REDACT_PHONE:
        text = _PHONE_RE.sub('[REDACTED_PHONE]', text)
    text = text.replace(os.path.expanduser("~"), "~")
    return text


def _sanitize_event_value(value: Any) -> Any:
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
        "browser": _BROWSER_NAME,
    }
    payload.update(extra)
    payload = _sanitize_event_value(payload)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


# =============================================================================
# 失败签名规范化与熔断器
# =============================================================================
def normalize_failure_signature(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '<UUID>', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(17\d{8}|20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\b', '<TIMESTAMP>', text)
    text = re.sub(r'/(?:tmp|var/folders)/[^\s:\'",]+', '<TEMP_PATH>', text)
    text = re.sub(r'\b(?:pid|PID)\s*[:=]?\s*\d+\b', '<PID>', text)
    text = re.sub(r'\bline\s+\d+\b', '<LINE>', text)
    return text.strip()


class CircuitOpenError(RuntimeError):
    pass


def _lock_fd(fd: int, *, blocking: bool) -> None:
    """Acquire one byte of OS-backed advisory locking without third-party deps."""
    if os.name == "nt":
        if msvcrt is None:
            raise RuntimeError("Windows file locking backend unavailable")
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as e:
            if not blocking:
                raise BlockingIOError(str(e)) from e
            raise
        return

    if fcntl is None:
        raise RuntimeError("POSIX file locking backend unavailable")
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    fcntl.flock(fd, flags)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        if msvcrt is None:
            return
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _circuit_lock_path() -> str:
    return CIRCUIT_STATE_FILE + ".lock"


def _read_circuit_state_unlocked() -> Dict[str, Any]:
    if not os.path.exists(CIRCUIT_STATE_FILE):
        return {}
    try:
        with open(CIRCUIT_STATE_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_state_unlocked(state: Dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=os.path.dirname(CIRCUIT_STATE_FILE), delete=False
    ) as tf:
        json.dump(state, tf, indent=2, ensure_ascii=False)
        tmp_name = tf.name
    os.replace(tmp_name, CIRCUIT_STATE_FILE)


def check_circuit_breaker(signature: str) -> None:
    norm_sig = normalize_failure_signature(signature)
    lock_fd = os.open(_circuit_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_fd(lock_fd, blocking=True)
        state = _read_circuit_state_unlocked()
        now = time.time()
        failures = state.get(norm_sig, [])
        valid_failures = [t for t in failures if now - t < CIRCUIT_WINDOW_SEC]
        if len(valid_failures) != len(failures):
            if valid_failures:
                state[norm_sig] = valid_failures
            else:
                state.pop(norm_sig, None)
            _atomic_write_state_unlocked(state)
        if len(valid_failures) >= CIRCUIT_MAX_RETRIES:
            raise CircuitOpenError(
                f"熔断已触发：签名 {norm_sig!r} 在过去 {CIRCUIT_WINDOW_SEC}s 内已连续失败 "
                f"{len(valid_failures)} 次（>= {CIRCUIT_MAX_RETRIES}），拒绝重复执行。"
            )
    finally:
        try:
            _unlock_fd(lock_fd)
        finally:
            os.close(lock_fd)


def record_failure(signature: str) -> None:
    norm_sig = normalize_failure_signature(signature)
    lock_fd = os.open(_circuit_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_fd(lock_fd, blocking=True)
        state = _read_circuit_state_unlocked()
        now = time.time()
        failures = state.get(norm_sig, [])
        valid_failures = [t for t in failures if now - t < CIRCUIT_WINDOW_SEC]
        valid_failures.append(now)
        state[norm_sig] = valid_failures
        _atomic_write_state_unlocked(state)
    finally:
        try:
            _unlock_fd(lock_fd)
        finally:
            os.close(lock_fd)


def record_success(signature: str) -> None:
    norm_sig = normalize_failure_signature(signature)
    lock_fd = os.open(_circuit_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_fd(lock_fd, blocking=True)
        state = _read_circuit_state_unlocked()
        if norm_sig in state:
            state.pop(norm_sig, None)
            _atomic_write_state_unlocked(state)
    finally:
        try:
            _unlock_fd(lock_fd)
        finally:
            os.close(lock_fd)


def reset_circuit_breaker() -> int:
    lock_fd = os.open(_circuit_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    cleared = 0
    try:
        _lock_fd(lock_fd, blocking=True)
        state = _read_circuit_state_unlocked()
        cleared = len(state)
        if os.path.exists(CIRCUIT_STATE_FILE):
            os.remove(CIRCUIT_STATE_FILE)
    finally:
        try:
            _unlock_fd(lock_fd)
        finally:
            os.close(lock_fd)
    return cleared


# =============================================================================
# TargetTabLock 事务锁（P0-2：跨进程对同一 target_url 加互斥锁）
# =============================================================================
class TargetTabBusyError(RuntimeError):
    pass


class TargetTabLock:
    def __init__(self, target_url: str):
        digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:24]
        self.path = os.path.join(
            tempfile.gettempdir(), f"bsk_chatgpt_tab_{digest}.lock"
        )
        self.fd: Optional[int] = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_fd(self.fd, blocking=False)
        except BlockingIOError:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            raise TargetTabBusyError(
                f"目标 ChatGPT Tab 正在被另一 bridge 占用（{self.path}）。"
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            try:
                _unlock_fd(self.fd)
            finally:
                try:
                    os.close(self.fd)
                except Exception:
                    pass
                self.fd = None
        return False


# =============================================================================
# 错误类定义
# =============================================================================
class BSKError(RuntimeError):
    pass


class NoTargetTabError(BSKError):
    pass


class AmbiguousTargetTabError(BSKError):
    pass


# =============================================================================
# target_url 校验
# =============================================================================
_ALLOWED_CHATGPT_HOSTS = {"chatgpt.com", "www.chatgpt.com"}


def validate_target_url(url: str) -> None:
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


def extract_conversation_uuid(url: str) -> Optional[str]:
    m = re.search(
        r'/c/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
        url,
        re.IGNORECASE,
    )
    return m.group(1).lower() if m else None


# =============================================================================
# Git 上下文
# =============================================================================
def get_git_head_context(cwd: Optional[str] = None) -> str:
    try:
        sha_res = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except Exception:
        return "Git: unavailable"

    if sha_res.returncode != 0:
        return "Git: untracked"

    sha = sha_res.stdout.strip()
    try:
        status_res = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except Exception:
        return f"Git: {sha} (unavailable)"

    if status_res.returncode != 0:
        return f"Git: {sha} (unavailable)"
    dirty = " (dirty)" if status_res.stdout.strip() else " (clean)"
    return f"Git: {sha}{dirty}"


# =============================================================================
# BSK 客户端包装器
# =============================================================================
class BSKClient:
    """包装 bsk CLI 调用，处理 JSON 解析、超时与异常。"""

    def __init__(
        self,
        session_name: str = "Antigravity2GPT",
        browser_profile: Optional[str] = None,
    ):
        self.session_name = session_name
        self.browser_profile = (browser_profile or "").strip() or None
        self.selected_browser_instance_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.agent_window_id: Optional[int] = None
        self.borrowed_tab_id: Optional[int] = None
        self.active_tab_id: Optional[int] = None

    def _exec_bsk(self, args: List[str], timeout: int = BSK_CLI_TIMEOUT_SEC) -> Tuple[int, str, str]:
        cmd = ["bsk"] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as e:
            raise BSKError(f"bsk 命令执行超时 (>{timeout}s): {' '.join(cmd)}") from e
        except FileNotFoundError:
            raise BSKError("系统中未找到 bsk 可执行文件，请确认已安装并加入 PATH")

    def check_daemon(self) -> Dict[str, Any]:
        code, out, err = self._exec_bsk(["status", "--json"], timeout=5)
        if code != 0:
            raise BSKError(f"bsk daemon 检查失败: {err.strip() or out.strip()}")
        try:
            data = json.loads(out)
            return data
        except json.JSONDecodeError:
            raise BSKError(f"bsk status 输出非合法 JSON: {out[:200]}")

    def list_browser_profiles(self) -> List[Dict[str, Any]]:
        """返回当前连接到 daemon 的 BrowserSkill 浏览器实例（一个实例对应一个浏览器 Profile/扩展存储域）。"""
        st = self.check_daemon()
        browsers = st.get("browsers", [])
        return browsers if isinstance(browsers, list) else []

    @staticmethod
    def _browser_profile_summary(browser: Dict[str, Any]) -> str:
        inst = str(browser.get("instance_id", "") or "")
        label = str(browser.get("label", "") or "").strip() or "(未命名)"
        name = str(browser.get("browser_name", "unknown") or "unknown")
        version = str(browser.get("browser_version", "") or "")
        return f"{label} [{inst}] {name} {version}".strip()

    def resolve_browser_profile(self) -> str:
        """
        将用户给出的 Profile 选择解析成稳定 instance_id。
        允许输入 instance_id 或唯一 label；多实例未指定时 fail-closed，绝不猜测。
        """
        browsers = self.list_browser_profiles()
        if not browsers:
            raise BSKError(
                "当前没有 BrowserSkill 浏览器实例在线；请先在目标 Chrome/Edge Profile 中安装并启用扩展。"
            )

        selector = self.browser_profile
        if not selector:
            if len(browsers) == 1:
                inst = str(browsers[0].get("instance_id", "") or "").strip()
                if not inst:
                    raise BSKError("唯一在线 BrowserSkill 实例缺少 instance_id，无法安全启动 session。")
                self.selected_browser_instance_id = inst
                return inst
            choices = "; ".join(self._browser_profile_summary(b) for b in browsers)
            raise BSKError(
                "检测到多个 BrowserSkill 浏览器/Profile 实例，拒绝隐式选择。"
                "请使用 --browser-profile <instance_id|唯一label> 指定目标。"
                f" 当前可选：{choices}"
            )

        id_matches = [
            b for b in browsers
            if str(b.get("instance_id", "") or "").strip() == selector
        ]
        if len(id_matches) == 1:
            self.selected_browser_instance_id = selector
            return selector

        label_matches = [
            b for b in browsers
            if str(b.get("label", "") or "").strip() == selector
        ]
        if len(label_matches) == 1:
            inst = str(label_matches[0].get("instance_id", "") or "").strip()
            if not inst:
                raise BSKError(f"Profile label {selector!r} 对应实例缺少 instance_id。")
            self.selected_browser_instance_id = inst
            return inst
        if len(label_matches) > 1:
            ids = ", ".join(
                str(b.get("instance_id", "") or "").strip() for b in label_matches
            )
            raise BSKError(
                f"Profile label {selector!r} 匹配多个 BrowserSkill 实例（{ids}）；"
                "请改用明确的 instance_id。"
            )

        choices = "; ".join(self._browser_profile_summary(b) for b in browsers)
        raise BSKError(
            f"未找到 BrowserSkill Profile {selector!r}。当前在线实例：{choices}"
        )

    def start_session(self) -> str:
        selected = self.resolve_browser_profile()
        code, out, err = self._exec_bsk(
            [
                "session", "start", "--json",
                "--name", self.session_name,
                "--browser", selected,
                "--no-focus",
            ],
            timeout=45,
        )
        if code != 0:
            raise BSKError(f"启动 bsk 会话失败: {err.strip() or out.strip()}")
        try:
            data = json.loads(out)
            session_id = data["session_id"]
            actual_browser = str(data.get("browser_instance_id", "") or "").strip()
            if actual_browser != selected:
                self.session_id = session_id
                try:
                    self.stop_session()
                finally:
                    self.session_id = None
                raise BSKError(
                    f"bsk session 绑定实例不一致：requested={selected}, "
                    f"actual={actual_browser or '<missing>'}"
                )
            self.session_id = session_id
            self.selected_browser_instance_id = actual_browser
            self.agent_window_id = data.get("agent_window_id")
            return self.session_id
        except BSKError:
            raise
        except Exception as e:
            raise BSKError(f"解析 bsk session start 结果失败: {e}; out={out[:200]}")

    def stop_session(self) -> None:
        if not self.session_id:
            return
        # 如果借用过 tab，先归还
        if self.borrowed_tab_id is not None:
            try:
                self.return_tab(self.borrowed_tab_id)
            except Exception:
                pass
            self.borrowed_tab_id = None

        sid = self.session_id
        self.session_id = None
        try:
            self._exec_bsk(["session", "stop", sid], timeout=5)
        except Exception:
            pass

    def list_tabs(self, scope: str = "all") -> List[Dict[str, Any]]:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 list_tabs")
        code, out, err = self._exec_bsk(
            ["tab", "list", "--session", self.session_id, "--scope", scope, "--json"],
            timeout=10,
        )
        if code != 0:
            raise BSKError(f"获取 tab 列表失败: {err.strip() or out.strip()}")
        try:
            data = json.loads(out)
            return data.get("tabs", [])
        except Exception as e:
            raise BSKError(f"解析 tab list 失败: {e}; out={out[:200]}")

    def borrow_tab(self, tab_id: int) -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 borrow_tab")
        code, out, err = self._exec_bsk(
            ["tab", "borrow", str(tab_id), "--session", self.session_id, "--timeout", "60s"],
            timeout=75,
        )
        if code != 0:
            raise BSKError(f"借用 tab {tab_id} 失败: {err.strip() or out.strip()}")
        self.borrowed_tab_id = tab_id
        self.active_tab_id = tab_id

    def return_tab(self, tab_id: int) -> None:
        if not self.session_id:
            return
        self._exec_bsk(["tab", "return", str(tab_id), "--session", self.session_id], timeout=5)
        if self.borrowed_tab_id == tab_id:
            self.borrowed_tab_id = None
            if self.active_tab_id == tab_id:
                self.active_tab_id = None

    def navigate(self, url: str) -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 navigate")
        code, out, err = self._exec_bsk(
            ["navigate", url, "--session", self.session_id, "--wait-until", "domcontentloaded", "--timeout", "30s"],
            timeout=35,
        )
        if code != 0:
            raise BSKError(f"导航至 {url} 失败: {err.strip() or out.strip()}")

    def evaluate(self, js_expr: str, timeout: int = BSK_CLI_TIMEOUT_SEC) -> Any:
        """执行 JavaScript 并返回结果。若 JS 内部抛出异常或 CLI 报错，抛出 BSKError。"""
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 evaluate")
        args = ["evaluate", "--session", self.session_id, "--json", js_expr]
        if self.active_tab_id is not None:
            args.extend(["--tab-id", str(self.active_tab_id)])
        code, out, err = self._exec_bsk(args, timeout=timeout)
        if code != 0:
            raise BSKError(f"bsk evaluate CLI 失败: {err.strip() or out.strip()}")
        try:
            res = json.loads(out)
        except json.JSONDecodeError as e:
            raise BSKError(f"bsk evaluate 输出无法解析为 JSON: {e}; out={out[:200]}")
        if not res.get("ok", False):
            err_info = res.get("error", {})
            err_msg = err_info.get("text") if isinstance(err_info, dict) else str(err_info)
            raise BSKError(f"JS 执行报错: {err_msg}")
        return res.get("value")

    def request_help(self, prompt: str, timeout: str = "5m") -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 request_help")
        args = [
            "request-help", "--session", self.session_id,
            "--prompt", prompt, "--timeout", timeout, "--json",
        ]
        if self.active_tab_id is not None:
            args.extend(["--tab-id", str(self.active_tab_id)])
        code, out, err = self._exec_bsk(args, timeout=330)
        if code != 0:
            raise BSKError(f"bsk request-help 失败: {err.strip() or out.strip()}")
        try:
            result = json.loads(out)
        except json.JSONDecodeError as e:
            raise BSKError(f"bsk request-help 输出非合法 JSON: {out[:200]}") from e
        outcome = str(result.get("outcome", "") or "").strip().lower()
        if outcome in ("continued", "completed"):
            return
        if outcome in ("cancelled", "timed_out", "disabled", "navigated"):
            raise BSKError(f"人工协助未完成，outcome={outcome}")
        raise BSKError(f"人工协助返回未知 outcome={outcome!r}: {out[:200]}")

    def focus(self, selector: str = "#prompt-textarea") -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 focus")
        args = ["focus", selector, "--session", self.session_id, "--json"]
        if self.active_tab_id is not None:
            args.extend(["--tab-id", str(self.active_tab_id)])
        code, out, err = self._exec_bsk(args, timeout=5)
        if code != 0:
            raise BSKError(f"bsk focus {selector} 失败: {err.strip() or out.strip()}")

    def press_key(self, key: str = "Enter") -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 press_key")
        args = ["press", key, "--session", self.session_id, "--json"]
        if self.active_tab_id is not None:
            args.extend(["--tab-id", str(self.active_tab_id)])
        code, out, err = self._exec_bsk(args, timeout=5)
        if code != 0:
            raise BSKError(f"bsk press {key} 失败: {err.strip() or out.strip()}")

    def click_element(self, selector: str = 'button[data-testid="send-button"]') -> None:
        if not self.session_id:
            raise BSKError("无可用 session_id，无法 click_element")
        args = ["click", selector, "--session", self.session_id, "--json"]
        if self.active_tab_id is not None:
            args.extend(["--tab-id", str(self.active_tab_id)])
        code, out, err = self._exec_bsk(args, timeout=10)
        if code != 0:
            raise BSKError(f"bsk click {selector} 失败: {err.strip() or out.strip()}")


# =============================================================================
# DOM 工具与 JS 模板
# =============================================================================
_BASELINE_JS = r"""
(() => {
    const all  = document.querySelectorAll("[data-message-author-role]");
    const usr  = document.querySelectorAll("[data-message-author-role='user']");
    const asst = document.querySelectorAll("[data-message-author-role='assistant']");
    const lastU = usr.length  > 0 ? usr[usr.length - 1]  : null;
    const lastA = asst.length > 0 ? asst[asst.length - 1] : null;
    function midOf(el) {
        if (!el) return null;
        return el.getAttribute("data-message-id") ||
            (el.closest && el.closest("[data-message-id]")
                ? el.closest("[data-message-id]").getAttribute("data-message-id")
                : null);
    }
    return {
        totalCount: all.length,
        userCount: usr.length,
        assistantCount: asst.length,
        lastUserText: lastU ? (lastU.innerText || "").trim() : "",
        lastAsstText: lastA ? (lastA.innerText || "").trim() : "",
        lastUserMessageId: midOf(lastU),
        lastAsstMessageId: midOf(lastA),
    };
})()
"""

_DETECT_CHALLENGE_JS = r"""
(() => {
    const cf1 = document.querySelector("iframe[src*='challenges.cloudflare.com']");
    const cf2 = document.querySelector("#challenge-running") || document.querySelector("#challenge-stage");
    const cf3 = document.title.includes("Just a moment") || document.title.includes("Checking your browser");
    const cf4 = document.body && (
        document.body.innerText.includes("Verify you are human") ||
        document.body.innerText.includes("确认您是真人") ||
        document.body.innerText.includes("Please enable JavaScript and Cookies")
    );
    return !!(cf1 || cf2 || cf3 || cf4);
})()
"""

def _last_message_id_js(role: str) -> str:
    return f"""
    (() => {{
        const nodes = document.querySelectorAll("[data-message-author-role='{role}']");
        const last = nodes.length > 0 ? nodes[nodes.length - 1] : null;
        if (!last) return {{id: null, count: 0, textLen: 0, fp: ""}};
        const id =
            last.getAttribute("data-message-id") ||
            (last.closest && last.closest("[data-message-id]")
                ? last.closest("[data-message-id]").getAttribute("data-message-id")
                : null);
        return {{
            id: id,
            count: nodes.length,
            textLen: (last.innerText || "").trim().length,
            fp: ((last.innerText || "").trim()).slice(0, 80),
        }};
    }})()
    """

def _user_commit_probe_js(expected_prompt: str) -> str:
    expected_literal = json.dumps(expected_prompt)
    return f"""
    (() => {{
        const norm = s =>
            (s || "")
                .replace(/\\r\\n/g, "\\n")
                .replace(/\\u00a0/g, " ")
                .replace(/\\s+/g, " ")
                .trim();
        const users = document.querySelectorAll("[data-message-author-role='user']");
        const last  = users.length > 0 ? users[users.length - 1] : null;
        const text  = last ? norm(last.innerText || "") : "";
        const id =
            last ? (last.getAttribute("data-message-id") ||
                (last.closest && last.closest("[data-message-id]")
                    ? last.closest("[data-message-id]").getAttribute("data-message-id")
                    : null)) : null;
        const exp = norm({expected_literal});
        const match = (text === exp) || (exp.length > 20 && (text.startsWith(exp.slice(0, 20)) || text.includes(exp.slice(0, 20)) || exp.startsWith(text.slice(0, 20))));
        return {{
            userCount: users.length,
            matchesExpected: match,
            textLen: text.length,
            messageId: id,
        }};
    }})()
    """

def _inject_verify_js(expected_prompt: str) -> str:
    expected_literal = json.dumps(expected_prompt)
    return f"""
    (() => {{
        const norm = s =>
            (s || "")
                .replace(/\\r\\n/g, "\\n")
                .replace(/\\u00a0/g, " ")
                .replace(/\\s+/g, " ")
                .trim();
        const el = document.querySelector("#prompt-textarea") ||
                   document.querySelector("form [contenteditable='true']") ||
                   document.querySelector("form");
        if (!el) return {{ok: false, reason: "NO_INPUT"}};
        const actual = (el.innerText || el.textContent || el.value || "");
        const normActual = norm(actual);
        const exp = norm({expected_literal});
        const match = (normActual === exp) || (exp.length > 20 && (normActual.startsWith(exp.slice(0, 20)) || normActual.includes(exp.slice(0, 20)) || exp.startsWith(normActual.slice(0, 20))));
        return {{
            ok: true,
            matchesExpected: match,
            textLen: normActual.length,
            visible: !!(el.offsetWidth || el.offsetHeight || (el.getClientRects && el.getClientRects().length)),
            isContentEditable: !!el.isContentEditable,
        }};
    }})()
    """

def _stable_poll_js(target_message_id: Optional[str]) -> str:
    safe_msg_id = target_message_id.replace("'", "") if target_message_id else ""
    query_part = f'document.querySelector("[data-message-id=\'{safe_msg_id}\']")' if target_message_id else 'null'
    return f"""
    (() => {{
        const stopBtn = document.querySelector("button[data-testid='stop-button']") ||
                        document.querySelector("button[aria-label='停止回答']");
        let node = {query_part};
        const asst = document.querySelectorAll("[data-message-author-role='assistant']");
        const last = asst.length > 0 ? asst[asst.length - 1] : null;
        if (!node && last) {{
            node = last;
        }}
        if (!node) return {{
            targetPresent: false, isStreaming: !!stopBtn, text: "",
            messageId: null
        }};
        return {{
            targetPresent: true,
            isStreaming: !!stopBtn,
            text: (node.innerText || "").trim(),
            messageId: node.getAttribute("data-message-id") ||
                (node.closest && node.closest("[data-message-id]")
                    ? node.closest("[data-message-id]").getAttribute("data-message-id")
                    : null),
        }};
    }})()
    """


# =============================================================================
# Payload 格式化
# =============================================================================
def _collect_local_diagnostics(cwd: Optional[str], max_lines: int = 40) -> str:
    """
    Empty-evidence self-heal: collect a bounded, read-only worktree snapshot so
    the cloud reviewer can distinguish a clean run from a dirty local checkout.
    Never mutates the repository and never includes file contents.
    """
    if not cwd:
        return "[no cwd supplied; automatic local diagnostics unavailable]"
    try:
        res = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except Exception as e:
        return sanitize_text(f"[git status unavailable: {type(e).__name__}: {e}]")
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        return sanitize_text(f"[git status failed rc={res.returncode}: {detail[:500]}]")
    lines = [line for line in res.stdout.splitlines() if line.strip()]
    if not lines:
        return "[auto diagnostics] git worktree is clean"
    clipped = lines[:max_lines]
    suffix = ""
    if len(lines) > max_lines:
        suffix = f"\n... [{len(lines) - max_lines} additional status lines omitted] ..."
    return sanitize_text(
        "[auto diagnostics] git status --short --untracked-files=all:\n"
        + "\n".join(clipped)
        + suffix
    )


def format_evidence_payload(task_type: str, context_text: str,
                            evidence_data: Optional[str] = None,
                            level: str = "L1",
                            cwd: Optional[str] = None) -> str:
    git_ctx = get_git_head_context(cwd)
    context_text = sanitize_text(context_text)
    evidence_data = sanitize_text(evidence_data or "")
    if task_type in ("feedback", "task-review") and not evidence_data.strip() and level != "L0":
        evidence_data = _collect_local_diagnostics(cwd)

    if level == "L0":
        evidence_snippet = "[L0 No Evidence Body: only request context provided]"
    elif level == "L1":
        lines = evidence_data.strip().splitlines()
        if len(lines) > 40:
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
# Tab Borrow 精确绑定
# =============================================================================
def _matching_target_tabs(
    tabs: List[Dict[str, Any]], target_url: str
) -> List[Dict[str, Any]]:
    """只匹配明确指定的 conversation/page，禁止模糊借用其他 ChatGPT Tab。"""
    target_uuid = extract_conversation_uuid(target_url)
    if target_uuid:
        return [
            t for t in tabs
            if extract_conversation_uuid(str(t.get("url", "") or "")) == target_uuid
        ]

    def canonical(url: str) -> Tuple[str, str, str, str]:
        try:
            p = urlparse(url)
            return (
                p.scheme.lower(),
                (p.hostname or "").lower(),
                (p.path or "/").rstrip("/") or "/",
                p.query,
            )
        except Exception:
            return ("", "", "", "")

    wanted = canonical(target_url)
    return [
        t for t in tabs
        if canonical(str(t.get("url", "") or "")) == wanted
    ]


# =============================================================================
# 核心桥接实现：send_and_receive_bsk_chatgpt
# =============================================================================
def send_and_receive_bsk_chatgpt(
    client: BSKClient,
    prompt: str,
    target_url: str,
    wait_timeout: int = 180,
    submit_deadline: int = SUBMIT_PHASE_BUDGET,
    turn_deadline: Optional[int] = None,
    allow_borrow: bool = True,
) -> Tuple[int, str]:
    if turn_deadline is None:
        turn_deadline = max(45, min(90, wait_timeout // 2))

    overall_deadline = time.monotonic() + wait_timeout

    def remaining() -> float:
        return max(0.0, overall_deadline - time.monotonic())

    emit_event("start", EXIT_OK, "Browser-Skill (bsk) ChatGPT Bridge v4.2 启动",
               target_url=target_url, wait_timeout=wait_timeout)

    # ---- 步骤 0：Session 启动与 Tab 绑定 ----
    try:
        client.start_session()
    except BSKError as e:
        emit_event("session_start", EXIT_SAFARI_FAIL, f"启动 bsk 会话失败: {e}")
        return EXIT_SAFARI_FAIL, ""

    # 默认使用 Agent Window；只有显式 --borrow 时才尝试精确借用目标 Tab。
    bound_target = False

    if allow_borrow:
        try:
            user_tabs = client.list_tabs(scope="user")
            matching_tabs = _matching_target_tabs(user_tabs, target_url)

            if len(matching_tabs) == 1:
                tab_id = matching_tabs[0]["tab_id"]
                try:
                    client.borrow_tab(tab_id)
                    bound_target = True
                    emit_event(
                        "tab_bind", EXIT_OK,
                        f"成功借用精确目标 ChatGPT 标签页 (tab_id={tab_id})",
                        tab_id=tab_id,
                    )
                except Exception as e:
                    emit_event(
                        "tab_borrow_fallback", EXIT_OK,
                        f"精确目标 Tab 借用失败，降级为 Agent Window: {e}",
                    )
            elif len(matching_tabs) > 1:
                emit_event(
                    "tab_borrow_ambiguous", EXIT_OK,
                    f"发现 {len(matching_tabs)} 个精确匹配目标 Tab，拒绝猜测并改用 Agent Window",
                )
            else:
                emit_event(
                    "tab_borrow_miss", EXIT_OK,
                    "未找到精确目标 Tab，不借用其他 ChatGPT 页面，改用 Agent Window",
                )
        except Exception as e:
            emit_event("tab_borrow_err", EXIT_OK, f"列出或借用标签异常: {e}")

    if not bound_target:
        # 在 Agent Window 标签中直接导航
        try:
            emit_event("navigate", EXIT_OK, f"在 Agent Window 中导航至目标 URL", target_url=target_url)
            client.navigate(target_url)
        except BSKError as e:
            emit_event("navigate", EXIT_SAFARI_FAIL, f"页面导航失败: {e}")
            return EXIT_SAFARI_FAIL, ""

    # ---- 步骤 0.5：风控检测与人机验证（Cloudflare / CAPTCHA）----
    time.sleep(1.0)
    try:
        is_challenged = client.evaluate(_DETECT_CHALLENGE_JS)
        if is_challenged:
            emit_event("human_challenge", EXIT_OK, "检测到 Cloudflare / 人机验证，唤醒 bsk request-help 请求人工协助")
            client.request_help("检测到 Cloudflare 人机验证，请在弹出的浏览器窗口中完成验证并继续", timeout="5m")
            emit_event("human_challenge", EXIT_OK, "人工协助完成，继续执行后续流程")
            time.sleep(2.0)
    except Exception as e:
        emit_event("challenge_check", EXIT_OK, f"人机验证探测非阻断提示: {e}")

    # 等待页面输入框就绪（必须等待 React 水合完成，contenteditable 置为 true）
    composer_ready_js = """
    (() => {
        const el = document.querySelector('#prompt-textarea');
        if (!el) return false;
        return el.getAttribute('contenteditable') === 'true' || el.isContentEditable || el.tagName.toLowerCase() === 'textarea';
    })()
    """
    ready_start = time.monotonic()
    ready = False
    while time.monotonic() - ready_start < 25 and remaining() > 0:
        try:
            if client.evaluate(composer_ready_js):
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not ready:
        emit_event("ready_check", EXIT_SAFARI_FAIL, "页面在 25s 内未能加载出 ChatGPT 输入框")
        return EXIT_SAFARI_FAIL, ""

    # 等待页面历史消息稳定呈现
    time.sleep(1.0)

    # ---- 步骤 1：基线采集 ----
    try:
        baseline = client.evaluate(_BASELINE_JS)
    except BSKError as e:
        emit_event("baseline", EXIT_BASELINE_FAIL, f"基线采集失败: {e}")
        return EXIT_BASELINE_FAIL, ""

    if not isinstance(baseline, dict) or "totalCount" not in baseline or "userCount" not in baseline:
        emit_event("baseline", EXIT_BASELINE_FAIL, f"基线格式异常: {baseline}")
        return EXIT_BASELINE_FAIL, ""

    emit_event("baseline", EXIT_OK, "基线采集成功",
               totalCount=baseline.get("totalCount"),
               userCount=baseline.get("userCount"),
               assistantCount=baseline.get("assistantCount"),
               lastUserMessageId=baseline.get("lastUserMessageId"))

    # ---- 步骤 2：注入 prompt ----
    try:
        client.focus("#prompt-textarea")
    except Exception:
        pass

    js_inject = f"""
    (() => {{
        const el = document.querySelector('#prompt-textarea') ||
                   document.querySelector("form [contenteditable='true']") ||
                   document.querySelector("form");
        if (!el) return "ERR_NO_INPUT";
        el.focus();
        
        const p = el.querySelector("p") || el;
        p.innerText = {json.dumps(prompt)};
        
        try {{
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, {json.dumps(prompt)});
        }} catch(e) {{}}
        
        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: {json.dumps(prompt)} }}));
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return "OK";
    }})()
    """
    try:
        res = client.evaluate(js_inject)
    except BSKError as e:
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框注入失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    if res != "OK":
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框定位失败: {res}")
        return EXIT_SAFARI_FAIL, ""

    # ---- 步骤 2.5：Composer 注入验证 ----
    cv_budget = min(5.0, submit_deadline, remaining())
    if cv_budget <= 0:
        emit_event("composer_verify", EXIT_TIMEOUT_EMPTY, "无预算执行 composer 验证")
        return EXIT_TIMEOUT_EMPTY, ""

    cv_probe = _inject_verify_js(prompt)
    cv_deadline = time.monotonic() + cv_budget
    cv_snap = None
    while time.monotonic() < cv_deadline:
        try:
            cv_snap = client.evaluate(cv_probe)
            if cv_snap.get("ok") is True and cv_snap.get("matchesExpected") is True:
                break
        except Exception:
            pass
        time.sleep(0.3)

    if not (cv_snap and cv_snap.get("ok") and cv_snap.get("matchesExpected")):
        emit_event("composer_verify", EXIT_SUBMIT_FAIL, f"Composer 注入未生效或未匹配: {cv_snap}")
        return EXIT_SUBMIT_FAIL, ""

    emit_event("composer_verify", EXIT_OK, "Composer 内容已确认为 expected prompt",
               textLen=cv_snap.get("textLen"),
               visible=cv_snap.get("visible"),
               isContentEditable=cv_snap.get("isContentEditable"))

    # ---- 步骤 3：触发发送 ----
    # 等待 ChatGPT React 将语音按钮切换为发送按钮
    send_btn_ready_js = """
    (() => {
        const b = document.querySelector("button[data-testid='send-button']") ||
                  document.querySelector("#composer-submit-button");
        return !!b && !b.disabled && b.getAttribute("aria-disabled") !== "true";
    })()
    """
    sb_budget = min(5.0, max(0.0, float(submit_deadline)), remaining())
    sb_deadline = time.monotonic() + sb_budget
    send_button_ready = False
    while time.monotonic() < sb_deadline:
        try:
            if client.evaluate(send_btn_ready_js):
                send_button_ready = True
                break
        except Exception:
            pass
        time.sleep(0.2)
    if not send_button_ready:
        emit_event(
            "send_ready", EXIT_OK,
            "发送按钮在等待预算内未 ready；继续原生点击/Enter 回退链，并以提交验证为最终判据",
        )

    send_res = "BSK_CLICK_SEND_BTN"
    try:
        # 优先通过 bsk click 原生点击发送按钮
        client.click_element('button[data-testid="send-button"]')
    except Exception as e_click:
        emit_event("click_err", EXIT_OK, f"button[data-testid='send-button'] 失败: {e_click}")
        try:
            # 备用 1: 尝试点击 composer-submit-button
            client.click_element("#composer-submit-button")
        except Exception as e_click2:
            emit_event("click_err2", EXIT_OK, f"#composer-submit-button 失败: {e_click2}")
            try:
                # 备用 2: 系统级原生 Enter 按键
                try:
                    client.focus("#prompt-textarea")
                except Exception:
                    pass
                client.press_key("Enter")
                send_res = "BSK_PRESS_ENTER"
            except Exception as e:
                emit_event("press_err", EXIT_OK, f"press_key Enter 失败: {e}")
                # 回退至 JS 点击与表单提交
                js_send = """
                (() => {
                    const submitBtn = document.querySelector("#composer-submit-button") ||
                                      document.querySelector(".composer-submit-button-color") ||
                                      document.querySelector("button[data-testid='send-button']") ||
                                      document.querySelector("button[aria-label*='发送']") ||
                                      document.querySelector("button[aria-label*='Send']");
                    if (submitBtn && !submitBtn.disabled && submitBtn.getAttribute("aria-disabled") !== "true") {
                        submitBtn.click();
                        submitBtn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
                        submitBtn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
                        submitBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                        return "CLICKED_SUBMIT";
                    }
                    return "ERR_NO_SEND";
                })()
                """
                try:
                    send_res = client.evaluate(js_send)
                except Exception:
                    send_res = "FALLBACK_CLICK_FAILED"

    emit_event("send", EXIT_OK, f"发送触发结果: {send_res}")

    # ---- 步骤 4：精确 user-message identity 验证 ----
    sub_budget = min(submit_deadline, remaining())
    if sub_budget <= 0:
        emit_event("submit_verify", EXIT_TIMEOUT_EMPTY, "无预算执行提交验证")
        return EXIT_TIMEOUT_EMPTY, ""

    sub_probe = _user_commit_probe_js(prompt)
    sub_deadline = time.monotonic() + sub_budget
    user_snap = None
    baseline_user_count = baseline.get("userCount", 0)
    baseline_user_id = baseline.get("lastUserMessageId")

    while time.monotonic() < sub_deadline:
        try:
            user_snap = client.evaluate(sub_probe)
            curr_id = user_snap.get("messageId")
            id_changed = (baseline_user_id is not None and curr_id != baseline_user_id) or (baseline_user_id is None and curr_id is not None)
            count_incremented = user_snap.get("userCount") == baseline_user_count + 1
            if (count_incremented or id_changed) and user_snap.get("matchesExpected") is True:
                break
        except Exception:
            pass
        time.sleep(0.4)

    if not (user_snap and user_snap.get("matchesExpected") is True):
        emit_event("submit_verify", EXIT_SUBMIT_FAIL, f"用户消息提交超时或未匹配: {user_snap}")
        return EXIT_SUBMIT_FAIL, ""

    emit_event("submit_verify", EXIT_OK, "用户消息已提交（exact prompt match）",
               newUserCount=user_snap.get("userCount"),
               newUserMessageId=user_snap.get("messageId"),
               newTextLen=user_snap.get("textLen"))

    # ---- 步骤 5：等待助手新回合 ----
    turn_budget = min(turn_deadline, remaining())
    if turn_budget <= 0:
        emit_event("new_turn", EXIT_TIMEOUT_EMPTY, "无预算等待助手新回合")
        return EXIT_TIMEOUT_EMPTY, ""

    asst_probe = _last_message_id_js("assistant")
    asst_deadline = time.monotonic() + turn_budget
    baseline_asst_count = baseline.get("assistantCount", 0)
    baseline_asst_id = baseline.get("lastAsstMessageId")
    asst_snap = None

    while time.monotonic() < asst_deadline:
        try:
            asst_snap = client.evaluate(asst_probe)
            curr_id = asst_snap.get("id")
            id_changed = (baseline_asst_id is not None and curr_id != baseline_asst_id) or (baseline_asst_id is None and curr_id is not None)
            count_incremented = asst_snap.get("count") == baseline_asst_count + 1
            if (count_incremented or id_changed) and asst_snap.get("textLen", 0) > 0:
                break
        except Exception:
            pass
        time.sleep(0.4)

    if not (asst_snap and asst_snap.get("textLen", 0) > 0):
        emit_event("new_turn", EXIT_NO_NEW_TURN, f"助手新回合未产生: {asst_snap}")
        return EXIT_NO_NEW_TURN, ""

    target_asst_id = asst_snap.get("id")
    emit_event("new_turn", EXIT_OK, "助手新回合已产生",
               assistantMessageId=target_asst_id,
               textLen=asst_snap.get("textLen"),
               initialFp=asst_snap.get("fp"))

    # ---- 步骤 6：稳定期轮询与内容提取 ----
    stable_budget = remaining()
    if stable_budget < STABLE_PHASE_MIN:
        emit_event("stable_poll", EXIT_TIMEOUT_EMPTY, f"稳定阶段预算过低: {stable_budget:.1f}s")
        return EXIT_TIMEOUT_EMPTY, ""

    emit_event("stable_poll", EXIT_OK, "进入稳定期轮询",
               targetMessageId=target_asst_id,
               budgetSec=round(stable_budget, 1))

    poll_js = _stable_poll_js(target_asst_id)
    poll_start = time.monotonic()
    last_text = ""
    stable_count = 0
    is_complete = False

    while time.monotonic() - poll_start < stable_budget:
        try:
            snap = client.evaluate(poll_js)
            if snap.get("targetPresent", False):
                text = snap.get("text", "") or ""
                streaming = bool(snap.get("isStreaming", False))
                if not streaming and len(text) > 0:
                    if text == last_text:
                        stable_count += 1
                        if stable_count >= 2:
                            is_complete = True
                            last_text = text
                            break
                    else:
                        stable_count = 0
                        last_text = text
                else:
                    last_text = text
                    stable_count = 0
        except Exception:
            pass
        time.sleep(0.5)

    if is_complete:
        emit_event("finish", EXIT_OK, "助手回答完成并稳定", textLen=len(last_text))
        return EXIT_OK, last_text
    elif last_text:
        emit_event("finish", EXIT_TIMEOUT_PARTIAL, "等待完成超时，截获部分文本", textLen=len(last_text))
        return EXIT_TIMEOUT_PARTIAL, last_text
    else:
        emit_event("finish", EXIT_TIMEOUT_EMPTY, "等待完成超时且未捕获到文本")
        return EXIT_TIMEOUT_EMPTY, ""


# =============================================================================
# CLI 与参数解析
# =============================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Browser-Skill (bsk) ChatGPT Cognitive-Control Bridge v4.2"
    )
    p.add_argument("--prompt", type=str, default="", help="提示词或上下文")
    p.add_argument("--prompt-file", type=str, default=None, help="从文件读取提示词")
    p.add_argument("--target-url", type=str, default="",
                   help="目标 ChatGPT 会话的完整 URL（必须 https://chatgpt.com）")
    p.add_argument("--browser-name", type=str, default="bsk",
                   help="事件 JSONL 中的 browser 字段值（默认 'bsk'）")
    p.add_argument("--type", type=str, default="raw",
                   choices=["raw", "plan", "feedback", "review", "task-code", "task-review"],
                   help="交互协议类型")
    evidence_group = p.add_mutually_exclusive_group()
    evidence_group.add_argument("--evidence", type=str, default=None)
    evidence_group.add_argument("--evidence-file", type=str, default=None)
    p.add_argument("--level", type=str, default="L1",
                   choices=["L0", "L1", "L2", "L3"])
    p.add_argument("--signature", type=str, default=None,
                   help="调用签名（用于熔断器）。")
    p.add_argument("--allow-concurrent", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--cwd", type=str, default=None)
    p.add_argument("--reset-circuit", action="store_true")
    p.add_argument("--check-env", action="store_true", help="自检 bsk 守护进程与浏览器扩展连通性")
    p.add_argument(
        "--list-browser-profiles",
        action="store_true",
        help="列出当前在线的 BrowserSkill 浏览器/Profile 实例后退出",
    )
    p.add_argument(
        "--browser-profile",
        type=str,
        default=os.environ.get("BSK_BROWSER_PROFILE", ""),
        help=(
            "指定 BrowserSkill 浏览器/Profile（instance_id 或唯一 label）。"
            "未指定时仅在恰好一个实例在线时自动选择；多实例会 fail-closed。"
        ),
    )
    borrow_group = p.add_mutually_exclusive_group()
    borrow_group.add_argument(
        "--borrow",
        action="store_true",
        help="显式允许借用用户已有 ChatGPT Tab；默认使用独立 Agent Window，不借用主窗口标签页",
    )
    borrow_group.add_argument(
        "--no-borrow",
        action="store_true",
        help="兼容参数：明确禁用 Tab Borrow（当前已是默认行为）",
    )
    return p


def _load_prompt(args) -> str:
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return args.prompt


def _load_evidence(args) -> Optional[str]:
    if args.evidence_file:
        with open(args.evidence_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return args.evidence


def main() -> int:
    args = _build_parser().parse_args()

    global _BROWSER_NAME
    if args.browser_name:
        _BROWSER_NAME = args.browser_name

    # 浏览器/Profile 枚举模式
    if args.list_browser_profiles:
        client = BSKClient(browser_profile=args.browser_profile or None)
        try:
            browsers = client.list_browser_profiles()
            if not browsers:
                print("(no BrowserSkill browser/profile instances connected)")
                return EXIT_SAFARI_FAIL
            print("INSTANCE    LABEL                 BROWSER")
            for b in browsers:
                inst = str(b.get("instance_id", "") or "")
                label = str(b.get("label", "") or "").strip() or "-"
                name = str(b.get("browser_name", "unknown") or "unknown")
                ver = str(b.get("browser_version", "") or "")
                print(f"{inst:<11} {label:<21} {name} {ver}".rstrip())
            return EXIT_OK
        except Exception as e:
            print(f"[✗] BrowserSkill Profile 枚举失败: {e}", file=sys.stderr)
            return EXIT_SAFARI_FAIL

    # 自检环境模式
    if args.check_env:
        client = BSKClient(browser_profile=args.browser_profile or None)
        try:
            st = client.check_daemon()
            print("==================================================")
            print("  Browser-Skill (bsk) 环境自检报告")
            print("==================================================")
            print(f"  [✓] bsk CLI:            已就绪")
            print(f"  [✓] Daemon Version:     {st.get('daemon_version', 'unknown')}")
            print(f"  [✓] Protocol Version:   {st.get('protocol_version', 'unknown')}")
            browsers = st.get("browsers", [])
            print(f"  [✓] Browsers Connected: {len(browsers)}")
            if browsers:
                for b in browsers:
                    name = b.get('browser_name', 'unknown')
                    b_ver = b.get('browser_version', '')
                    ext_ver = b.get('extension_version', '')
                    inst_id = b.get('instance_id', '')
                    label = (b.get('label', '') or '').strip() or '(未命名)'
                    print(
                        f"      - Profile: {label} | 浏览器: {name} {b_ver} "
                        f"(Ext v{ext_ver}, ID={inst_id})"
                    )
                if args.browser_profile:
                    selected = client.resolve_browser_profile()
                    print(f"  [✓] Selected Profile:    {args.browser_profile} -> {selected}")
                elif len(browsers) > 1:
                    print(
                        "      [!] 多个 Profile 在线：实际运行必须使用 "
                        "--browser-profile <instance_id|唯一label> 明确指定。"
                    )
            else:
                print("      [!] 提示: 当前未连接活跃浏览器实例，请确保 Chrome/Edge 已启动且装有 bsk 扩展。")
            print("==================================================")
            return EXIT_OK if len(browsers) > 0 else EXIT_SAFARI_FAIL
        except Exception as e:
            print(f"[✗] bsk 环境自检失败: {e}", file=sys.stderr)
            return EXIT_SAFARI_FAIL

    if args.reset_circuit:
        cleared = reset_circuit_breaker()
        emit_event("reset_circuit", EXIT_OK, f"已清空熔断器（清理 {cleared} 个 signature）", cleared=cleared)
        return EXIT_OK

    if not args.target_url:
        emit_event("validate_target", EXIT_SAFARI_FAIL, "必须指定 --target-url")
        return EXIT_SAFARI_FAIL

    try:
        validate_target_url(args.target_url)
    except ValueError as e:
        emit_event("validate_target", EXIT_SAFARI_FAIL, f"target_url 校验失败: {e}", target_url=args.target_url)
        return EXIT_SAFARI_FAIL

    raw_prompt = _load_prompt(args)
    if not raw_prompt:
        emit_event("validate_prompt", EXIT_SAFARI_FAIL, "提示词不能为空 (请提供 --prompt 或 --prompt-file)")
        return EXIT_SAFARI_FAIL

    try:
        evidence_body = _load_evidence(args)
    except Exception as e:
        emit_event("evidence_load", EXIT_SAFARI_FAIL, f"--evidence-file 读取失败: {e}")
        return EXIT_SAFARI_FAIL

    signature = args.signature or None
    if signature:
        try:
            check_circuit_breaker(signature)
        except CircuitOpenError as e:
            emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN, str(e), signature=signature)
            return EXIT_CIRCUIT_OPEN

    try:
        with TargetTabLock(args.target_url):
            return _main_locked(args, raw_prompt, signature, evidence_body)
    except TargetTabBusyError as e:
        emit_event("target_busy", EXIT_TARGET_BUSY, f"目标 Tab 锁竞争失败: {e}", target_url=args.target_url)
        return EXIT_TARGET_BUSY


def _main_locked(args, raw_prompt: str, signature: Optional[str], evidence_body: Optional[str]) -> int:
    client = BSKClient(browser_profile=args.browser_profile or None)

    # 格式化 Payload
    payload = format_evidence_payload(
        args.type, raw_prompt, evidence_body, level=args.level, cwd=args.cwd
    )

    try:
        exit_code, answer = send_and_receive_bsk_chatgpt(
            client=client,
            prompt=payload,
            target_url=args.target_url,
            wait_timeout=args.timeout,
            allow_borrow=bool(args.borrow and not args.no_borrow),
        )
        if exit_code == EXIT_OK:
            if signature:
                record_success(signature)
        else:
            if signature and exit_code in (EXIT_TIMEOUT_EMPTY, EXIT_SAFARI_FAIL, EXIT_SUBMIT_FAIL, EXIT_NO_NEW_TURN):
                record_failure(signature)

        if answer:
            print(answer)
        return exit_code
    except Exception as e:
        if signature:
            record_failure(signature)
        emit_event("fatal", EXIT_SAFARI_FAIL, f"未预期异常 ({type(e).__name__}): {e}")
        return EXIT_SAFARI_FAIL
    finally:
        client.stop_session()


if __name__ == "__main__":
    sys.exit(main())
