#!/usr/bin/env python3
"""
Chrome ChatGPT Cognitive-Control Bridge (v4.1 Evidence-Integrity, CDP)
---------------------------------------------------------------------
Safari 版本的 Chrome DevTools Protocol 兄弟实现。所有 P0/P1/P2 契约与
safari_chatgpt.py 完全等价：

  - 同一组退出码 / 同一组事件 JSONL schema / 同一组熔断器 + TargetTabLock
  - 同一套 sanitize / signature normalize / git context
  - 同一套 P0-1 (精确 user-message identity) / P0-2 / P0-4

仅替换浏览器侧执行通道：
  Safari : osascript  → tell Safari / do JavaScript
  Chrome : Chrome DevTools Protocol (HTTP /json/list + WS Runtime.evaluate)

前置条件：
  启动 Chrome 并打开目标 ChatGPT Tab 后，执行：
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
        --remote-debugging-port=9222 --remote-allow-origins=* \
        https://chatgpt.com/c/<conversation-uuid>

调用范例见 SKILL.md。
"""

import sys
import os
import re
import time
import json
import fcntl
import socket
import tempfile
import struct
import urllib.request
import urllib.error
import urllib.parse
import base64
import hashlib
import secrets
import argparse
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse

# =============================================================================
# 退出码常量 (Verification Plane 唯一判据)
# =============================================================================
EXIT_OK              = 0
EXIT_TIMEOUT_PARTIAL = 2
EXIT_TIMEOUT_EMPTY   = 3
EXIT_SAFARI_FAIL     = 4   # 复用名称：通用浏览器通道失败（CDP 也走这个码）
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
CIRCUIT_STATE_FILE   = "/tmp/chrome_chatgpt_circuit_breaker.json"
CIRCUIT_WINDOW_SEC   = 3600
CIRCUIT_MAX_RETRIES  = 3

CDP_HTTP_TIMEOUT_SEC = 5       # 单次 HTTP /json/list /json/version 超时
CDP_RPC_TIMEOUT_SEC  = 30      # 单次 Runtime.evaluate 超时（与 Safari 对齐）
CDP_RETRY_ATTEMPTS   = 2       # 单次 RPC 失败重试次数（自动重连）
CDP_VERSION_CACHE    = "/tmp/chrome_chatgpt_cdp_version.json"  # 缓存 lastTargetId

SUBMIT_PHASE_BUDGET = 20
TURN_PHASE_BUDGET   = 60
STABLE_PHASE_MIN    = 5

GIT_SUBPROCESS_TIMEOUT_SEC = 5


# =============================================================================
# Secret 脱敏 (与 safari_chatgpt.py 100% 对齐)
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

_REDACT_EMAIL = os.environ.get("CHROME_BRIDGE_REDACT_EMAIL", "0") == "1"
_REDACT_PHONE = os.environ.get("CHROME_BRIDGE_REDACT_PHONE", "0") == "1"
_EMAIL_RE     = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_RE     = re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)')


def sanitize_text(text: str) -> str:
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


def normalize_failure_signature(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
                  '<UUID>', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(17\d{8}|20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\b',
                  '<TIMESTAMP>', text)
    text = re.sub(r'/(?:tmp|var/folders)/[^\s:\'",]+', '<TEMP_PATH>', text)
    text = re.sub(r'\b(?:pid|PID)\s*[:=]?\s*\d+\b', '<PID>', text)
    text = re.sub(r'\bline\s+\d+\b', '<LINE>', text)
    return text.strip()


# =============================================================================
# 结构化事件输出
# =============================================================================
def _sanitize_event_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_event_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_event_value(v) for v in value]
    return value


_BROWSER_NAME: Optional[str] = None  # 由 CLI --browser-name 注入，用于事件元数据


def emit_event(stage: str, exit_code: int, message: str, **extra) -> None:
    payload: Dict[str, Any] = {
        "ts": time.time(),
        "stage": stage,
        "exit_code": exit_code,
        "exit_name": EXIT_CODE_NAME.get(exit_code, "UNKNOWN"),
        "message": message,
        "browser": _BROWSER_NAME or "chrome",
    }
    payload.update(extra)
    payload = _sanitize_event_value(payload)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


# =============================================================================
# target_url 校验 (与 safari 版 100% 对齐)
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


# =============================================================================
# 熔断器 (滑动窗口 + 原子写 + 文件锁)
# =============================================================================
class CircuitOpenError(RuntimeError):
    pass


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
        "w", dir=os.path.dirname(CIRCUIT_STATE_FILE) or "/tmp",
        prefix=".circuit.", suffix=".tmp", delete=False
    ) as tf:
        json.dump(state, tf, ensure_ascii=False)
        tmp_name = tf.name
    os.replace(tmp_name, CIRCUIT_STATE_FILE)


def _prune_state_unlocked(state: Dict[str, Any], now: float) -> Dict[str, Any]:
    pruned: Dict[str, Any] = {}
    for k, v in state.items():
        if isinstance(v, list):
            kept = [t for t in v
                    if isinstance(t, (int, float)) and (now - t) <= CIRCUIT_WINDOW_SEC]
            if kept:
                pruned[k] = kept
        else:
            continue
    return pruned


class _CircuitLock:
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
    if not failure_signature:
        return True
    if normalize:
        failure_signature = hashlib.sha256(
            normalize_failure_signature(failure_signature).encode("utf-8")
        ).hexdigest()[:16]

    now = time.time()
    with _CircuitLock():
        state = _read_circuit_state_unlocked()
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
            f"请排查后运行：python3 chrome_chatgpt.py --reset-circuit"
        )
    return True


