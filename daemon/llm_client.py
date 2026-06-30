import json
import re
import openai
from daemon.config import load as load_config
from daemon.memory import get_top_phrases

_SYSTEM_PROMPT = """\
You are an inline autocomplete engine embedded in the user's desktop.
Your job: predict what the user is about to type next, based on their recent text.

Rules:
- Output ONLY a JSON array of strings. No explanation, no markdown, no extra text.
- Each suggestion continues directly from the last character of the input — no repetition of what was already typed.
- Every suggestion MUST be grammatically correct and natural-sounding English.
- Use proper subject-verb agreement, correct tense, and natural word order.
- Prefer short, high-confidence completions (2–8 words). Avoid padding or filler.
- Make each suggestion meaningfully different from the others.
- If the context is code, complete the code idiomatically and syntactically correct."""

_USER_TEMPLATE = """\
App: {app}
Recent text: {context}{memory_block}

Return {n} grammatically correct completions as a JSON array."""

_MEMORY_BLOCK = """
Past phrases this user has accepted (use as style reference):
{phrases}"""


def get_suggestions(
    context: str,
    n: int = 3,
    app_name: str = "",
) -> list[str]:
    cfg = load_config()

    if not cfg.get("api_key") and "localhost" not in cfg.get("base_url", ""):
        return []

    client = openai.OpenAI(
        api_key=cfg["api_key"] or "ollama",
        base_url=cfg["base_url"],
    )

    # Pull accepted phrases from memory to personalise suggestions
    past = get_top_phrases(context, app_name, limit=5)
    memory_block = ""
    if past:
        memory_block = _MEMORY_BLOCK.format(phrases="\n".join(f"- {p}" for p in past))

    user_msg = _USER_TEMPLATE.format(
        app=app_name or "unknown",
        context=context.strip(),
        memory_block=memory_block,
        n=n,
    )

    extra = {}
    if cfg.get("disable_reasoning", True):
        extra["extra_body"] = {"reasoning": {"effort": "none"}}

    try:
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=cfg["max_tokens"],
            temperature=0.4,
            timeout=cfg["timeout"],
            **extra,
        )
        raw = response.choices[0].message.content or ""
        return _parse_suggestions(raw, n)
    except Exception:
        return []


def _parse_suggestions(raw: str, n: int) -> list[str]:
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if match:
        try:
            items = json.loads(match.group())
            return [str(s).strip() for s in items if str(s).strip()][:n]
        except json.JSONDecodeError:
            pass
    lines = [l.strip().strip('"').strip("'").strip("-").strip() for l in re.split(r"[\n,]", raw)]
    return [l for l in lines if l][:n]
