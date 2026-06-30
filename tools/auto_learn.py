#!/usr/bin/env python3
"""
Auto-learn: runs periodically, reads what you've been typing from typing-booster,
and asks an LLM for high-confidence completions — steered by a feedback loop.

Strategy (quality over volume):
  - Read WINNERS  : phrases that were actually selected (user_freq > 1)
  - Read LOSERS   : old seeds that were never selected (about to be pruned)
  - Feed both into the prompt so the LLM generates more of what works
    and stops generating what flopped.
  - Generate ~150 high-confidence phrases (not 1200), cap the DB seed pool,
    and run with reasoning OFF for speed/cost.
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

# Keep at most this many unselected LLM seeds in the DB — prevents the
# "landfill" effect where freq=1 noise drowns out real phrases.
MAX_SEEDS = 500
PRUNE_HOURS = 48

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


# ── Feedback loop ─────────────────────────────────────────────────────────────

def read_winners(limit: int = 40) -> list[str]:
    """Phrases that got selected (freq > 1) — hard proof of what the user wants."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT phrase FROM phrases WHERE user_freq > 1 "
        "ORDER BY user_freq DESC, timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def read_losers(older_than_hours: int = PRUNE_HOURS, limit: int = 40) -> list[str]:
    """Old seeds never selected — proof of what to STOP generating.

    Read BEFORE prune_stale() deletes them.
    """
    cutoff = time.time() - older_than_hours * 3600
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT phrase FROM phrases WHERE user_freq = 1 AND timestamp < ? "
        "ORDER BY timestamp ASC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _feedback_block(winners: list[str], losers: list[str]) -> str:
    parts = []
    if winners:
        parts.append(
            "Phrases the user ACTUALLY selected (generate MORE in this style/topic):\n"
            + "\n".join(f"+ {w}" for w in winners[:25])
        )
    if losers:
        parts.append(
            "Phrases generated before but NEVER used (do NOT generate anything like these):\n"
            + "\n".join(f"- {l}" for l in losers[:25])
        )
    return "\n\n".join(parts)


# ── LLM ─────────────────────────────────────────────────────────────────────

