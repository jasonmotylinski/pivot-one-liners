#!/home/jason/.local/venvs/ytdl/bin/python
"""Extract Galloway one-liner candidates v3.

v2 bug: my reflow regex realigned '>>' markers so text preceded them.
v3 splits ON '>>' directly — each chunk is one speaker turn.
Dumps scored candidates for LLM curation into quotes.json.
"""
import json
import re
from pathlib import Path

TRANSCRIPTS = Path("/home/jason/pivot-backfill/transcripts")
META = Path("/tmp/pivot_videos_raw.txt")
OUT = Path("/tmp/pivot_candidates_v3.json")
TOP_TXT = Path("/tmp/pivot_candidates_top.txt")

titles = {}
for line in META.read_text().strip().splitlines():
    vid, _, title = line.partition("|")
    titles[vid.strip()] = title.strip()

def turns_from(raw: str):
    """Split transcript into cleaned speaker turns."""
    for ch in re.split(r">>", raw):
        t = re.sub(r"\[.*?\]", " ", ch)      # [laughter] [music]
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            yield t

def normalize(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[^a-z0-9' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

BOILER = (
    "welcome to pivot", "i'm kara swisher", "this is pivot from",
    "support for pivot", "we'll be right back", "apple podcasts",
    "spotify", "voyage", "click here", "subscribe to", "rating and review",
    "new york magazine and the vox media", "i'm kara",
)

MARKERS = (
    "bullish", "bearish", "i'm bullish", "i am bullish", "wake up",
    "here's the thing", "here is the thing", "the reality is", "let's be clear",
    "young people", "gen z", "millennial", "military-age", "talent",
    "product-market fit", "apprenticeship", "mentorship", "purpose",
    "america", "capitalism", "social contract", "housing", "homeownership",
    "nyc", "new york", "nyu", "stanford", "harvard", "mba",
    "trump", "musk", "zuckerberg", "bezos", "cook", "pichai", "nadella",
    "sanders", "warren", "biden", "obama", "clinton",
    "china", "deepseek", "openai", "anthropic", "open ai",
    "billionaire", "rich people", "wealth", "income", "inequality",
    "birth rate", "marriage", "dating", "loneliness", "bro", "sigma",
    "gym", "discipline", "stoic", "therapy",
    "elevator pitch", "winners and losers", "prediction", "big new thing",
    "faang", "mag seven", "mags", "nvidia", "ai data center",
    "the answer is", "the answer to", "my prediction", "i predict",
    "we need to", "the problem is", "the greatest",
    "number one", "most important", "if i were", "the most powerful",
    "love story", "it's not", "people who", "the kids",
)

def is_candidate(t: str) -> bool:
    n = len(t.split())
    if not (6 <= n <= 45):
        return False
    if t.endswith("?"):
        return False
    low = t.lower()
    if any(b in low for b in BOILER):
        return False
    if re.search(r"\bhttp|\bwww\b", low):
        return False
    return True

candidates = []
for f in sorted(TRANSCRIPTS.glob("*.txt")):
    vid = f.stem
    for turn in turns_from(f.read_text(errors="replace")):
        if not is_candidate(turn):
            continue
        low = turn.lower()
        hits = [m for m in MARKERS if m in low]
        if not hits:
            continue
        n = len(turn.split())
        length_bonus = 3 if n <= 15 else (2 if n <= 20 else (1 if n <= 28 else 0))
        score = len(hits) * 3 + length_bonus
        candidates.append({
            "text": turn,
            "norm": normalize(turn),
            "score": score,
            "markers": hits,
            "words": n,
            "video": vid,
            "title": titles.get(vid, ""),
        })

candidates.sort(key=lambda c: -c["score"])
OUT.write_text(json.dumps(candidates, indent=2))
print(f"candidates: {len(candidates)}")

lines = []
for i, c in enumerate(candidates[:200], 1):
    lines.append(f"{i:3}. [{c['score']:>3}|{c['words']:>2}w] {c['text']}")
TOP_TXT.write_text("\n".join(lines))
print(f"wrote {TOP_TXT} ({len(lines)} lines)")
print("\n".join(lines[:30]))