def reset_circuit_breaker() -> int:
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
# TargetTabLock (跨进程对同一 target_url 加事务锁)
# =============================================================================
class TargetTabBusyError(RuntimeError):
    pass


class TargetTabLock:
    def __init__(self, target_url: str):
        digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:24]
        self.path = f"/tmp/chrome_chatgpt_tab_{digest}.lock"
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
        return False


# =============================================================================
# Git 上下文（与 safari 版同实现）
# =============================================================================
def get_git_head_context(cwd: Optional[str] = None) -> str:
    try:
        sha_res = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=cwd,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
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
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return f"Git: {sha} (unavailable)"
    if status_res.returncode != 0:
        return f"Git: {sha} (unavailable)"
    dirty = " (dirty)" if status_res.stdout.strip() else " (clean)"
    return f"Git: {sha}{dirty}"


# =============================================================================
# Chrome DevTools Protocol 客户端 (纯标准库)
# =============================================================================
class CDPError(RuntimeError):
    pass


class NoTargetTabError(CDPError):
    pass


class AmbiguousTargetTabError(CDPError):
    pass


def _http_get_json(host: str, port: int, path: str,
                   timeout: int = CDP_HTTP_TIMEOUT_SEC) -> Any:
    """对 Chrome DevTools HTTP endpoint（127.0.0.1）做 GET，返回 parsed JSON。"""
    req = urllib.request.Request(f"http://{host}:{port}{path}",
                                 headers={"Host": "localhost",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError) as e:
        raise CDPError(f"CDP HTTP 失败 ({path}): {e}") from e
    except json.JSONDecodeError as e:
        raise CDPError(f"CDP HTTP {path} 返回非 JSON: {e}") from e


# ----- WebSocket 帧 (RFC 6455) —— 仅实现 server→client / client→server 文本帧 -----
WS_OPCODE_TEXT = 0x1
WS_OPCODE_CLOSE = 0x8
WS_OPCODE_PING = 0x9
WS_OPCODE_PONG = 0xA


def _ws_send_text(sock: socket.socket, payload: bytes) -> None:
    header = bytearray()
    header.append(0x80 | WS_OPCODE_TEXT)  # FIN + text
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)            # MASK + 7-bit len
    elif n < (1 << 16):
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", n))
    mask = secrets.token_bytes(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise CDPError("WebSocket 远端关闭")
        buf.extend(chunk)
    return bytes(buf)


def _ws_recv_text(sock: socket.socket, timeout: float) -> bytes:
    sock.settimeout(timeout)
    try:
        # First byte
        b1 = sock.recv(1)
        if not b1:
            raise CDPError("WebSocket 头部读取失败")
        b1 = b1[0]
        opcode = b1 & 0x0F
        if opcode == WS_OPCODE_CLOSE:
            raise CDPError("WebSocket 远端主动关闭")
        if opcode == WS_OPCODE_PING:
            # 回 pong 并重读下一帧
            sock.sendall(bytes([0x8A]))
            return _ws_recv_text(sock, timeout)
        if opcode != WS_OPCODE_TEXT:
            # 跳过 continuation / binary 等非文本帧
            b2 = sock.recv(1)[0]
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                _ws_recv_exact(sock, 2)
            elif length == 127:
                _ws_recv_exact(sock, 8)
            if masked:
                _ws_recv_exact(sock, 4)
            # 简化：不读取 payload，仅消耗头部
            return _ws_recv_text(sock, timeout)

        # 文本帧：读第二字节 + length + payload
        b2 = sock.recv(1)[0]
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", _ws_recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _ws_recv_exact(sock, 8))[0]
        if masked:
            mask = _ws_recv_exact(sock, 4)
        else:
            mask = b""
        payload = _ws_recv_exact(sock, length)
        if mask:
            payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
        return payload
    except socket.timeout as e:
        raise CDPError(f"WebSocket 读取超时（>{timeout:.1f}s）") from e


def _ws_handshake(host: str, port: int, path: str,
                  timeout: int = CDP_HTTP_TIMEOUT_SEC) -> socket.socket:
    """完成 WebSocket Upgrade，返回已可读写的 socket。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode("ascii"))
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise CDPError("WebSocket 握手失败：连接提前关闭")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    if " 101 " not in status_line:
        raise CDPError(f"WebSocket 握手被拒：{status_line}")
    # 任何已缓冲的额外字节丢弃（CDP server handshake 一般无 body）
    _ = rest
    return sock


class ChromeCDPClient:
    """单进程对单个 Tab 的 CDP 客户端。
    设计要点：
      - 长连接 WS，命令以递增 id 关联
      - 单 worker 收包：所有 response / event 都进 self._rx 队列（按 id 路由）
      - 暂不需要订阅 events，但保留 Runtime.enable 上下文以保证 execute 正常
    """

    def __init__(self, host: str, port: int, target_id: str,
                 ws_url: Optional[str] = None):
        self.host = host
        self.port = port
        self.target_id = target_id
        self._next_id = 1
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._sock: Optional[socket.socket] = None
        self._rx_thread: Optional[Any] = None
        self._closed = False
        if ws_url:
            self._connect_ws_url(ws_url)
        else:
            self._connect_via_target_id()

    # ---- connect via webSocketDebuggerUrl (preferred) ----
    def _connect_ws_url(self, ws_url: str) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        if parsed.scheme != "ws":
            raise CDPError(f"webSocketDebuggerUrl 必须 ws://，got {parsed.scheme}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222
        path = parsed.path or "/"
        self._sock = _ws_handshake(host, port, path)
        self._start_reader()

    # ---- connect via /json/version + /json/list ----
    def _connect_via_target_id(self) -> None:
        # /json/version 给 host:port（通常 127.0.0.1:9222）
        ver = _http_get_json(self.host, self.port, "/json/version")
        ws_host = ver.get("host", "127.0.0.1")
        # CDP /json/list 列出所有 tab；找到 targetId 匹配的 webSocketDebuggerUrl
        targets = _http_get_json(ws_host, self.port, "/json/list")
        ws_url: Optional[str] = None
        for t in targets:
            if t.get("id") == self.target_id:
                ws_url = t.get("webSocketDebuggerUrl")
                break
        if not ws_url:
            raise NoTargetTabError(f"在 /json/list 中找不到 targetId={self.target_id}")
        self._connect_ws_url(ws_url)

    def _start_reader(self) -> None:
        import threading
        def _loop():
            assert self._sock is not None
            sock = self._sock
            try:
                while not self._closed:
                    payload = _ws_recv_text(sock, timeout=CDP_RPC_TIMEOUT_SEC + 5)
                    try:
                        msg = json.loads(payload.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    msg_id = msg.get("id")
                    if isinstance(msg_id, int) and msg_id in self._pending:
                        ev, _ = self._pending.pop(msg_id)
                        ev["msg"] = msg
                        ev["set"]()
                    # events 暂不消费
            except Exception:
                # 失败时唤醒所有等待者
                for ev in self._pending.values():
                    try:
                        ev["msg"] = {"error": {"message": "WebSocket reader crashed"}}
                        ev["set"]()
                    except Exception:
                        pass

        self._rx_thread = threading.Thread(target=_loop, daemon=True)
        self._rx_thread.start()

    def send_command(self, method: str, params: Optional[Dict[str, Any]] = None,
                     timeout: float = CDP_RPC_TIMEOUT_SEC,
                     attempts: int = CDP_RETRY_ATTEMPTS) -> Dict[str, Any]:
        """单条命令。失败时尝试自动重连一次（attempts=2 即重试 1 次）。"""
        last_err: Optional[Exception] = None
        for i in range(attempts):
            try:
                return self._send_command_once(method, params, timeout)
            except Exception as e:
                last_err = e
                if i + 1 < attempts:
                    try:
                        self._reconnect()
                    except Exception:
                        pass
        raise CDPError(f"CDP 命令 {method} 失败（重试 {attempts-1} 次）: {last_err}")

    def _send_command_once(self, method: str, params: Optional[Dict[str, Any]],
                           timeout: float) -> Dict[str, Any]:
        if self._closed or self._sock is None:
            raise CDPError("CDP 连接已关闭")
        msg_id = self._next_id
        self._next_id += 1
        frame = {"id": msg_id, "method": method, "params": params or {}}
        import threading
        ev: Dict[str, Any] = {"set": threading.Event(), "msg": None}
        self._pending[msg_id] = ev
        try:
            _ws_send_text(self._sock, json.dumps(frame).encode("utf-8"))
        except Exception as e:
            self._pending.pop(msg_id, None)
            raise CDPError(f"CDP 发送 {method} 失败: {e}") from e
        if not ev["set"].wait(timeout=timeout):
            self._pending.pop(msg_id, None)
            raise CDPError(f"CDP {method} 超时（>{timeout:.1f}s）")
        msg = ev["msg"] or {}
        if "error" in msg:
            raise CDPError(f"CDP {method} 错误: {msg['error']}")
        if "result" not in msg:
            raise CDPError(f"CDP {method} 返回异常: {msg}")
        return msg["result"]

    def _reconnect(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._closed = False
        self._connect_via_target_id()

    def close(self) -> None:
        self._closed = True
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None


# =============================================================================
# Tab 定位：HTTP /json/list 拉所有 page 类型，按 URL 过滤
# =============================================================================
def _url_matches(target_url: str, candidate_url: str) -> bool:
    """与 Safari 版相同语义：startswith base_url 或 contains 会话 UUID。"""
    if not candidate_url:
        return False
    base = target_url.rstrip('?#/ ').split('?')[0]
    if candidate_url.startswith(base):
        return True
    uuid_match = re.search(
        r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        target_url,
    )
    if uuid_match and uuid_match.group(0) in candidate_url:
        return True
    return False


def find_target_tab(host: str, port: int, target_url: str) -> Tuple[str, str]:
    """返回 (targetId, webSocketDebuggerUrl)。多个匹配抛 AmbiguousTargetTabError。"""
    targets = _http_get_json(host, port, "/json/list")
    matched = [t for t in targets
               if t.get("type") == "page" and _url_matches(target_url, t.get("url", ""))]
    if len(matched) == 0:
        raise NoTargetTabError(f"未在 Chrome /json/list 中找到 URL 匹配的 page: {target_url}")
    if len(matched) > 1:
        raise AmbiguousTargetTabError(
            f"URL 匹配命中 {len(matched)} 个 Tab（歧义）："
            f"{[t.get('url', '') for t in matched]}"
        )
    t = matched[0]
    tid = t.get("id")
    ws_url = t.get("webSocketDebuggerUrl")
    if not (tid and ws_url):
        raise NoTargetTabError(f"Tab 缺少 id / webSocketDebuggerUrl: {t}")
    return tid, ws_url


# =============================================================================
# JS 执行 (CDP Runtime.evaluate)
# =============================================================================
def execute_chrome_js(client: ChromeCDPClient, js_code: str,
                      timeout: int = CDP_RPC_TIMEOUT_SEC) -> str:
    """执行 JS 并把 JSON.stringify 后返回值以 string 形式返回。
    非 string 类型的返回值会被 repr() 包一层，提示调用方检查。
    """
    res = client.send_command(
        "Runtime.evaluate",
        {
            "expression": js_code,
            "returnByValue": True,
            "awaitPromise": False,
            "userGesture": True,   # 避免弹窗拦截
            "timeout": timeout * 1000,
        },
        timeout=timeout,
    )
    if "exceptionDetails" in res:
        exc = res["exceptionDetails"]
        text = exc.get("text", "")
        exc_obj = exc.get("exception") or {}
        desc = exc_obj.get("description") or exc_obj.get("value") or ""
        raise CDPError(f"CDP Runtime.evaluate 异常: {text} | {desc}")

    remote = res.get("result") or {}
    rtype = remote.get("type")
    if rtype == "string":
        return remote.get("value", "")
    if rtype == "undefined":
        return ""
    # object / number / boolean：序列化为 JSON 字符串
    try:
        return json.dumps(remote.get("value"), ensure_ascii=False)
    except (TypeError, ValueError):
        return remote.get("description") or json.dumps(remote)


# =============================================================================
# 基线 / 提交验证 / 稳定等待 (JS 模板与 Safari 版完全一致)
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
    return JSON.stringify({
        totalCount: all.length,
        userCount: usr.length,
        assistantCount: asst.length,
        lastUserText: lastU ? (lastU.innerText || "").trim() : "",
        lastAsstText: lastA ? (lastA.innerText || "").trim() : "",
        lastUserMessageId: midOf(lastU),
        lastAsstMessageId: midOf(lastA),
    });
})()
"""


def _last_message_id_js(role: str) -> str:
    return f"""
    (() => {{
        const nodes = document.querySelectorAll("[data-message-author-role='{role}']");
        const last = nodes.length > 0 ? nodes[nodes.length - 1] : null;
        if (!last) return JSON.stringify({{id: null, count: 0, textLen: 0}});
        const id =
            last.getAttribute("data-message-id") ||
            (last.closest && last.closest("[data-message-id]")
                ? last.closest("[data-message-id]").getAttribute("data-message-id")
                : null);
        return JSON.stringify({{
            id: id,
            count: nodes.length,
            textLen: (last.innerText || "").trim().length,
            fp: ((last.innerText || "").trim()).slice(0, 80),
        }});
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
                .trim();
        const users = document.querySelectorAll("[data-message-author-role='user']");
        const last  = users.length > 0 ? users[users.length - 1] : null;
        const text  = last ? norm(last.innerText || "") : "";
        const id =
            last ? (last.getAttribute("data-message-id") ||
                (last.closest && last.closest("[data-message-id]")
                    ? last.closest("[data-message-id]").getAttribute("data-message-id")
                    : null)) : null;
        return JSON.stringify({{
            userCount: users.length,
            matchesExpected: text === norm({expected_literal}),
            textLen: text.length,
            messageId: id,
        }});
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
                .trim();
        const el = document.querySelector("#prompt-textarea") ||
                   document.querySelector("form [contenteditable='true']") ||
                   document.querySelector("form");
        if (!el) return JSON.stringify({{ok: false, reason: "NO_INPUT"}});
        const actual = (el.innerText || el.textContent || el.value || "");
        const normActual = norm(actual);
        return JSON.stringify({{
            ok: true,
            matchesExpected: normActual === norm({expected_literal}),
            textLen: normActual.length,
            visible: !!(el.offsetWidth || el.offsetHeight ||
                       (el.getClientRects && el.getClientRects().length)),
            isContentEditable: !!el.isContentEditable,
        }});
    }})()
    """


def _stable_poll_js(target_message_id: Optional[str]) -> str:
    safe_msg_id = target_message_id.replace("'", "") if target_message_id else ""
    query_part = f'document.querySelector("[data-message-id=\'{safe_msg_id}\']")' if target_message_id else 'null'
    return f"""
    (() => {{
        const stopBtn = document.querySelector(
            "button[data-testid='stop-button']") ||
            document.querySelector("button[aria-label='停止回答']");
        let node = {query_part};
        const asst = document.querySelectorAll("[data-message-author-role='assistant']");
        const last = asst.length > 0 ? asst[asst.length - 1] : null;
        if (!node && last) {{
            node = last;
        }}
        if (!node) return JSON.stringify({{
            targetPresent: false, isStreaming: !!stopBtn, text: "",
            messageId: null
        }});
        return JSON.stringify({{
            targetPresent: true,
            isStreaming: !!stopBtn,
            text: (node.innerText || "").trim(),
            messageId: node.getAttribute("data-message-id") ||
                (node.closest && node.closest("[data-message-id]")
                    ? node.closest("[data-message-id]").getAttribute("data-message-id")
                    : null),
        }});
    }})()
    """


def capture_baseline(client: ChromeCDPClient) -> Dict[str, Any]:
    try:
        raw = execute_chrome_js(client, _BASELINE_JS)
    except CDPError as e:
        raise CDPError(f"基线阶段 CDP 调用失败: {e}") from e
    try:
        b = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CDPError(f"基线 JSON 解析失败: {e}; raw={raw[:200]}") from e
    if not isinstance(b, dict) or "totalCount" not in b or "userCount" not in b:
        raise CDPError(f"基线字段缺失或类型错误: {b}")
    return b


def _fetch_snapshot(client: ChromeCDPClient,
                    js_override: Optional[str] = None) -> Dict[str, Any]:
    js = js_override if js_override is not None else _BASELINE_JS
    raw = execute_chrome_js(client, js)
    try:
        snap = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CDPError(f"snapshot JSON 解析失败: {e}; raw={raw[:200]}") from e
    if not isinstance(snap, dict):
        raise CDPError(f"snapshot 类型错误: {type(snap).__name__}")
    return snap


def dispatch_cdp_enter_key(client: ChromeCDPClient) -> None:
    client.send_command("Input.dispatchKeyEvent", {
        "type": "rawKeyDown",
        "windowsVirtualKeyCode": 13,
        "nativeVirtualKeyCode": 13,
        "macCharCode": 13,
        "unmodifiedText": "\r",
        "text": "\r",
        "key": "Enter",
        "code": "Enter"
    })
    try:
        client.send_command("Input.dispatchKeyEvent", {
            "type": "char",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
            "macCharCode": 13,
            "unmodifiedText": "\r",
            "text": "\r",
            "key": "Enter",
            "code": "Enter"
        })
    except Exception:
        pass
    client.send_command("Input.dispatchKeyEvent", {
        "type": "keyUp",
        "windowsVirtualKeyCode": 13,
        "nativeVirtualKeyCode": 13,
        "macCharCode": 13,
        "unmodifiedText": "\r",
        "text": "\r",
        "key": "Enter",
        "code": "Enter"
    })


def wait_for_user_message_committed(client: ChromeCDPClient,
                                    baseline_user_count: int,
                                    expected_prompt: str,
                                    timeout: float) -> Dict[str, Any]:
    probe_js = _user_commit_probe_js(expected_prompt)
    deadline = time.monotonic() + timeout
    last_snapshot: Optional[Dict[str, Any]] = None
    attempts = 0
    while time.monotonic() < deadline:
        snap = _fetch_snapshot(client, js_override=probe_js)
        last_snapshot = snap
        if (snap.get("userCount") == baseline_user_count + 1
                and snap.get("matchesExpected") is True):
            return snap
        attempts += 1
        if attempts in (3, 6):
            try:
                execute_chrome_js(client, """(() => {
                    const el = document.querySelector('#prompt-textarea') || document.querySelector("form [contenteditable='true']");
                    if (el) {
                        el.focus();
                        el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                    }
                    const btn = document.querySelector('button[data-testid="send-button"]') || document.querySelector("#composer-submit-button");
                    if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') {
                        btn.click();
                    }
                })()""")
                dispatch_cdp_enter_key(client)
            except Exception:
                pass
        time.sleep(0.4)
    raise CDPError(f"用户消息提交超时（>{timeout:.1f}s）。最终快照={last_snapshot}")


def verify_composer(client: ChromeCDPClient,
                    expected_prompt: str, timeout: float) -> Dict[str, Any]:
    js = _inject_verify_js(expected_prompt)
    deadline = time.monotonic() + timeout
    last: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        snap = _fetch_snapshot(client, js_override=js)
        last = snap
        if snap.get("ok") is True and snap.get("matchesExpected") is True:
            return snap
        time.sleep(0.3)
    raise CDPError(f"Composer 注入验证超时（>{timeout:.1f}s）。最终快照={last}")


def wait_for_assistant_new_turn(client: ChromeCDPClient,
                                baseline_assistant_count: int,
                                timeout: float) -> Dict[str, Any]:
    js = _last_message_id_js("assistant")
    deadline = time.monotonic() + timeout
    last_snapshot: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        snap = _fetch_snapshot(client, js_override=js)
        last_snapshot = snap
        if (snap.get("count") == baseline_assistant_count + 1
                and snap.get("textLen", 0) > 0):
            return snap
        time.sleep(0.4)
    raise CDPError(f"助手新回合未产生（>{timeout:.1f}s）。最终快照={last_snapshot}")


def wait_for_assistant_stable(client: ChromeCDPClient,
                              target_message_id: Optional[str],
                              wait_timeout: float) -> Tuple[str, bool]:
    js = _stable_poll_js(target_message_id)
    start = time.monotonic()
    last_text = ""
    stable_count = 0
    while time.monotonic() - start < wait_timeout:
        snap = _fetch_snapshot(client, js_override=js)
        if not snap.get("targetPresent", False):
            raise CDPError(
                f"目标 assistant turn（id={target_message_id!r}）节点已消失，"
                f"证据失效。最终快照={snap}"
            )
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
# Payload 格式化（与 safari 版完全一致，仅元数据 browser=chrome）
# =============================================================================
def format_evidence_payload(task_type: str, context_text: str,
                            evidence_data: Optional[str] = None,
                            level: str = "L1",
                            cwd: Optional[str] = None) -> str:
    git_ctx = get_git_head_context(cwd)
    context_text = sanitize_text(context_text)
    evidence_data = sanitize_text(evidence_data or "")

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
        return f"【ARCHITECTURAL_PLAN_REQUEST】\n[Local State]: {git_ctx}\n[Browser]: chrome\n[Target Scope]:\n{context_text}\n\n请输出结构化架构决策及需要本地 Agent 执行的原子操作建议。"
    elif task_type == "feedback":
        return f"【EXECUTION_EVIDENCE_FEEDBACK】\n[Local State]: {git_ctx}\n[Browser]: chrome\n[Execution Context]:\n{context_text}\n\n[Evidence ({level})]:\n{evidence_snippet}\n\n请基于上述事实分析原因并给出自愈修复补丁。"
    elif task_type == "review":
        return f"【CODE_AND_ARCHITECTURE_REVIEW】\n[Local State]: {git_ctx}\n[Browser]: chrome\n[Review Target]:\n{context_text}\n\n请从系统解耦、安全性、边界与性能给出评审意见。"
    elif task_type == "task-code":
        return f"""【TASK_CODE_IMPLEMENTATION】\n[Local State]: {git_ctx}\n[Browser]: chrome\n[Task to Implement]:\n{context_text}\n\n请完成此任务的代码实现并直接推送到 GitHub 目标分支。\n\n【输出格式要求】（严格遵守，否则无法解析）：\n1. 代码直接使用 GitHub 直连工具在远端分支修改并提交。\n2. 禁止在回复中粘贴代码全文。\n3. 测试命令用以下格式（放在单独的 bash 块中）：\n   `TEST: <实际命令>`\n   `EXPECTED: <预期结果描述>`\n4. 如果任务涉及多文件，请按依赖顺序排列。\n5. 只测试命令，不要输出代码，不要写说明文字。"""
    elif task_type == "task-review":
        return f"【TASK_CODE_REVIEW】\n[Local State]: {git_ctx}\n[Browser]: chrome\n[Task Description]:\n{context_text}\n\n[Evidence ({level})]:\n{evidence_snippet}\n\n请基于上述代码和测试结果做出裁决。只输出以下三种格式之一，不得输出其他内容：\n  APPROVED  — 代码符合任务要求，测试全部通过。\n  NEEDS_FIX — 代码有问题，测试失败或不符合要求。请明确说明：\n              (1) 失败原因\n              (2) 需要修改的文件和具体修改方案\n  BLOCKED   — 任务依赖前置条件未满足（如缺少依赖、配置错误等）。请说明阻塞原因。\n\n【注意】请严格只输出 APPROVED / NEEDS_FIX(...)/ BLOCKED(...) 其一，不要写其他文字。"
    return context_text


# =============================================================================
# 主流：与 safari 版一一对应
# =============================================================================
def send_and_receive_chrome_chatgpt(client: ChromeCDPClient, prompt: str,
                                    target_url: str,
                                    wait_timeout: int = 180,
                                    submit_deadline: int = SUBMIT_PHASE_BUDGET,
                                    turn_deadline: Optional[int] = None
                                    ) -> Tuple[int, str]:
    if turn_deadline is None:
        turn_deadline = max(45, min(90, wait_timeout // 2))

    overall_deadline = time.monotonic() + wait_timeout

    def remaining() -> float:
        return max(0.0, overall_deadline - time.monotonic())

    emit_event("start", EXIT_OK,
               "Chrome ChatGPT Cognitive-Control Bridge v4.1 (CDP) 启动",
               target_url=target_url, wait_timeout=wait_timeout,
               cdp_host=client.host, cdp_port=client.port,
               targetId=client.target_id)

    # ---- Step 1：基线 ----
    try:
        baseline = capture_baseline(client)
    except NoTargetTabError as e:
        emit_event("baseline", EXIT_NO_TAB, f"目标 Tab 不存在: {e}", target_url=target_url)
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("baseline", EXIT_BASELINE_FAIL, f"基线采集失败: {e}")
        return EXIT_BASELINE_FAIL, ""

    emit_event("baseline", EXIT_OK, "基线采集成功",
               totalCount=baseline.get("totalCount"),
               userCount=baseline.get("userCount"),
               assistantCount=baseline.get("assistantCount"),
               lastUserMessageId=baseline.get("lastUserMessageId"))

    # ---- Step 2：注入 prompt ----
    js_inject = f"""
    (() => {{
        const el = document.querySelector('#prompt-textarea') ||
                   document.querySelector("form [contenteditable='true']") ||
                   document.querySelector("form");
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
        res = execute_chrome_js(client, js_inject)
    except NoTargetTabError as e:
        emit_event("inject", EXIT_NO_TAB, f"目标 Tab 在注入阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框注入失败: {e}")
        return EXIT_SAFARI_FAIL, ""
    if res != "OK":
        emit_event("inject", EXIT_SAFARI_FAIL, f"输入框定位失败: {res}")
        return EXIT_SAFARI_FAIL, ""

    # ---- Step 2.5：Composer Verify ----
    cv_budget = min(5.0, submit_deadline, remaining())
    if cv_budget <= 0:
        emit_event("composer_verify", EXIT_TIMEOUT_EMPTY, "无预算执行 composer 验证")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        cv_snap = verify_composer(client, prompt, cv_budget)
    except CDPError as e:
        emit_event("composer_verify", EXIT_SUBMIT_FAIL,
                   f"Composer 注入未生效: {e}")
        return EXIT_SUBMIT_FAIL, ""
    emit_event("composer_verify", EXIT_OK,
               "Composer 内容已确认为 expected prompt",
               textLen=cv_snap.get("textLen"),
               visible=cv_snap.get("visible"),
               isContentEditable=cv_snap.get("isContentEditable"))

    # 等待发送按钮进入就绪状态（最多 2s）
    btn_ready_js = """
    (() => {
        const btn = document.querySelector('button[data-testid="send-button"]') ||
                    document.querySelector('#composer-submit-button') ||
                    document.querySelector("button[aria-label*='Send']") ||
                    document.querySelector("button[aria-label*='发送']");
        return !!btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true';
    })()
    """
    btn_deadline = time.monotonic() + 2.0
    while time.monotonic() < btn_deadline:
        try:
            if execute_chrome_js(client, btn_ready_js) is True:
                break
        except Exception:
            pass
        time.sleep(0.1)

    # ---- Step 3：触发发送（CDP 原生硬件回车键 + 按钮点击双重保障） ----
    send_res = "NONE"
    try:
        execute_chrome_js(client, """(() => {
            const el = document.querySelector('#prompt-textarea') || document.querySelector("form [contenteditable='true']");
            if (el) el.focus();
            return true;
        })()""")
        time.sleep(0.1)

        # 核心物理触发 1：向浏览器内核发送真正的物理回车键事件 (Enter KeyDown + KeyUp)
        dispatch_cdp_enter_key(client)
        send_res = "CDP_KEY_ENTER"

        time.sleep(0.2)

        # 核心物理触发 2：若发送按钮已处于激活状态，同时执行真实点击双重兜底
        click_js = """(() => {
            const btn = document.querySelector('button[data-testid="send-button"]') ||
                        document.querySelector('#composer-submit-button') ||
                        document.querySelector("button[aria-label*='Send']") ||
                        document.querySelector("button[aria-label*='发送']") ||
                        document.querySelector("form button[type='submit']");
            if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') {
                btn.click();
                return "CLICKED";
            }
            return btn ? "DISABLED" : "NO_BUTTON";
        })()"""
        click_res = execute_chrome_js(client, click_js)
        if click_res == "CLICKED":
            send_res += "+BTN_CLICK"
        emit_event("send", EXIT_OK, f"发送触发结果: {send_res}")
    except NoTargetTabError as e:
        emit_event("send", EXIT_NO_TAB, f"目标 Tab 在发送阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("send", EXIT_SAFARI_FAIL, f"发送触发失败: {e}")
        return EXIT_SAFARI_FAIL, ""

    # ---- Step 4：精确 user-message identity 验证 ----
    sub_budget = min(submit_deadline, remaining())
    if sub_budget <= 0:
        emit_event("submit_verify", EXIT_TIMEOUT_EMPTY, "无预算执行提交验证")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        user_snap = wait_for_user_message_committed(
            client=client,
            baseline_user_count=baseline["userCount"],
            expected_prompt=prompt,
            timeout=sub_budget,
        )
    except NoTargetTabError as e:
        emit_event("submit_verify", EXIT_NO_TAB, f"目标 Tab 在提交验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("submit_verify", EXIT_SUBMIT_FAIL,
                   f"用户消息未真正提交（Enter/Click 未生效或内容不匹配）: {e}")
        return EXIT_SUBMIT_FAIL, ""

    emit_event("submit_verify", EXIT_OK,
               "用户消息已提交（exact prompt match）",
               newUserCount=user_snap.get("userCount"),
               newUserMessageId=user_snap.get("messageId"),
               newTextLen=user_snap.get("textLen"))

    # ---- Step 5：等待助手新回合 ----
    turn_budget = min(turn_deadline, remaining())
    if turn_budget <= 0:
        emit_event("new_turn", EXIT_TIMEOUT_EMPTY, "无预算等待助手新回合")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        turn_snap = wait_for_assistant_new_turn(
            client=client,
            baseline_assistant_count=baseline["assistantCount"],
            timeout=turn_budget,
        )
    except NoTargetTabError as e:
        emit_event("new_turn", EXIT_NO_TAB, f"目标 Tab 在回合验证阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("new_turn", EXIT_NO_NEW_TURN, f"助手新回合未产生: {e}")
        return EXIT_NO_NEW_TURN, ""

    target_message_id = turn_snap.get("id")
    emit_event("new_turn", EXIT_OK,
               "助手新回合已开始",
               newAssistantCount=turn_snap.get("count"),
               messageId=target_message_id,
               firstTextLen=turn_snap.get("textLen"))

    # ---- Step 6：稳定等待 ----
    stable_budget = max(STABLE_PHASE_MIN, remaining())
    if stable_budget <= 0:
        emit_event("stable", EXIT_TIMEOUT_EMPTY, "无预算执行稳定等待")
        return EXIT_TIMEOUT_EMPTY, ""
    try:
        text, complete = wait_for_assistant_stable(
            client=client,
            target_message_id=target_message_id,
            wait_timeout=stable_budget,
        )
    except NoTargetTabError as e:
        emit_event("stable", EXIT_NO_TAB, f"目标 Tab 在稳定等待阶段丢失: {e}")
        return EXIT_NO_TAB, ""
    except CDPError as e:
        emit_event("stable", EXIT_SAFARI_FAIL,
                   f"稳定等待期间目标 turn 消失或 CDP 异常: {e}")
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
# CLI
# =============================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Chrome ChatGPT Cognitive-Control Bridge v4.1 (CDP, Evidence-Integrity)"
    )
    p.add_argument("--prompt", type=str, required=True, help="提示词或上下文")
    p.add_argument("--target-url", type=str, required=True,
                   help="目标 ChatGPT 会话的完整 URL（必须 https://chatgpt.com）")
    p.add_argument("--chrome-port", type=int, default=9222,
                   help="Chrome --remote-debugging-port（默认 9222）")
    p.add_argument("--chrome-host", type=str, default="127.0.0.1",
                   help="Chrome remote debugging host（默认 127.0.0.1）")
    p.add_argument("--browser-name", type=str, default=None,
                   help="事件 JSONL 中的 browser 字段值（默认 'chrome'；Edge 用户填 'edge'，"
                        "Brave 填 'brave'，Arc 填 'arc' 等）。不影响 CDP 连接，仅用于下游审计区分来源。")

    p.add_argument("--type", type=str, default="raw",
                   choices=["raw", "plan", "feedback", "review", "task-code", "task-review"],
                   help="交互协议类型")
    evidence_group = p.add_mutually_exclusive_group()
    evidence_group.add_argument("--evidence", type=str, default=None)
    evidence_group.add_argument("--evidence-file", type=str, default=None)
    p.add_argument("--level", type=str, default="L1",
                   choices=["L0", "L1", "L2", "L3"])
    p.add_argument("--signature", type=str, default=None,
                   help="调用签名（用于熔断器）。不传则仅按 target-url 互斥。")
    p.add_argument("--allow-concurrent", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--cwd", type=str, default=None)
    p.add_argument("--reset-circuit", action="store_true")
    return p


def _load_evidence(args) -> Optional[str]:
    if args.evidence_file:
        with open(args.evidence_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return args.evidence


def main() -> int:
    args = _build_parser().parse_args()

    # 注入 browser 元数据（仅影响事件 JSONL，不影响 CDP 连接）
    global _BROWSER_NAME
    if args.browser_name:
        _BROWSER_NAME = args.browser_name

    if args.reset_circuit:
        cleared = reset_circuit_breaker()
        emit_event("reset_circuit", EXIT_OK,
                   f"已清空熔断器（清理 {cleared} 个 signature）",
                   cleared=cleared)
        return EXIT_OK

    try:
        validate_target_url(args.target_url)
    except ValueError as e:
        emit_event("validate_target", EXIT_SAFARI_FAIL,
                   f"target_url 校验失败: {e}",
                   target_url=args.target_url)
        return EXIT_SAFARI_FAIL

    try:
        evidence_body = _load_evidence(args)
    except (OSError, IOError) as e:
        emit_event("evidence_load", EXIT_SAFARI_FAIL,
                   f"--evidence-file 读取失败: {e}",
                   evidence_file=args.evidence_file)
        return EXIT_SAFARI_FAIL

    signature = args.signature
    if signature == "":
        signature = None

    if signature:
        try:
            check_circuit_breaker(signature)
        except CircuitOpenError as e:
            emit_event("circuit_breaker", EXIT_CIRCUIT_OPEN, str(e),
                       signature=signature)
            return EXIT_CIRCUIT_OPEN

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
    # 0. 解析 target Tab（连接前锁定 URL 语义）
    try:
        target_id, ws_url = find_target_tab(args.chrome_host, args.chrome_port,
                                            args.target_url)
    except NoTargetTabError as e:
        emit_event("resolve_tab", EXIT_NO_TAB, f"目标 Tab 不存在: {e}",
                   target_url=args.target_url)
        return EXIT_NO_TAB
    except AmbiguousTargetTabError as e:
        emit_event("resolve_tab", EXIT_AMBIGUOUS_TAB, f"目标 Tab 歧义: {e}",
                   target_url=args.target_url)
        return EXIT_AMBIGUOUS_TAB
    except CDPError as e:
        emit_event("resolve_tab", EXIT_SAFARI_FAIL,
                   f"CDP HTTP 发现失败: {e}",
                   chrome_host=args.chrome_host, chrome_port=args.chrome_port)
        return EXIT_SAFARI_FAIL

    # 1. 格式化 Payload
    payload = format_evidence_payload(
        args.type, args.prompt, evidence_body, level=args.level, cwd=args.cwd
    )

    # 2. 建链并执行
    client = ChromeCDPClient(host=args.chrome_host, port=args.chrome_port,
                             target_id=target_id, ws_url=ws_url)
    try:
        try:
            exit_code, answer = send_and_receive_chrome_chatgpt(
                client=client,
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
        except AmbiguousTargetTabError as e:
            emit_event("fatal", EXIT_AMBIGUOUS_TAB,
                       f"未捕获的 AmbiguousTargetTabError: {e}")
            return EXIT_AMBIGUOUS_TAB
        except CDPError as e:
            emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 CDP 异常: {e}")
            return EXIT_SAFARI_FAIL
        except json.JSONDecodeError as e:
            emit_event("fatal", EXIT_SAFARI_FAIL, f"未捕获的 JSONDecodeError: {e}")
            return EXIT_SAFARI_FAIL
        except Exception as e:
            emit_event("fatal", EXIT_SAFARI_FAIL,
                       f"未预期异常 ({type(e).__name__}): {e}")
            return EXIT_SAFARI_FAIL
    finally:
        try:
            client.close()
        except Exception:
            pass

    if answer:
        print(answer)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
