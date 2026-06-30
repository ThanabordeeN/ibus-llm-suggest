#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[*] Stopping engine..."
pkill -f "LLM-auto-suggestion/engine/main.py" 2>/dev/null || true

echo "[*] Disabling systemd timer..."
systemctl --user disable --now llm-auto-learn.timer 2>/dev/null || true
rm -f ~/.config/systemd/user/llm-auto-learn.{service,timer}
systemctl --user daemon-reload

echo "[*] Removing autostart entry..."
rm -f ~/.config/autostart/llm-ibus.desktop

echo "[*] Removing IBus component..."
rm -f ~/.local/share/ibus/component/llm-suggest.xml

echo "[*] Removing config and data..."
rm -rf ~/.config/llm-ibus
rm -rf ~/.local/share/llm-ibus

echo ""
read -rp "[?] Also remove learned phrases from typing-booster DB? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    python3 - <<'PYEOF'
import sqlite3, os
db = os.path.expanduser("~/.local/share/ibus-typing-booster/user.db")
if os.path.exists(db):
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM phrases WHERE user_freq = 1")
    conn.commit()
    conn.close()
    print("[*] Removed LLM-generated phrases (freq=1) from typing-booster")
PYEOF
fi

echo ""
echo "[✓] Uninstalled. Config at ~/.config/llm-ibus was removed."
echo "    Run 'bash $PROJECT_DIR/install.sh' to reinstall."
