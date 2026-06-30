#!/usr/bin/env python3
"""
Auto-learn: runs every hour, reads what you've been typing from typing-booster,
sends it to LLM, gets back related phrases, inserts them back into typing-booster.
"""
import sys
import os
import re
import time
import sqlite3
import json
import subprocess
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.expanduser("~/.local/share/ibus-typing-booster/user.db")
LOG_PATH = os.path.expanduser("~/.local/share/llm-ibus/auto_learn.log")
STATE_PATH = os.path.expanduser("~/.local/share/llm-ibus/last_learn.json")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def has_internet() -> bool:
    try:
        subprocess.check_call(
            ["ping", "-c", "1", "-W", "3", "1.1.1.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"last_timestamp": 0.0}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def read_recent_phrases(since_ts: float, limit: int = 200) -> list[str]:
    """Read phrases the user actually typed since last run."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT DISTINCT phrase FROM phrases
        WHERE timestamp > ? AND user_freq >= 1
        ORDER BY user_freq DESC, timestamp DESC
        LIMIT ?
        """,
        (since_ts, limit),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def llm_generate(recent_phrases: list[str]) -> list[str]:
    from daemon.config import load as load_config
    import openai

    cfg = load_config()
    client = openai.OpenAI(
        api_key=cfg["api_key"] or "ollama",
        base_url=cfg["base_url"],
    )

    sample = "\n".join(f"- {p}" for p in recent_phrases[:80])

    # Reasoning on — batch job, latency doesn't matter
    extra = {"extra_body": {"reasoning": {"effort": "high"}}}

    prompt = f"""\
These are phrases a user has recently typed:
{sample}

Based on their vocabulary and domain, generate 100 short phrases (2–6 words) \
they are likely to type in the near future.

Requirements:
- Every phrase MUST be grammatically correct English
- Proper subject-verb agreement, correct tense, natural word order
- No sentence fragments that sound unnatural on their own
- Match the user's domain and tone (technical, casual, etc.)
- Each phrase should be something a fluent English speaker would actually write

Return ONLY a JSON array of strings."""

    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": "You generate grammatically correct English autocomplete phrases. Return only a JSON array."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4000,
            temperature=0.5,
            timeout=30,
            **extra,
        )
        raw = resp.choices[0].message.content or ""
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            items = json.loads(match.group())
            return [str(s).strip() for s in items if s and len(s.strip()) > 1]
    except Exception as e:
        log.error("LLM error: %s", e)

    return []


def insert_phrases(phrases: list[str]) -> int:
    if not phrases:
        return 0

    conn = sqlite3.connect(DB_PATH)
    now = time.time()
    count = 0

    for phrase in phrases:
        tokens = re.findall(r"[฀-๿]+|[a-zA-Z0-9']+", phrase)
        if not tokens:
            continue

        input_phrase = tokens[0]

        existing = conn.execute(
            "SELECT id, user_freq FROM phrases WHERE input_phrase=? AND phrase=?",
            (input_phrase, phrase),
        ).fetchone()

        if existing:
            # Boost frequency slightly — LLM suggested it, so it's relevant
            conn.execute(
                "UPDATE phrases SET user_freq=?, timestamp=? WHERE id=?",
                (existing[1] + 1, now, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO phrases (input_phrase, phrase, p_phrase, pp_phrase, user_freq, timestamp) VALUES (?,?,?,?,?,?)",
                (input_phrase, phrase, "", "", 1, now),
            )
        count += 1

    conn.commit()
    conn.close()
    return count


def prune_stale(older_than_hours: int = 48) -> int:
    """Delete freq=1 phrases that were added more than N hours ago and never selected."""
    cutoff = time.time() - (older_than_hours * 3600)
    conn = sqlite3.connect(DB_PATH)
    result = conn.execute(
        "DELETE FROM phrases WHERE user_freq = 1 AND timestamp < ?", (cutoff,)
    )
    deleted = result.rowcount
    conn.commit()
    conn.close()
    return deleted


def main():
    log.info("Auto-learn started")

    if not has_internet():
        log.info("No internet — skipping")
        print("No internet connection, skipping.")
        return

    if not os.path.exists(DB_PATH):
        log.warning("typing-booster DB not found at %s", DB_PATH)
        print(f"typing-booster DB not found: {DB_PATH}")
        return

    state = load_state()
    since = state["last_timestamp"]
    now = time.time()

    recent = read_recent_phrases(since_ts=since)
    log.info("Found %d recent phrases since last run", len(recent))

    if len(recent) < 5:
        log.info("Not enough new phrases to learn from (%d), skipping LLM call", len(recent))
        print(f"Only {len(recent)} new phrases since last run — skipping (need at least 5).")
        save_state({"last_timestamp": now})
        return

    # Prune stale LLM phrases that were never selected after 48h
    pruned = prune_stale(older_than_hours=48)
    if pruned:
        log.info("Pruned %d stale phrases", pruned)
        print(f"[+] Pruned {pruned} unused LLM phrases (>48h, never selected)")

    print(f"[+] {len(recent)} recent phrases found, calling LLM...")
    generated = llm_generate(recent)
    log.info("LLM generated %d phrases", len(generated))
    print(f"[+] LLM generated {len(generated)} new phrases")

    inserted = insert_phrases(generated)
    log.info("Inserted/updated %d phrases in DB", inserted)
    print(f"[+] Inserted {inserted} phrases into typing-booster")

    save_state({"last_timestamp": now})
    log.info("Done")


if __name__ == "__main__":
    main()
