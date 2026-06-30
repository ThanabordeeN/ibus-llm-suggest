#!/usr/bin/env python3
"""
Learn from typing history and inject phrases into ibus-typing-booster.

Usage:
  # From a text file
  python3 tools/learn_from_text.py mytext.txt

  # From stdin
  cat mytext.txt | python3 tools/learn_from_text.py

  # Dry-run (show what would be inserted, don't write)
  python3 tools/learn_from_text.py mytext.txt --dry-run

  # LLM generate extra phrases too
  python3 tools/learn_from_text.py mytext.txt --llm-expand
"""
import sys
import os
import re
import time
import sqlite3
import argparse
import json
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.expanduser("~/.local/share/ibus-typing-booster/user.db")
CHUNK_SIZE = 2000   # chars per LLM call


# ── Text extraction ──────────────────────────────────────────────────────────

def tokenize(text: str) -> list[str]:
    """Split text into word tokens, keeping Thai and Latin words."""
    # Keep Thai unicode block + alphanumeric + apostrophe
    tokens = re.findall(r"[฀-๿]+|[a-zA-Z0-9']+", text)
    return [t for t in tokens if len(t) > 1]


def extract_ngrams(tokens: list[str]) -> list[tuple]:
    """
    Returns list of (input_phrase, phrase, p_phrase, pp_phrase, freq).
    Mirrors the format typing-booster expects.
    """
    freq_uni: Counter = Counter()
    freq_bi: Counter = Counter()

    for i, tok in enumerate(tokens):
        freq_uni[tok] += 1
        if i > 0:
            bigram = f"{tokens[i-1]} {tok}"
            freq_bi[bigram] += 1

    rows = []

    # Unigrams
    for phrase, freq in freq_uni.items():
        rows.append((phrase, phrase, "", "", freq))

    # Bigrams: "how many" with context of what came before "how"
    for bigram, freq in freq_bi.items():
        w1, w2 = bigram.split(" ", 1)
        rows.append((bigram, bigram, "", "", freq))

    return rows


# ── LLM expansion ────────────────────────────────────────────────────────────

def llm_expand(sample_text: str) -> list[str]:
    """Ask LLM to generate additional phrases matching the style of the text."""
    from daemon.config import load as load_config
    import openai

    cfg = load_config()
    client = openai.OpenAI(
        api_key=cfg["api_key"] or "ollama",
        base_url=cfg["base_url"],
    )

    # Trim sample to reasonable size
    sample = sample_text[:CHUNK_SIZE]

    extra = {}
    if cfg.get("disable_reasoning", True):
        extra["extra_body"] = {"reasoning": {"effort": "none"}}

    prompt = f"""\
Below is a sample of text from someone's writing history.
Generate 80 phrases (2-6 words each) that this person would likely type frequently.
Match their language, style, and domain exactly — mix of Thai and English if present.
Return ONLY a JSON array of strings.

Sample:
{sample}"""

    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": "You generate autocomplete phrase lists. Return only JSON arrays."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
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
        print(f"[LLM error] {e}", file=sys.stderr)

    return []


# ── Database insertion ────────────────────────────────────────────────────────

def insert_rows(rows: list[tuple], dry_run: bool = False) -> int:
    """
    rows: list of (input_phrase, phrase, p_phrase, pp_phrase, freq)
    Returns count of inserted/updated rows.
    """
    if dry_run:
        for r in rows[:20]:
            print(f"  WOULD INSERT: {r}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        return len(rows)

    conn = sqlite3.connect(DB_PATH)
    now = time.time()
    inserted = 0

    for input_phrase, phrase, p_phrase, pp_phrase, freq in rows:
        existing = conn.execute(
            "SELECT id, user_freq FROM phrases WHERE input_phrase=? AND phrase=? AND p_phrase=? AND pp_phrase=?",
            (input_phrase, phrase, p_phrase, pp_phrase),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE phrases SET user_freq=?, timestamp=? WHERE id=?",
                (existing[1] + freq, now, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO phrases (input_phrase, phrase, p_phrase, pp_phrase, user_freq, timestamp) VALUES (?,?,?,?,?,?)",
                (input_phrase, phrase, p_phrase, pp_phrase, freq, now),
            )
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def phrases_to_rows(phrases: list[str]) -> list[tuple]:
    """Convert plain phrase list (from LLM) into DB rows."""
    rows = []
    for phrase in phrases:
        tokens = tokenize(phrase)
        if not tokens:
            continue
        # Single entry: input = first token or full phrase
        rows.append((tokens[0], phrase, "", "", 1))
        # Also store full phrase as input_phrase for direct lookup
        if len(tokens) > 1:
            rows.append((phrase, phrase, "", "", 1))
    return rows


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Learn phrases from text into typing-booster")
    parser.add_argument("file", nargs="?", help="Text file to learn from (default: stdin)")
    parser.add_argument("--llm-expand", action="store_true", help="Use LLM to generate extra phrases")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be inserted, don't write")
    parser.add_argument("--min-freq", type=int, default=1, help="Min frequency to include a phrase (default: 1)")
    args = parser.parse_args()

    # Read input text
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("No text provided.", file=sys.stderr)
        sys.exit(1)

    print(f"[+] Read {len(text)} chars of text")

    # Extract ngrams from the text
    tokens = tokenize(text)
    print(f"[+] Tokenized: {len(tokens)} tokens")

    ngram_rows = [r for r in extract_ngrams(tokens) if r[4] >= args.min_freq]
    print(f"[+] Extracted {len(ngram_rows)} ngrams (min_freq={args.min_freq})")

    # LLM expansion
    llm_rows = []
    if args.llm_expand:
        print("[+] Calling LLM to generate related phrases...")
        llm_phrases = llm_expand(text)
        llm_rows = phrases_to_rows(llm_phrases)
        print(f"[+] LLM generated {len(llm_phrases)} phrases → {len(llm_rows)} rows")

    all_rows = ngram_rows + llm_rows
    print(f"[+] Total rows to insert: {len(all_rows)}")

    count = insert_rows(all_rows, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\n[DRY RUN] Would insert {count} rows into {DB_PATH}")
    else:
        print(f"[+] Done — inserted/updated {count} rows in {DB_PATH}")
        print("[+] Restart ibus-typing-booster or run: ibus restart")


if __name__ == "__main__":
    main()
