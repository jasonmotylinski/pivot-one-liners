#!/usr/bin/env python3
"""Pivot transcript backfill — runs on a cron, retries blocked downloads.

Run from the repo root (any checkout): python3 pipeline/backfill.py
Everything lives in ./data/ (gitignored): transcripts/, videos.txt, cookies.txt.
Overrides via env when needed (YT_DLP); cookies are optional.

Fetches up to MAX_FETCH undownloaded Pivot episodes per run (stops on first
failure, i.e. if YouTube is still blocking this IP). When new transcripts
land, re-runs extraction, promotes high-scoring quotes, regenerates the
site's quotes.js, pushes to GitHub (Pages auto-deploys), and sends Jason a
Telegram notice. Exits 0 silently when blocked.
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent          # repo root (any checkout)
BASE = SITE / "data"                                     # gitignored local data
TRANSCRIPTS = BASE / "transcripts"
VIDEOS = BASE / "videos.txt"
COOKIES = BASE / "cookies.txt"   # optional YouTube session export; chmod 600
QUOTES_JSON = SITE / "quotes_data.json"  # canonical data, committed with the site
YTDLP = os.environ.get("YT_DLP") or shutil.which("yt-dlp") or "yt-dlp"
MAX_FETCH = 3
FETCH_GAP = 45         # seconds between downloads; 8s bursts trip YouTube's rate limiter even with cookies
MIN_SCORE = 9          # auto-promotion threshold
MAX_NEW_PER_RUN = 15

# ---------------------------------------------------------------- helpers

def log(msg):
    print(msg, flush=True)

def load_videos():
    order, titles = [], {}
    for line in VIDEOS.read_text().strip().splitlines():
        vid, _, title = line.partition("|")
        vid = vid.strip()
        order.append(vid)
        titles[vid] = title.strip()
    return order, titles

def fetch_api(vid):
    from http.cookiejar import MozillaCookieJar
    from youtube_transcript_api import YouTubeTranscriptApi
    kwargs = {}
    if COOKIES.exists():
        jar = MozillaCookieJar(str(COOKIES))
        jar.load(ignore_discard=True, ignore_expires=True)
        from requests import Session
        session = Session()
        session.cookies = jar
        kwargs["http_client"] = session
    api = YouTubeTranscriptApi(**kwargs)
    t = api.fetch(vid, languages=["en"])
    return "\n".join(s.text for s in t)

def vtt_to_text(vtt):
    out, prev = [], ""
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or re.match(r"^[\d\s:.,]+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        words = line.split()
        # rolling auto-captions repeat the tail of the previous line
        while words and prev and words[0].lower() == prev.split()[-1].lower():
            words.pop(0)
        if words:
            out.append(" ".join(words))
            prev = line
    return re.sub(r"\s+", " ", " ".join(out)).strip()

def fetch_ytdlp(vid):
    out_base = BASE / "dl_tmp"
    cmd = [YTDLP, "--skip-download", "--write-auto-subs", "--sub-langs", "en",
           "--sub-format", "vtt", "-o", str(out_base)]
    if COOKIES.exists():
        # Cookies + node JS runtime + remote EJS solver: required to pass
        # YouTube's bot-check from this IP (verified working 2026-10-04).
        cmd += ["--cookies", str(COOKIES),
                "--js-runtimes", "node",
                "--remote-components", "ejs:github"]
    cmd.append(f"https://www.youtube.com/watch?v={vid}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    vtt = Path(f"{out_base}.en.vtt")
    if not vtt.exists():
        return None
    text = vtt_to_text(vtt.read_text(errors="replace"))
    vtt.unlink()
    return text or None

# ---------------------------------------------------------- extraction

BOILER = (
    "welcome to pivot", "i'm kara swisher", "this is pivot from",
    "support for pivot", "we'll be right back", "apple podcasts",
    "spotify", "voyage", "click here", "subscribe to", "rating and review",
    "new york magazine and the vox media", "i'm kara",
    # Prof G Pod boilerplate
    "this is the prof g", "office hours with prof g", "prof g pod",
    "i'm scott galloway", "this is scott galloway", "welcome back to",
    "the week with", "raging moderates", "china decode",
    "support for the prof g", "sponsors of", "this episode is brought",
)
MARKERS = (
    "bullish", "bearish", "wake up", "here's the thing", "the reality is",
    "let's be clear", "young people", "gen z", "millennial", "talent",
    "product-market fit", "apprenticeship", "america", "capitalism",
    "housing", "nyc", "new york", "nyu", "mba", "trump", "musk",
    "zuckerberg", "bezos", "cook", "pichai", "nadella", "china", "deepseek",
    "openai", "anthropic", "billionaire", "wealth", "inequality",
    "birth rate", "marriage", "dating", "loneliness", "sigma",
    "elevator pitch", "winners and losers", "prediction", "nvidia",
    "the answer is", "the problem is", "the greatest", "most important",
    "if i were", "the most powerful",
)
TOPIC_MAP = [
    (("ai", "openai", "nvidia", "anthropic", "deepseek"), "AI"),
    (("trump", "biden", "obama", "clinton", "midterm", "election", "democrat", "republican"), "Politics"),
    (("nyc", "new york"), "NYC"),
    (("prediction", "predict"), "Predictions"),
    (("bullish", "bearish", "valuation", "ipo", "stock", "market"), "Markets"),
    (("musk", "zuckerberg", "bezos", "cook", "pichai", "nadella", "ceo"), "Tech CEOs"),
    (("young people", "gen z", "millennial", "dating", "marriage", "loneliness", "birth rate"), "Gen Z"),
    (("housing", "wealth", "income", "inequality", "billionaire", "capitalism"), "Economy"),
    (("china", "tariff", "trade"), "Geopolitics"),
    (("media", "cable", "journalism", "tiktok", "youtube"), "Media"),
]

def turns_from(raw):
    for ch in re.split(r">>", raw):
        t = re.sub(r"\[.*?\]", " ", ch)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            yield t

def normalize(t):
    t = t.lower()
    t = re.sub(r"[^a-z0-9' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def is_candidate(t):
    n = len(t.split())
    if not (6 <= n <= 45) or t.endswith("?"):
        return False
    low = t.lower()
    if any(b in low for b in BOILER) or re.search(r"\bhttp|\bwww\b", low):
        return False
    return True

def extract_candidates():
    cands = []
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
            bonus = 3 if n <= 15 else (2 if n <= 20 else (1 if n <= 28 else 0))
            cands.append({
                "text": turn, "norm": normalize(turn),
                "score": len(hits) * 3 + bonus, "video": vid, "markers": hits,
            })
    return cands

def assign_topic(markers, text):
    hay = " ".join(markers) + " " + text.lower()
    for keys, topic in TOPIC_MAP:
        if any(k in hay for k in keys):
            return topic
    return "Pivot"

# ------------------------------------------------------- site update

def regenerate_quotes_js(quotes):
    lines = [
        "// Scott Galloway one-liners — extracted from Pivot + Prof G Pod transcripts.",
        "// Auto-maintained by pivot-backfill: hand-curated + auto-promoted quotes.",
        "const QUOTES = [",
    ]
    for q in quotes:
        t = json.dumps(q["text"])
        tp = json.dumps(q["topic"])
        lines.append(f"  {{ text: {t}, topic: {tp} }},")
    lines.append("];")
    (SITE / "quotes.js").write_text("\n".join(lines) + "\n")

def git_push(message):
    subprocess.run(["git", "add", "quotes.js", "quotes_data.json"], cwd=SITE, capture_output=True, text=True)
    r = subprocess.run(["git", "commit", "-q", "-m", message], cwd=SITE, capture_output=True, text=True)
    if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
        return False
    r = subprocess.run(["git", "push"], cwd=SITE, capture_output=True, text=True)
    return r.returncode == 0

def notify(text):
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        return
    try:
        data = json.dumps({"chat_id": os.environ.get("TG_CHAT_ID", "1414942425"), "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"notify failed: {e}")

# ------------------------------------------------------------- main

def main():
    order, titles = load_videos()
    have = {f.stem for f in TRANSCRIPTS.glob("*.txt")}
    todo = [v for v in order if v not in have]
    if not todo:
        log(f"ALL_DONE {len(have)} transcripts")
        return

    new_count = 0
    for vid in todo[:MAX_FETCH]:
        text = None
        try:
            text = fetch_api(vid)
        except Exception as e:
            log(f"api fail {vid}: {type(e).__name__}")
        if not text:
            try:
                text = fetch_ytdlp(vid)
            except Exception as e:
                log(f"ytdlp fail {vid}: {type(e).__name__}")
        if not text:
            log(f"BLOCKED after {new_count} new" if new_count else "BLOCKED")
            break
        (TRANSCRIPTS / f"{vid}.txt").write_text(text)
        new_count += 1
        log(f"got {vid} ({len(text.split())} words) {titles.get(vid,'')[:50]}")
        time.sleep(FETCH_GAP)

    if new_count == 0:
        return

    # Re-extract over everything and promote new quotes
    quotes = json.loads(QUOTES_JSON.read_text())
    existing_norms = [normalize(q["text"]) for q in quotes]
    cands = extract_candidates()

    ep_for_norm = defaultdict(set)
    for c in cands:
        ep_for_norm[c["norm"]].add(c["video"])

    def is_dup(norm):
        return any(SequenceMatcher(None, norm, e).ratio() >= 0.72 for e in existing_norms)

    added = 0
    seen = set(existing_norms)
    ranked = sorted(cands, key=lambda c: -(c["score"] + 3 * max(0, len(ep_for_norm[c["norm"]]) - 1)))
    for c in ranked:
        if added >= MAX_NEW_PER_RUN:
            break
        repeats = len(ep_for_norm[c["norm"]])
        if c["score"] < MIN_SCORE and repeats < 2:
            continue
        if c["norm"] in seen or is_dup(c["norm"]):
            continue
        if any(SequenceMatcher(None, c["norm"], s).ratio() >= 0.72 for s in seen):
            continue
        quotes.append({"text": c["text"], "topic": assign_topic(c["markers"], c["text"])})
        seen.add(c["norm"])
        existing_norms.append(c["norm"])
        added += 1

    if added == 0:
        log(f"{new_count} transcripts fetched, no new quotes promoted")
        return

    QUOTES_JSON.write_text(json.dumps(quotes, indent=2))
    regenerate_quotes_js(quotes)
    ok = git_push(f"Pivot backfill: +{new_count} episodes, +{added} quotes")
    log(f"promoted {added} quotes, pushed={'yes' if ok else 'FAILED'}, total={len(quotes)}")
    if ok:
        notify(
            f"🎤 Pivot backfill: +{new_count} episodes, +{added} quotes "
            f"({len(quotes)} total) — site updated.\n"
            f"https://jason.motylinski.com/pivot-one-liners/"
        )

if __name__ == "__main__":
    main()
