#!/bin/bash
# VoiceInput for macOS: install or update with one command
#
#   curl -fsSL https://raw.githubusercontent.com/weiwei84530/voice-input/mac/install.sh | bash
#
# Puts the code, a private Python (uv) and the speech model in ~/Library/Application Support/VoiceInput and the app
# in /Applications (~/Applications when /Applications is not writable). Running it again updates VoiceInput; the
# permissions given to the app stay, because the app itself (a small launcher) does not change.
#   uninstall:  curl -fsSL .../install.sh | bash -s -- --uninstall
# Environment: VOICEINPUT_BRANCH (default mac), VOICEINPUT_SOURCE (a local checkout instead of downloading),
# VOICEINPUT_APPS_DIR (where the app goes), VOICEINPUT_NO_OPEN=1 (do not start it).
set -euo pipefail

REPO="weiwei84530/voice-input"
BRANCH="${VOICEINPUT_BRANCH:-mac}"
ROOT="$HOME/Library/Application Support/VoiceInput"
TOOLS="$ROOT/tools"
UV="$TOOLS/uv"
VENV="$ROOT/venv"
SRC="$ROOT/source"
if [ -n "${VOICEINPUT_APPS_DIR:-}" ]; then
    APPS="$VOICEINPUT_APPS_DIR"
elif [ -w /Applications ]; then
    APPS="/Applications"
else
    APPS="$HOME/Applications"
fi
APP="$APPS/VoiceInput.app"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31m安裝失敗：%s\033[0m\n' "$*" >&2; exit 1; }

stop_running() {
    pkill -f "$SRC/run.pyw" 2>/dev/null || true
    pkill -x VoiceInput 2>/dev/null || true
    sleep 1
}

if [ "$(uname -s)" != "Darwin" ]; then
    fail "這個安裝指令只適用於 macOS"
fi

if [ "${1:-}" = "--uninstall" ]; then
    say "移除 VoiceInput"
    stop_running
    launchctl unload "$HOME/Library/LaunchAgents/com.voiceinput.app.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.voiceinput.app.plist"
    hidutil property --set '{"UserKeyMapping":[]}' >/dev/null 2>&1 || true
    rm -rf "/Applications/VoiceInput.app" "$HOME/Applications/VoiceInput.app"
    rm -rf "$SRC" "$VENV" "$TOOLS"
    echo "已移除。設定、詞庫和模型留在 $ROOT，不要的話可以整個資料夾刪掉。"
    echo "系統設定 › 隱私權與安全性 裡的 VoiceInput 項目可以手動移除。"
    exit 0
fi

major="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$major" -lt 11 ]; then
    fail "需要 macOS 11 以上（目前是 $(sw_vers -productVersion)）"
fi
mkdir -p "$ROOT" "$TOOLS" "$APPS"

say "1/5 取得程式碼（$BRANCH）"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if [ -n "${VOICEINPUT_SOURCE:-}" ]; then
    mkdir -p "$tmp/src"
    (cd "$VOICEINPUT_SOURCE" && tar cf - --exclude .git --exclude .venv --exclude .tools .) | (cd "$tmp/src" && tar xf -)
else
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" -o "$tmp/src.tar.gz" \
        || fail "下載程式碼失敗，請檢查網路"
    mkdir -p "$tmp/src"
    tar xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components 1
fi
[ -f "$tmp/src/run.pyw" ] || fail "下載的程式碼不完整"
stop_running
rm -rf "$SRC"
mv "$tmp/src" "$SRC"

say "2/5 準備 Python"
if [ ! -x "$UV" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TOOLS" UV_NO_MODIFY_PATH=1 sh >/dev/null \
        || fail "安裝 uv 失敗"
fi
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_PYTHON_PREFERENCE=only-managed
if [ ! -x "$VENV/bin/python3" ]; then
    "$UV" venv --quiet --python 3.12 "$VENV" || fail "建立 Python 環境失敗"
fi

say "3/5 安裝套件（第一次要幾分鐘）"
"$UV" pip install --quiet --python "$VENV/bin/python3" -r "$SRC/requirements.txt" || fail "安裝套件失敗"

say "4/5 下載語音模型（約 130MB，已下載過會略過）"
(cd "$SRC" && "$VENV/bin/python3" -c "
from voiceinput import models
models.download(models.MAIN_MODEL, models._cli_progress)
models.vad_path()
print()
") || fail "下載模型失敗"

say "5/5 建立 VoiceInput.app"
bundle="$tmp/VoiceInput.app"
mkdir -p "$bundle/Contents/MacOS"
cp "$SRC/packaging/mac/Info.plist" "$bundle/Contents/Info.plist"
if [ -f "$SRC/packaging/mac/VoiceInput" ]; then
    cp "$SRC/packaging/mac/VoiceInput" "$bundle/Contents/MacOS/VoiceInput"
elif command -v cc >/dev/null 2>&1 && xcode-select -p >/dev/null 2>&1; then
    cc -O2 -mmacosx-version-min=11.0 -o "$bundle/Contents/MacOS/VoiceInput" "$SRC/packaging/mac/launcher.c" \
        || fail "編譯啟動程式失敗"
else
    fail "找不到啟動程式（packaging/mac/VoiceInput）"
fi
chmod +x "$bundle/Contents/MacOS/VoiceInput"
# the same bytes signed the same way give the same signature: permissions given before stay valid
codesign --force --sign - --identifier com.voiceinput.app "$bundle" >/dev/null 2>&1 || fail "簽署 app 失敗"
if [ -d "$APP" ] && cmp -s "$APP/Contents/MacOS/VoiceInput" "$bundle/Contents/MacOS/VoiceInput" \
        && cmp -s "$APP/Contents/Info.plist" "$bundle/Contents/Info.plist"; then
    echo "app 沒有變，保留原本的（權限不用重新給）"
else
    rm -rf "$APP"
    mv "$bundle" "$APP"
fi

echo
say "完成：$APP"
cat <<'EOF'
第一次使用時 macOS 會請你允許三件事（之後更新不用再給）：
  1. 麥克風：跳出視窗時按「允許」
  2. 輔助使用：系統設定 › 隱私權與安全性 › 輔助使用，打開 VoiceInput
  3. 輸入監控：系統設定 › 隱私權與安全性 › 輸入監控，打開 VoiceInput
打開後幾秒內就能用：滑鼠中鍵按一下開始聽寫，再按一下結束；選單列的麥克風圖示裡有設定。
EOF
if [ "${VOICEINPUT_NO_OPEN:-}" != "1" ]; then
    open "$APP"
fi
