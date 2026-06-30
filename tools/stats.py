#!/usr/bin/env python3
"""
Show whether auto-learn is actually helping.

Usage:
  python3 tools/stats.py
"""
import sys, os, sqlite3, json, time
from datetime import datetime

DB_PATH   = os.path.expanduser("~/.local/share/ibus-typing-booster/user.db")
STATE_PATH = os.path.expanduser("~/.local/share/llm-ibus/last_learn.json")
LOG_PATH  = os.path.expanduser("~/.local/share/llm-ibus/auto_learn.log")

def ts(t): return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")

conn = sqlite3.connect(DB_PATH)

# ── 1. Overall DB size ────────────────────────────────────────────────────────
total   = conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]
human   = conn.execute("SELECT COUNT(*) FROM phrases WHERE user_freq >= 2").fetchone()[0]
llm_gen = conn.execute("SELECT COUNT(*) FROM phrases WHERE user_freq = 1").fetchone()[0]

print("═" * 55)
print("  typing-booster DB stats")
print("═" * 55)
print(f"  Total phrases        : {total}")
print(f"  Typed by you (freq≥2): {human}  ← things you actually typed")
print(f"  LLM-generated (freq=1): {llm_gen}  ← added by auto-learn")

# ── 2. LLM-generated phrases that got SELECTED (freq bumped to ≥2) ───────────
# We can't perfectly track which ones came from LLM vs human, but we can
# look at growth over time from the log
print()
print("─" * 55)
print("  Recent auto-learn runs  (from log)")
print("─" * 55)
if os.path.exists(LOG_PATH):
    lines = open(LOG_PATH).readlines()
    runs = [l for l in lines if "Inserted" in l or "skipping" in l.lower() or "Done" in l]
    for l in runs[-10:]:
        print(" ", l.rstrip())
else:
    print("  No log yet.")

# ── 3. Top phrases YOU actually type (= the useful ones) ─────────────────────
print()
print("─" * 55)
print("  Top 20 most-used phrases (you selected these)")
print("─" * 55)
rows = conn.execute(
    "SELECT phrase, user_freq, timestamp FROM phrases ORDER BY user_freq DESC LIMIT 20"
).fetchall()
for phrase, freq, ts_ in rows:
    print(f"  [{freq:3d}x]  {phrase}  ({ts(ts_)})")

# ── 4. Most recently added LLM phrases ───────────────────────────────────────
print()
print("─" * 55)
print("  Last 20 phrases added by auto-learn")
print("─" * 55)
rows = conn.execute(
    "SELECT phrase, user_freq, timestamp FROM phrases ORDER BY timestamp DESC LIMIT 20"
).fetchall()
for phrase, freq, ts_ in rows:
    marker = "✓ USED" if freq >= 2 else "·"
    print(f"  {marker}  {phrase!r}")

# ── 5. Hit rate: LLM phrases that got used ───────────────────────────────────
# Approximate: phrases with freq=1 added recently vs those bumped higher
state = {}
if os.path.exists(STATE_PATH):
    state = json.load(open(STATE_PATH))
last_run = state.get("last_timestamp", 0)

if last_run:
    added_since = conn.execute(
        "SELECT COUNT(*) FROM phrases WHERE timestamp > ?", (last_run - 3600,)
    ).fetchone()[0]
    used_since = conn.execute(
        "SELECT COUNT(*) FROM phrases WHERE timestamp > ? AND user_freq >= 2", (last_run - 3600,)
    ).fetchone()[0]
    print()
    print("─" * 55)
    print(f"  Since last run ({ts(last_run - 3600)})")
    print("─" * 55)
    print(f"  Phrases added         : {added_since}")
    print(f"  Of those, you used    : {used_since}")
    hit = f"{used_since/added_since*100:.1f}%" if added_since else "n/a"
    print(f"  Hit rate              : {hit}")
    print()
    if used_since == 0 and added_since > 0:
        print("  ⚠ None of the LLM phrases were selected yet.")
        print("    This could mean:")
        print("    - You haven't typed enough since the last run")
        print("    - LLM is generating irrelevant phrases")
        print("    - typing-booster isn't ranking them high enough")
    elif used_since > 0:
        print(f"  ✓ {used_since} LLM-generated phrases were actually selected!")

print("═" * 55)
conn.close()
