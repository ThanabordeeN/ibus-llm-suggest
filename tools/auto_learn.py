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
import concurrent.futures

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


def _call_llm(client, prompt: str, system: str, max_tokens: int = 4000) -> list[str]:
    """Shared LLM call — returns a parsed list of strings from a JSON array response."""
    extra = {"extra_body": {"reasoning": {"effort": "high"}}}
    try:
        resp = client.chat.completions.create(
            model=__import__("daemon.config", fromlist=["load"]).load()["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.5,
            timeout=60,
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


def _make_client():
    from daemon.config import load as load_config
    import openai
    cfg = load_config()
    return openai.OpenAI(api_key=cfg["api_key"] or "ollama", base_url=cfg["base_url"])


def llm_generate(recent_phrases: list[str]) -> list[str]:
    """Phase 1 — generate phrases matching overall writing style."""
    client = _make_client()
    sample = "\n".join(f"- {p}" for p in recent_phrases[:80])
    prompt = f"""\
These are phrases a user has recently typed:
{sample}

Based on their vocabulary and domain, generate 150 short phrases (2–6 words) \
they are likely to type in the near future.

Requirements:
- Every phrase MUST be grammatically correct English
- Proper subject-verb agreement, correct tense, natural word order
- No sentence fragments that sound unnatural on their own
- Match the user's domain and tone (technical, casual, etc.)
- Each phrase should be something a fluent English speaker would actually write

Return ONLY a JSON array of strings."""

    return _call_llm(
        client, prompt,
        system="You generate grammatically correct English autocomplete phrases. Return only a JSON array.",
    )


def llm_predict_new_words(recent_phrases: list[str], all_known: list[str]) -> list[str]:
    """Phase 3 — predict words/phrases the user has NEVER typed but likely will.

    Looks at the user's domain/context and surfaces related vocabulary they
    haven't used yet, pre-loading typing-booster before they need it.
    """
    client = _make_client()
    sample = "\n".join(f"- {p}" for p in recent_phrases[:60])
    known_words = set(
        w.lower()
        for p in all_known
        for w in re.findall(r"[a-zA-Z]{3,}", p)
    )
    known_str = ", ".join(sorted(known_words)[:80])

    prompt = f"""\
A user's recent writing (their domain and style):
{sample}

Words they have already typed: {known_str}

Your job: infer what RELATED words and phrases they have NOT yet typed but \
will likely need soon — vocabulary that fits their domain but is missing from their history.

For example, if they write about Python APIs, they might not have typed \
"rate limiting", "pagination", "retry logic", "status code" yet — but probably will.

Generate 80 short phrases (2–6 words) using this predicted new vocabulary.
- Every phrase MUST be grammatically correct English
- Only include phrases with words NOT already in their known word list
- Prioritize high-value domain-specific phrases

Return ONLY a JSON array of strings."""

    results = _call_llm(
        client, prompt,
        system="You predict future vocabulary for autocomplete. Return only a JSON array of grammatically correct phrases.",
        max_tokens=5000,
    )
    log.info("New-word prediction produced %d phrases", len(results))
    return results


def llm_expand_words(recent_phrases: list[str]) -> list[str]:
    """Phase 2 — for each frequent word, expand all likely continuations.

    This is the aggressive part: instead of matching overall style, we ensure
    typing-booster has completions ready for every individual word the user types.
    """
    # Extract unique words sorted by frequency across all recent phrases
    word_freq: dict[str, int] = {}
    for phrase in recent_phrases:
        for word in re.findall(r"[a-zA-Z]{3,}", phrase):
            word_freq[word.lower()] = word_freq.get(word.lower(), 0) + 1

    # Take top 30 most frequent words — these are what the user types most
    top_words = sorted(word_freq, key=lambda w: -word_freq[w])[:30]
    if not top_words:
        return []

    client = _make_client()
    words_str = ", ".join(f'"{w}"' for w in top_words)

    prompt = f"""\
A user frequently types these words: {words_str}

For EACH word, generate 5 short grammatically correct phrases (3–7 words) \
that a person would naturally type starting with or immediately after that word.

Requirements:
- Cover different contexts each word could appear in
- Every phrase must be grammatically correct and complete-sounding
- Mix of sentence starters and mid-sentence continuations
- Flat list of all phrases combined — do NOT group by word

Return ONLY a single JSON array of all phrases."""

    results = _call_llm(
        client, prompt,
        system="You expand words into grammatically correct autocomplete phrase lists. Return only a JSON array.",
        max_tokens=6000,
    )
    log.info("Word expansion produced %d phrases from %d words", len(results), len(top_words))
    return results


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

    # Read all-time known phrases for Phase 3 deduplication
    conn = sqlite3.connect(DB_PATH)
    all_known = [r[0] for r in conn.execute("SELECT phrase FROM phrases").fetchall()]
    conn.close()

    print(f"[+] {len(recent)} recent phrases — running 3 LLM phases in parallel...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(llm_generate, recent)
        f2 = ex.submit(llm_expand_words, recent)
        f3 = ex.submit(llm_predict_new_words, recent, all_known)

        generated = f1.result()
        expanded  = f2.result()
        predicted = f3.result()

    log.info("Phase 1 (style): %d  Phase 2 (expand): %d  Phase 3 (predict): %d",
             len(generated), len(expanded), len(predicted))
    print(f"[+] Phase 1 (style match) : {len(generated)} phrases")
    print(f"[+] Phase 2 (word expand) : {len(expanded)} phrases")
    print(f"[+] Phase 3 (new words)   : {len(predicted)} phrases")

    all_phrases = generated + expanded + predicted
    inserted = insert_phrases(all_phrases)
    log.info("Inserted/updated %d phrases in DB", inserted)
    print(f"[+] Inserted {inserted} total phrases into typing-booster")

    save_state({"last_timestamp": now})
    log.info("Done")


if __name__ == "__main__":
    main()