def _call_llm(client, prompt: str, system: str, max_tokens: int = 2500,
              timeout: int = 90, reasoning: str = "none") -> list[str]:
    """Shared LLM call — returns a parsed list of strings from a JSON array response.

    reasoning="none" keeps it fast/cheap; phrase generation does not need
    chain-of-thought.
    """
    from daemon.config import load as load_config
    extra = {"extra_body": {"reasoning": {"effort": reasoning}}}
    try:
        resp = client.chat.completions.create(
            model=load_config()["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.5,
            timeout=timeout,
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


def llm_generate(recent_phrases: list[str], winners: list[str], losers: list[str]) -> list[str]:
    """Phase 1 — high-confidence phrases matching the user's style and proven wins."""
    client = _make_client()
    sample = "\n".join(f"- {p}" for p in recent_phrases[:60])
    fb = _feedback_block(winners, losers)
    prompt = f"""\
These are phrases a user recently typed:
{sample}

{fb}

Generate 60 short phrases (2–6 words) they are HIGHLY likely to type next.

Requirements:
- Every phrase MUST be grammatically correct, natural English
- Match their domain, tone, and especially the style of phrases they actually selected
- High confidence only — quality over quantity, no filler or padding
- Each phrase meaningfully distinct

Return ONLY a JSON array of strings."""

    return _call_llm(
        client, prompt,
        system="You generate high-confidence English autocomplete phrases. Return only a JSON array.",
        max_tokens=2000,
    )


def llm_expand_words(recent_phrases: list[str], winners: list[str]) -> list[str]:
    """Phase 2 — for the user's most frequent words, generate a few strong continuations."""
    word_freq: dict[str, int] = {}
    for phrase in recent_phrases:
        for word in re.findall(r"[a-zA-Z]{3,}", phrase):
            word_freq[word.lower()] = word_freq.get(word.lower(), 0) + 1

    # Top 15 words (down from 30) — focus on the words they type most.
    top_words = sorted(word_freq, key=lambda w: -word_freq[w])[:15]
    if not top_words:
        return []

    client = _make_client()
    words_str = ", ".join(f'"{w}"' for w in top_words)
    win_hint = ""
    if winners:
        win_hint = ("\nPhrases the user actually selected (match this style):\n"
                    + "\n".join(f"+ {w}" for w in winners[:15]))

    prompt = f"""\
A user frequently types these words: {words_str}
{win_hint}

For EACH word, generate 3 short, grammatically correct phrases (3–7 words) \
a person would naturally type starting with that word.

Requirements:
- Different realistic contexts per word
- Every phrase grammatically correct and complete-sounding
- Flat list of all phrases combined — do NOT group by word

Return ONLY a single JSON array of all phrases."""

    results = _call_llm(
        client, prompt,
        system="You expand words into grammatically correct autocomplete phrases. Return only a JSON array.",
        max_tokens=2000,
    )
    log.info("Word expansion produced %d phrases from %d words", len(results), len(top_words))
    return results


def llm_predict_new_words(recent_phrases: list[str], all_known: list[str]) -> list[str]:
    """Phase 3 (demoted) — a SMALL set of adjacent vocabulary, measured separately.

    This is the lowest-precision phase, so it's deliberately kept tiny.
    """
    client = _make_client()
    sample = "\n".join(f"- {p}" for p in recent_phrases[:40])
    known_words = set(
        w.lower()
        for p in all_known
        for w in re.findall(r"[a-zA-Z]{3,}", p)
    )
    known_str = ", ".join(sorted(known_words)[:60])

    prompt = f"""\
A user's recent writing (their domain and style):
{sample}

Words they have already typed: {known_str}

Generate 40 short phrases (2–6 words) using closely RELATED vocabulary they \
have not typed yet but will very likely need soon in this exact domain.

Requirements:
- Every phrase MUST be grammatically correct English
- Stay tightly on-domain — no generic filler, no unrelated topics
- Prioritize vocabulary NOT already in their known word list

Return ONLY a JSON array of strings."""

    results = _call_llm(
        client, prompt,
        system="You predict tightly on-domain autocomplete phrases. Return only a JSON array.",
        max_tokens=1500,
    )
    log.info("New-word prediction produced %d phrases", len(results))
    return results


# ── DB writes ─────────────────────────────────────────────────────────────────

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
            # Boost frequency slightly — LLM suggested it again, so it's relevant
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


def prune_stale(older_than_hours: int = PRUNE_HOURS) -> int:
    """Delete freq=1 phrases added more than N hours ago and never selected."""
    cutoff = time.time() - (older_than_hours * 3600)
    conn = sqlite3.connect(DB_PATH)
    result = conn.execute(
        "DELETE FROM phrases WHERE user_freq = 1 AND timestamp < ?", (cutoff,)
    )
    deleted = result.rowcount
    conn.commit()
    conn.close()
    return deleted


def cap_seeds(max_seeds: int = MAX_SEEDS) -> int:
    """Keep at most max_seeds freq=1 LLM phrases; delete the oldest beyond that."""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM phrases WHERE user_freq = 1").fetchone()[0]
    deleted = 0
    if total > max_seeds:
        excess = total - max_seeds
        conn.execute(
            "DELETE FROM phrases WHERE id IN ("
            "  SELECT id FROM phrases WHERE user_freq = 1 ORDER BY timestamp ASC LIMIT ?"
            ")",
            (excess,),
        )
        deleted = excess
        conn.commit()
    conn.close()
    return deleted


# ── Main ───────────────────────────────────────────────────────────────────────

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
    since = state.get("last_timestamp", 0.0)
    now = time.time()

    recent = read_recent_phrases(since_ts=since)

    # Feedback signals — read losers BEFORE pruning them away.
    winners = read_winners()
    losers = read_losers()
    print(f"[i] Feedback: {len(winners)} proven-useful phrases, {len(losers)} flopped seeds to avoid")
    log.info("Feedback: winners=%d losers=%d recent=%d", len(winners), len(losers), len(recent))

    pruned = prune_stale()
    if pruned:
        log.info("Pruned %d stale phrases", pruned)
        print(f"[+] Pruned {pruned} unused seeds (>{PRUNE_HOURS}h, never selected)")

    conn = sqlite3.connect(DB_PATH)
    seed_total = conn.execute("SELECT COUNT(*) FROM phrases WHERE user_freq = 1").fetchone()[0]
    all_known = [r[0] for r in conn.execute("SELECT phrase FROM phrases").fetchall()]
    conn.close()

    # Activity gate: don't burn API when there's nothing new and the DB is
    # already well-seeded. Still cap the pool on the way out.
    if not recent and seed_total >= 200:
        capped = cap_seeds()
        msg = f"[=] No new typing and {seed_total} seeds present — skipping generation."
        if capped:
            msg += f" Capped {capped} oldest excess seeds."
        print(msg)
        log.info("Skipped generation (no new typing, %d seeds)", seed_total)
        save_state({"last_timestamp": now, "seeded_count": 0, "seed_total": seed_total})
        return

    print(f"[+] {len(recent)} new phrases — generating (feedback-steered, reasoning off)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(llm_generate, recent, winners, losers)
        f2 = ex.submit(llm_expand_words, recent, winners)
        f3 = ex.submit(llm_predict_new_words, recent, all_known)

        generated = f1.result()
        expanded = f2.result()
        predicted = f3.result()

    log.info("Phase 1 (style): %d  Phase 2 (expand): %d  Phase 3 (predict): %d",
             len(generated), len(expanded), len(predicted))
    print(f"[+] Phase 1 (style + feedback) : {len(generated)} phrases")
    print(f"[+] Phase 2 (word expand)      : {len(expanded)} phrases")
    print(f"[+] Phase 3 (adjacent vocab)   : {len(predicted)} phrases")

    all_phrases = generated + expanded + predicted
    inserted = insert_phrases(all_phrases)

    capped = cap_seeds()
    if capped:
        print(f"[+] Capped DB seed pool — removed {capped} oldest excess seeds")

    log.info("Inserted/updated %d phrases in DB (capped %d)", inserted, capped)
    print(f"[+] Inserted {inserted} total phrases into typing-booster")

    save_state({
        "last_timestamp": now,
        "seeded_count": inserted,
        "winners": len(winners),
        "losers": len(losers),
    })
    log.info("Done")


if __name__ == "__main__":
    main()
