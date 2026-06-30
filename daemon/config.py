import json
import os

CONFIG_PATH = os.path.expanduser("~/.config/llm-ibus/config.json")

DEFAULTS = {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "",
    "model": "deepseek/deepseek-v4-flash",
    "max_suggestions": 3,
    "max_tokens": 80,
    "disable_reasoning": True,
    "timeout": 5.0,
    "enabled": True,
    "context_words": 30,
}


def load() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
