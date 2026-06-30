# ibus-llm-suggest

AI-powered autocomplete for Linux — integrates with **IBus typing-booster** and learns from your typing history using any OpenAI-compatible API.

## How it works

```
You type  →  typing-booster records it
              ↓  (every hour, if online)
           LLM reads your recent phrases
              ↓
           Generates grammatically correct completions in your style
              ↓
           Injects them back into typing-booster DB
              ↓
           typing-booster suggests them as you type
```

## Requirements

- Linux with GNOME / IBus
- `ibus-typing-booster` installed and opened at least once
- Python 3.10+
- An OpenAI-compatible API key (OpenRouter, OpenAI, or local Ollama)

**Fedora:**
```bash
sudo dnf install ibus ibus-typing-booster python3-gobject
```

**Ubuntu/Debian:**
```bash
sudo apt install ibus ibus-typing-booster python3-gi gir1.2-ibus-1.0
```

## Install

```bash
git clone https://github.com/ThanabordeeN/ibus-llm-suggest
cd ibus-llm-suggest
bash install.sh
```

The installer will ask for:

| Prompt | Example |
|--------|---------|
| Base URL | `https://openrouter.ai/api/v1` |
| API Key | `sk-or-v1-...` |
| Model | `deepseek/deepseek-v4-flash` |

Then it automatically:
- Tests your API connection
- Registers the IBus engine
- Sets up an hourly systemd timer for auto-learning
- Adds an autostart entry for your desktop session

## Usage

Start typing in any app. After a short pause, typing-booster will suggest completions learned from your writing style.

The auto-learn timer runs every hour in the background — the more you type, the smarter it gets.

## Supported APIs

| Provider | Base URL |
|----------|----------|
| OpenRouter | `https://openrouter.ai/api/v1` |
| OpenAI | `https://api.openai.com/v1` |
| Ollama (local) | `http://localhost:11434/v1` |
| llama.cpp | `http://localhost:8080/v1` |

Recommended model: `deepseek/deepseek-v4-flash` (fast, cheap, reasoning-capable)

## Tools

```bash
# See if auto-learn is actually improving suggestions (hit rate)
python3 tools/stats.py

# Manually learn from a text file
python3 tools/learn_from_text.py mytext.txt --llm-expand

# Watch the auto-learn log live
journalctl --user -u llm-auto-learn.service -f

# Run auto-learn now instead of waiting
systemctl --user start llm-auto-learn.service
```

## Config

`~/.config/llm-ibus/config.json`

```json
{
  "base_url": "https://openrouter.ai/api/v1",
  "api_key": "sk-or-v1-...",
  "model": "deepseek/deepseek-v4-flash",
  "max_suggestions": 3,
  "max_tokens": 80,
  "timeout": 5.0,
  "enabled": true,
  "context_words": 30,
  "disable_reasoning": true
}
```

## Uninstall

```bash
bash uninstall.sh
```

## Project structure

```
├── daemon/
│   ├── config.py        # Load/save config
│   ├── llm_client.py    # OpenAI-compatible API calls
│   └── memory.py        # SQLite phrase memory
├── engine/
│   ├── llm_engine.py    # IBus engine
│   └── main.py          # Entry point / component registration
├── settings/
│   └── settings_ui.py   # GTK3 settings window
├── tools/
│   ├── auto_learn.py    # Hourly learning daemon
│   ├── learn_from_text.py  # Manual import from text file
│   └── stats.py         # Hit-rate diagnostics
├── install.sh
└── uninstall.sh
```

## License

MIT
