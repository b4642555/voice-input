"""ChatGPT subscription access through the official Codex CLI; no API credentials."""
import atexit
from collections import deque
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import queue
import threading
import time
import tomllib

MODEL = "gpt-6-luna"
INSTALL_URL = "https://learn.chatgpt.com/docs/cli"
NO_WINDOW = 0x08000000


class CodexError(RuntimeError):
    def __init__(self, message, retry_after=30.0):
        super().__init__(message)
        self.retry_after = retry_after


def cli_path() -> str | None:
    found = shutil.which("codex")
    if found:
        return found
    # Desktop installations do not always add their bundled CLI to pythonw's PATH.
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    candidates = list(root.glob("*/codex.exe"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    candidates.extend([Path.home() / "AppData/Roaming/npm/codex.cmd"])
    return next((str(p) for p in candidates if p.is_file()), None)


def _environment() -> dict[str, str]:
    # Keep CODEX_HOME for the user's own sign-in. Never inherit a paid API route.
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(("OPENAI_", "ANTHROPIC_", "CLAUDE"))
            and (not k.upper().startswith("CODEX_") or k.upper() == "CODEX_HOME")}


def login_state() -> str:
    exe = cli_path()
    if not exe:
        return "missing"
    try:
        r = subprocess.run([exe, "login", "status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=5, creationflags=NO_WINDOW, env=_environment())
        return "ok" if r.returncode == 0 and "using ChatGPT" in r.stdout + r.stderr else "out"
    except (OSError, subprocess.SubprocessError):
        return "out"


def login() -> None:
    exe = cli_path()
    if not exe:
        raise CodexError("找不到 Codex，請先安裝")
    # Interactive browser sign-in, deliberately opened by the user's login button.
    subprocess.Popen([exe, "login"], env=_environment(),
                     creationflags=0x00000010)


def login_status() -> tuple[str, str]:
    """Read account identity through the official CLI, without inference or saving it."""
    stage = login_state()
    if stage != "ok":
        return stage, ""
    server = None
    try:
        server = _Server(time.monotonic() + 8)
        email = server.account.get("email")
        return "ok", email.strip() if isinstance(email, str) else ""
    except (CodexError, OSError):
        # A status check can succeed while the identity lookup is unavailable.
        # Never retain an earlier email or infer identity from another provider.
        return "unknown", ""
    finally:
        if server is not None:
            server.close()


# The official app-server is prestarted without a model request. Each worker handles
# one ephemeral turn, then exits so screen/caret context is released immediately.
# A fresh standby is prepared while that turn runs; this avoids exec's per-call setup
# and shutdown latency without carrying another dictation's history into the model.
def _config() -> dict:
    cfg = {"forced_login_method": "chatgpt", "model_provider": "openai",
           "model_reasoning_effort": "none", "approval_policy": "never",
           "sandbox_mode": "read-only", "skills.include_instructions": False,
           "project_doc_max_bytes": 0, "web_search": "disabled", "notify": [],
           "history.persistence": "none", "features.unbounded_connection_retries": False,
           "include_apps_instructions": False, "include_collaboration_mode_instructions": False}
    cfg.update(service_tier="default")
    cfg["features.fast_mode"] = True
    for feature in ("shell_tool", "apply_patch_freeform", "multi_agent", "multi_agent_v2", "apps",
                    "plugins", "hooks", "memories", "skill_search", "image_generation",
                    "computer_use", "view_image", "code_mode", "code_mode_host", "shell_snapshot"):
        cfg["features." + feature] = False
    # Disable the user's configured tool servers for this text-only child. Override
    # the complete table (TOML), so names containing dots cannot become config paths.
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        data = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
        servers = data.get("mcp_servers", {})
        cfg["mcp_servers"] = {name: {"enabled": False} for name in servers}
    except FileNotFoundError:
        cfg["mcp_servers"] = {}
    except (OSError, ValueError):
        raise CodexError("無法讀取 Codex 設定，暫時使用 Claude", 60) from None
    return cfg


def _toml(value):
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + "=" + _toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise CodexError("GPT 回應逾時")
    return left


class _Server:
    def __init__(self, deadline):
        self.proc = None
        self.folder = tempfile.TemporaryDirectory(prefix="voiceinput-gpt-")
        self.events = queue.Queue()
        self.pending = deque()
        self.seq = 0
        try:
            exe = cli_path()
            if not exe:
                raise CodexError("找不到 Codex，暫時使用 Claude", 300)
            self.config = _config()
            cmd = [exe, "app-server"]
            for key, value in self.config.items():
                cmd.extend(["-c", key + "=" + _toml(value)])
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                         cwd=self.folder.name, env=_environment(), creationflags=NO_WINDOW)
            threading.Thread(target=self._read, daemon=True).start()
            self._rpc("initialize", {"clientInfo": {"name": "voiceinput", "version": "1.0"},
                                     "capabilities": {"experimentalApi": True}}, deadline)
            self._send({"method": "initialized"})
            self.check_account(deadline)
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if "id" in event or event.get("method") in ("item/completed", "turn/completed", "error"):
                    self.events.put(event)
        except (OSError, ValueError):
            pass
        finally:
            self.events.put(None)

    def _send(self, event):
        try:
            self.proc.stdin.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise CodexError("GPT 連線中斷") from None

    def _next(self, deadline):
        try:
            event = self.events.get(timeout=_remaining(deadline))
        except queue.Empty:
            raise CodexError("GPT 回應逾時") from None
        if event is None:
            raise CodexError("GPT 連線中斷")
        if "id" in event and "method" in event:
            # No filesystem, tools or approval interaction is allowed in a polisher.
            self._send({"id": event["id"], "error": {"code": -32601, "message": "Text-only client"}})
            raise CodexError("GPT 回應包含額外操作，已取消")
        return event

    def _rpc(self, method, params, deadline):
        self.seq += 1
        self._send({"id": self.seq, "method": method, "params": params})
        while True:
            event = self._next(deadline)
            if event.get("id") == self.seq:
                if "error" in event:
                    raise CodexError("GPT 暫時無法回應，請檢查訂閱額度、登入或網路")
                return event.get("result") or {}
            self.pending.append(event)

    def check_account(self, deadline):
        account = self._rpc("account/read", {"refreshToken": False}, deadline).get("account") or {}
        if account.get("type") != "chatgpt":
            raise CodexError("GPT 尚未以 ChatGPT 帳號登入，暫時使用 Claude", 300)
        self.account = account

    def ask(self, messages, model, deadline, fast=False):
        self.check_account(deadline)  # a logout or API-login change must take effect before sending text
        system = next((m["content"] for m in messages if m["role"] == "system"), "只輸出整理後的文字。")
        task = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
        thread = self._rpc("thread/start", {"model": model, "modelProvider": "openai", "ephemeral": True,
                           "cwd": self.folder.name, "approvalPolicy": "never", "sandbox": "read-only",
                           "baseInstructions": system, "developerInstructions": "", "environments": [],
                           "config": self.config}, deadline)["thread"]["id"]
        turn = self._rpc("turn/start", {"threadId": thread, "effort": "none",
                        "serviceTierForTurn": "priority" if fast else "default",
                        "input": [{"type": "text", "text": task}]}, deadline)["turn"]["id"]
        answer = ""
        while True:
            event = self.pending.popleft() if self.pending else self._next(deadline)
            params = event.get("params") or {}
            if params.get("threadId") != thread:
                continue
            method = event.get("method")
            if method == "error":
                raise CodexError("GPT 暫時無法回應，請檢查訂閱額度、登入或網路")
            if method == "item/completed" and params.get("turnId") == turn:
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                    answer = item.get("text", "")
            if method == "turn/completed" and params.get("turn", {}).get("id") == turn:
                if params["turn"].get("status") != "completed" or not isinstance(answer, str) or not answer.strip():
                    raise CodexError("GPT 沒有完成回應")
                return answer.strip()

    def close(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
        self.pending.clear()
        self.folder.cleanup()


_lock = threading.Lock()
_ready = threading.Event()
_standby = None
_starting = False
_closed = False
_active = set()
_retry_at = 0.0
_last_error = "GPT 正在準備，暫時使用 Claude"


def _unavailable(error):
    global _retry_at, _last_error
    _retry_at = time.monotonic() + error.retry_after
    _last_error = str(error)


def warm_up():
    """Prepare a logged-in transport only. No inference request or quota is used."""
    global _starting
    with _lock:
        if _closed or _standby is not None or _starting or time.monotonic() < _retry_at:
            return
        _starting = True
        _ready.clear()
    def prepare():
        global _standby, _starting
        server = None
        try:
            server = _Server(time.monotonic() + 10)
        except (CodexError, OSError, ValueError, KeyError) as exc:
            with _lock:
                _unavailable(exc if isinstance(exc, CodexError) else CodexError("無法啟動 GPT 連線"))
        finally:
            with _lock:
                if server is not None:
                    if _closed or time.monotonic() < _retry_at:
                        server.close()
                    else:
                        _standby = server
                _starting = False
                _ready.set()
    threading.Thread(target=prepare, daemon=True).start()


def retry_now():
    global _retry_at
    with _lock:
        _retry_at = 0.0
    warm_up()


def chat(messages: list[dict], timeout: float, model: str = MODEL, fast=False) -> str:
    global _standby
    deadline = time.monotonic() + timeout
    warm_up()
    # Startup normally finishes before the user records. Do not spend a full model
    # timeout waiting for an unavailable worker on each dictation.
    _ready.wait(min(2.0, _remaining(deadline)))
    with _lock:
        if time.monotonic() < _retry_at or _standby is None:
            raise CodexError(_last_error)
        server, _standby = _standby, None
        _active.add(server)
    warm_up()  # prepare the next empty worker while this one handles the dictation
    try:
        return server.ask(messages, model, deadline, fast=fast)
    except (CodexError, OSError, ValueError, KeyError) as exc:
        error = exc if isinstance(exc, CodexError) else CodexError("GPT 回應格式異常")
        with _lock:
            _unavailable(error)
        raise error from None
    finally:
        server.close()
        with _lock:
            _active.discard(server)


def shutdown():
    global _standby, _closed
    with _lock:
        _closed = True
        server, _standby = _standby, None
        active = list(_active)
    for worker in active + ([server] if server is not None else []):
        worker.close()


atexit.register(shutdown)


def list_models() -> list[str]:
    """Read the signed-in account's official catalog without inference."""
    server = _Server(time.monotonic() + 10)
    try:
        ids, cursor = [], None
        while True:
            result = server._rpc("model/list", {"includeHidden": False, "cursor": cursor}, time.monotonic() + 10)
            ids.extend(m["model"] for m in result.get("data", []) if isinstance(m.get("model"), str))
            following = result.get("nextCursor")
            if not following or following == cursor:
                return list(dict.fromkeys(ids))
            cursor = following
    finally:
        server.close()
