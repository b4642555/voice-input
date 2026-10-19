"""Official Antigravity CLI account transport. Conversations may be saved by agy.

Opt-in only. A workspace PreToolUse hook denies every tool; this application
accepts completed plain text only. No credentials are read, copied or exported.
"""
import atexit
import base64
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from .paths import DATA_DIR

MODEL = "gemini-3.8-flash-low"
NO_WINDOW = 0x08000000
WORKSPACE = DATA_DIR / "antigravity-polish"


class AntigravityError(RuntimeError):
    pass


def cli_path():
    found = shutil.which("agy")
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
    return found or (str(path) if path.is_file() else None)


def environment():
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(("GOOGLE_", "GEMINI_", "AGY_", "ANTHROPIC_", "OPENAI_"))}


def list_models():
    exe = cli_path()
    if not exe:
        raise AntigravityError("找不到 Antigravity CLI")
    try:
        result = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8",
                                timeout=15, env=environment(), creationflags=NO_WINDOW)
        if result.returncode:
            raise AntigravityError("無法取得 Antigravity 模型清單")
        return [line.split()[0] for line in result.stdout.splitlines() if line.startswith("gemini-")]
    except (OSError, subprocess.SubprocessError) as error:
        raise AntigravityError("無法取得 Antigravity 模型清單") from error


def prepare_workspace():
    # The hook is static: no user text is interpolated into a command or file.
    folder = WORKSPACE / ".agents"
    folder.mkdir(parents=True, exist_ok=True)
    script = 'Write-Output \'{"decision":"deny","reason":"VoiceInput text-only: tools disabled"}\''
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    hook = {"voiceinput-no-tools": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command",
            "command": "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded, "timeout": 5}]}]}}
    target = folder / "hooks.json"
    content = json.dumps(hook)
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")


class Process:
    def __init__(self, model):
        exe = cli_path()
        if not exe:
            raise AntigravityError("找不到 Antigravity CLI")
        # A configured API route must never receive subscription dictation.
        config = Path.home() / ".gemini/antigravity-cli/settings.json"
        try:
            if config.exists() and json.loads(config.read_text(encoding="utf-8")).get("modelProvider"):
                raise AntigravityError("Antigravity 已設定其他供應商，請改用帳號登入")
        except (OSError, ValueError) as error:
            raise AntigravityError("無法確認 Antigravity 登入方式") from error
        prepare_workspace()
        self.model, self.used, self.dead = model, 0, False
        self.events = queue.Queue()
        self.usage = {}
        self.proc = subprocess.Popen([exe, "--model", model, "--disable-slash-commands", "--input-format",
                                      "stream-json", "--output-format", "stream-json", "--print-timeout", "12s"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", cwd=WORKSPACE,
                                     env=environment(), creationflags=NO_WINDOW)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                    if event.get("event") in ("init", "result", "step_update"):
                        self.events.put(event)
                except ValueError:
                    pass
        finally:
            self.events.put(None)

    def ask(self, messages, timeout):
        end = time.monotonic() + timeout
        text = "\n\n".join(m["content"] for m in messages)
        try:
            self.proc.stdin.write(json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            while True:
                event = self.events.get(timeout=max(.01, end-time.monotonic()))
                if event is None:
                    raise AntigravityError("Antigravity 連線中斷，請確認帳號登入")
                if event.get("event") == "step_update" and event.get("step_update", {}).get("step_type") == "tool":
                    raise AntigravityError("Antigravity 嘗試額外操作，已取消潤稿")
                if event.get("event") == "result":
                    result = event.get("result", {})
                    answer = result.get("response")
                    if result.get("status") != "SUCCESS" or not isinstance(answer, str) or not answer.strip():
                        raise AntigravityError("Antigravity 未完成回應，請確認登入、額度或模型")
                    self.used += 1
                    self.usage = result.get("usage", {})
                    return answer.strip()
        except (queue.Empty, OSError, ValueError) as error:
            self.dead = True
            raise AntigravityError("Antigravity 回應逾時或連線中斷") from error
        except AntigravityError:
            self.dead = True
            raise

    def close(self):
        self.dead = True
        if self.proc.poll() is None:
            # Kill only this worker and its children; agy owns a language-server child.
            try:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, timeout=5, creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


_lock = threading.Lock()
_process = None
_retry_at = 0.0


def warm_up(model=MODEL):
    def run():
        global _process
        with _lock:
            if time.monotonic() < _retry_at:
                return
            try:
                if _process and (_process.model != model or _process.dead or _process.proc.poll() is not None):
                    _process.close()
                    _process = None
                if _process is None:
                    _process = Process(model)
            except (AntigravityError, OSError):
                pass
    threading.Thread(target=run, daemon=True).start()


def chat(messages, timeout, model=MODEL):
    global _process, _retry_at
    with _lock:
        if time.monotonic() < _retry_at:
            raise AntigravityError("Antigravity 暫時無法使用，稍後再試")
        try:
            if _process and (_process.model != model or _process.dead or _process.used >= 8 or _process.proc.poll() is not None):
                _process.close()
                _process = None
            if _process is None:
                _process = Process(model)
            return _process.ask(messages, timeout)
        except (AntigravityError, OSError) as error:
            _retry_at = time.monotonic() + 60
            if _process:
                _process.close()
                _process = None
            raise AntigravityError(str(error)) from error


def shutdown():
    global _process
    with _lock:
        if _process:
            _process.close()
            _process = None


atexit.register(shutdown)
