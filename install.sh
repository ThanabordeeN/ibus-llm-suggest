#!/usr/bin/env bash
set -e

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[+]${NC} $*"; }
ok()      { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*"; exit 1; }
ask()     { echo -e "${BOLD}${CYAN}[?]${NC}${BOLD} $*${NC}"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   IBus LLM Auto-suggest  installer   ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. Check OS / deps ───────────────────────────────────────────────────────
info "Checking dependencies..."

command -v python3 >/dev/null || error "python3 not found"
command -v ibus    >/dev/null || error "ibus not found. Install with: sudo dnf install ibus  OR  sudo apt install ibus"
command -v pip3    >/dev/null || error "pip3 not found"

python3 -c "import gi; gi.require_version('IBus','1.0'); from gi.repository import IBus" 2>/dev/null \
    || error "python3-gobject / ibus python bindings not found.\n  Fedora: sudo dnf install python3-gobject ibus\n  Ubuntu: sudo apt install python3-gi gir1.2-ibus-1.0"

# Check ibus-typing-booster
if ! python3 -c "import sqlite3" 2>/dev/null; then
    error "python3 sqlite3 not available"
fi

TB_DB="$HOME/.local/share/ibus-typing-booster/user.db"
if [ ! -f "$TB_DB" ]; then
    warn "ibus-typing-booster DB not found at $TB_DB"
    warn "Install typing-booster and open it once before running this installer:"
    warn "  Fedora: sudo dnf install ibus-typing-booster"
    warn "  Ubuntu: sudo apt install ibus-typing-booster"
    read -rp "Continue anyway? [y/N] " cont
    [[ "$cont" =~ ^[Yy]$ ]] || exit 0
fi

ok "Dependencies OK"

# ── 2. Install Python packages ───────────────────────────────────────────────
info "Installing Python dependencies..."
pip3 install --quiet --user openai
ok "openai installed"

# ── 3. API configuration ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── API Configuration ──────────────────────────────${NC}"
echo "  Examples:"
echo "    OpenRouter : https://openrouter.ai/api/v1"
echo "    OpenAI     : https://api.openai.com/v1"
echo "    Ollama     : http://localhost:11434/v1"
echo ""

ask "Base URL (default: https://openrouter.ai/api/v1):"
read -rp "  > " INPUT_URL
BASE_URL="${INPUT_URL:-https://openrouter.ai/api/v1}"

ask "API Key (leave blank for local Ollama):"
read -rsp "  > " API_KEY
echo ""

ask "Model (default: deepseek/deepseek-v4-flash):"
read -rp "  > " INPUT_MODEL
MODEL="${INPUT_MODEL:-deepseek/deepseek-v4-flash}"

# Write config
CONFIG_DIR="$HOME/.config/llm-ibus"
CONFIG_FILE="$CONFIG_DIR/config.json"
mkdir -p "$CONFIG_DIR"

python3 - <<PYEOF
import json
cfg = {
    "base_url":          "$BASE_URL",
    "api_key":           "$API_KEY",
    "model":             "$MODEL",
    "max_suggestions":   3,
    "max_tokens":        80,
    "timeout":           5.0,
    "enabled":           True,
    "context_words":     30,
    "disable_reasoning": True,
}
with open("$CONFIG_FILE", "w") as f:
    json.dump(cfg, f, indent=2)
print("  Config written to $CONFIG_FILE")
PYEOF

ok "Config saved"

# ── 4. Test API connection ────────────────────────────────────────────────────
echo ""
info "Testing API connection..."
RESULT=$(python3 - 2>&1 <<PYEOF
import sys
sys.path.insert(0, "$PROJECT_DIR")
from daemon.llm_client import get_suggestions
r = get_suggestions("Hello this is a test", n=1)
if r:
    print("OK: " + repr(r[0]))
else:
    print("FAIL: no response")
    sys.exit(1)
PYEOF
)

if echo "$RESULT" | grep -q "^OK:"; then
    ok "API works — $RESULT"
else
    warn "API test failed: $RESULT"
    warn "You can fix the config later at: $CONFIG_FILE"
fi

# ── 5. Register IBus component ───────────────────────────────────────────────
info "Registering IBus component..."
COMPONENT_DIR="$HOME/.local/share/ibus/component"
mkdir -p "$COMPONENT_DIR"

cat > "$COMPONENT_DIR/llm-suggest.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<component>
  <name>org.freedesktop.IBus.LLMSuggest</name>
  <description>LLM Autocomplete Engine</description>
  <version>0.1.0</version>
  <author>user</author>
  <license>MIT</license>
  <exec>python3 $PROJECT_DIR/engine/main.py</exec>
  <engines>
    <engine>
      <name>llm-suggest</name>
      <language>other</language>
      <license>MIT</license>
      <author>user</author>
      <longname>LLM Suggest</longname>
      <description>AI-powered autocomplete via OpenAI-compatible API</description>
      <rank>0</rank>
      <icon></icon>
      <layout>us</layout>
      <symbol>AI</symbol>
    </engine>
  </engines>
</component>
EOF
ok "IBus component registered"

# ── 6. Systemd user timer ────────────────────────────────────────────────────
info "Setting up auto-learn timer (every 1 hour)..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/llm-auto-learn.service" <<EOF
[Unit]
Description=LLM Auto-Learn for IBus Typing Booster
After=network-online.target

[Service]
Type=oneshot
ExecStart=python3 $PROJECT_DIR/tools/auto_learn.py
StandardOutput=journal
StandardError=journal
EOF

cat > "$SYSTEMD_DIR/llm-auto-learn.timer" <<EOF
[Unit]
Description=Run LLM Auto-Learn every hour

[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now llm-auto-learn.timer
ok "Auto-learn timer enabled"

# ── 7. Autostart on login ────────────────────────────────────────────────────
info "Setting up autostart on login..."
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/llm-ibus.desktop" <<EOF
[Desktop Entry]
Name=LLM IBus Engine
Type=Application
Exec=bash $PROJECT_DIR/start.sh
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
ok "Autostart entry created"

# ── 8. Start now ────────────────────────────────────────────────────────────
echo ""
info "Starting engine now..."

pkill -f "LLM-auto-suggestion/engine/main.py" 2>/dev/null || true
sleep 1

if ! pgrep -x ibus-daemon >/dev/null; then
    ibus-daemon -dr --panel=disable
    sleep 3
fi

python3 "$PROJECT_DIR/engine/main.py" &
sleep 4
ibus engine llm-suggest 2>/dev/null && ok "Engine active" || warn "Could not activate engine — run 'ibus engine llm-suggest' manually"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════${NC}"
echo ""
echo "  Start typing in any app — suggestions appear after a short pause."
echo "  Use ↓↑ to navigate, → or click to accept, Esc to dismiss."
echo ""
echo "  Useful commands:"
echo "    Stats    : python3 $PROJECT_DIR/tools/stats.py"
echo "    Config   : $CONFIG_FILE"
echo "    Log      : journalctl --user -u llm-auto-learn.service -f"
echo "    Uninstall: bash $PROJECT_DIR/uninstall.sh"
echo ""
