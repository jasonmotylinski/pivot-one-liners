# Pivot + Prof G one-liners

Random Scott Galloway one-liner on every refresh, extracted from YouTube captions of **Pivot** (Kara Swisher & Scott Galloway) and **The Prof G Pod**.

**Live:** https://jason.motylinski.com/pivot-one-liners/

## The site

Static — no build step, no server:

- `index.html` — quote card, random pick on load, share button (native share sheet / clipboard)
- `quotes.js` — generated quote data, loaded directly by the page
- `quotes_data.json` — canonical quote source; the pipeline regenerates `quotes.js` from this

## The pipeline (`pipeline/`)

Automated transcript backfill and quote extraction:

- `backfill.py` — the cron worker. Fetches up to 10 undownloaded episodes per run via yt-dlp with YouTube session cookies (+ node JS runtime + remote EJS solver to pass the bot-check), re-runs extraction over all transcripts, auto-promotes high-scoring quotes (cross-episode repeats weighted), regenerates `quotes.js`, pushes to GitHub (Pages auto-deploys), and sends a Telegram notification. Fails silently when YouTube blocks.
- `extract_candidates.py` — one-shot curation tool used for the initial hand-review pass: splits captions on `>>` speaker markers, filters boilerplate, scores Scott-flavored lexicon hits, dumps ranked candidates for review.

### Running it yourself

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r pipeline/requirements.txt

# one-shot extraction over local transcripts
python pipeline/extract_candidates.py

# full fetch + promote + push cycle
python pipeline/backfill.py
```

Everything lives in `./data/` (gitignored) — transcripts, the episode queue, cookies. Optional env overrides: `YT_DLP` (yt-dlp binary), `TG_BOT_TOKEN` / `TG_CHAT_ID` (Telegram notification; skipped when unset).

Data lives in `./data/` (gitignored, never committed):

| Path | Contents |
|---|---|
| `data/transcripts/` | downloaded caption text, one file per video |
| `data/videos.txt` | episode queue (`id\|date\|title`, newest first) |
| `data/cookies.txt` | YouTube session export (chmod 600, never commit) |

### Getting past YouTube's bot-check

Anonymous requests from cloud IPs are hard-blocked. The working recipe:

1. Export cookies from a YouTube-logged-in browser ("Get cookies.txt LOCALLY" extension)
2. Save as `data/cookies.txt`
3. yt-dlp needs `--cookies data/cookies.txt --js-runtimes node --remote-components ejs:github`

The official YouTube Data API v3 does not expose third-party captions — cookies are the only unauthenticated-session-equivalent route.

## Notes

- Quotes are auto-captions: lightly cleaned of filler/false starts, otherwise verbatim — expect occasional garble.
- Auto-promotion is a convenience; hand-curated quotes are the quality bar. Review candidates after big backfills.
- Interview-heavy shows (Prof G Conversations, China Decode) mix guest lines in — the lexicon scoring filters most, not all.
