# Claude Token Usage Dashboard

A local dashboard that shows your real Claude Code token usage, cost, and activity — pulled directly from your `~/.claude/projects/` session logs. Supports week, month, quarter, year, and all-time views. Switch ranges in the UI without re-running the script.

> **Note:** Only Claude Code sessions are available locally via JSONL logs. Claude.ai Chat and Cowork sessions are server-side only and cannot be parsed locally.

![Dashboard shows weekly token totals, cost, trend chart, heatmap, and session table]

---

## Files

| File | Purpose |
|---|---|
| `Token Usage.html` | The dashboard — open this in any browser |
| `generate_usage_data.py` | Reads your Claude logs and writes `live_usage.js` |
| `live_usage.js` | Generated data file loaded by the dashboard |
| `data/archive.json` | All sessions ever seen, merged across runs |
| `data/last_sync.txt` | Timestamp of last sync — only newer JSONL entries are re-read |

---

## Quickstart (your own machine)

**Requirements:** Python 3.10+

### 1. Generate your usage data

Run the script once (defaults to `--range week`):

```bash
cd /path/to/ClaudeTokens

python3 generate_usage_data.py               # defaults to --range week
python3 generate_usage_data.py --range month
python3 generate_usage_data.py --range last30
python3 generate_usage_data.py --range quarter
python3 generate_usage_data.py --range year
python3 generate_usage_data.py --range alltime
```

You'll see a summary like:

```
Scanning : /Users/you/.claude/projects
Last sync: Apr 18 2026 13:21 UTC
Archive  : 5 sessions on disk
New turns: 12 across 2 sessions
Merged   : +1 new  ~1 updated  →  6 total

──────────────────────────────────────────────
  Range     This week
  Week starts: 2026-04-13, Today: 2026-04-18, Days included: 6
  Sessions  4
  Tokens         1,234,567
  Cost              $12.34
  vs prev           +22.1%
──────────────────────────────────────────────
  Earliest session: 2026-03-22
  Latest session:   2026-04-18
──────────────────────────────────────────────
  Archive → data/archive.json
  Output  → live_usage.js
```

The first run does a full scan. Every subsequent run only reads JSONL entries newer than the last sync timestamp, so it stays fast as your history grows.

### 2. Open the dashboard

Double-click `Token Usage.html` — it opens directly in your browser, no server needed.

> **Range switching works in the UI.** The script exports your full session archive; the dashboard filters and re-buckets data client-side when you switch ranges. You only need to re-run the script to pick up new sessions — not to change the date range.

---

## Sharing with someone else

The dashboard reads from `~/.claude/projects/` which is specific to your machine.
To share with another person:

### Option A — Share just the dashboard with your data baked in

1. Run `python3 generate_usage_data.py` on your machine
2. Send them both files: `Token Usage.html` + `live_usage.js`
3. They open `Token Usage.html` — your data loads automatically, no Python needed

### Option B — Let them run it on their own machine

1. Send them: `Token Usage.html` + `generate_usage_data.py`
2. They run `python3 generate_usage_data.py` on their machine
3. Their own `~/.claude/projects/` logs are parsed
4. They open `Token Usage.html` to see their own usage

---

## How the data is parsed

The script reads every `.jsonl` file under `~/.claude/projects/` recursively.
It only processes lines where `type == "assistant"` and extracts:

| Field | Source |
|---|---|
| Session ID | `sessionId` |
| Title | Last folder name of `cwd` |
| Model | `message.model` (matched as sonnet / opus / haiku) |
| Input tokens | `message.usage.input_tokens` |
| Output tokens | `message.usage.output_tokens` |
| Cache read tokens | `message.usage.cache_read_input_tokens` |
| Cache write tokens | `message.usage.cache_creation_input_tokens` |

Sessions are grouped by `sessionId`. The full archive is always written to `live_usage.js` so the UI can filter any range without re-running the script.

---

## Cost rates

| Model | Input | Cache write | Cache read | Output |
|---|---|---|---|---|
| Sonnet | $3.00/M | $3.75/M | $0.30/M | $15.00/M |
| Opus | $15.00/M | $18.75/M | $1.50/M | $75.00/M |
| Haiku | $0.80/M | $1.00/M | $0.08/M | $4.00/M |

These are the API pay-as-you-go rates used to compute your **API-equivalent cost** — what you would have paid without a Pro subscription. Your actual plan cost is separate (see Plan Value below).

Cache tokens are priced separately from regular input tokens because they're cheaper to read and slightly more expensive to write.

---

## Dashboard features

- **Range picker** — switch between This week / This month / Last 30 days / This quarter / This year / All time directly in the UI — no script re-run needed
- **Model tabs** — filter all charts and the session table by Sonnet / Opus / Haiku
- **Hero card** — total tokens + API-equivalent spend, delta vs prior period, plan value ratio, monthly plan progress bar
- **Plan value** — shows how much API value you extract per $1 of your Pro subscription (green = above 1×, red = below); based on configurable monthly plan cost (default $20)
- **Trend chart** — stacked bars (input / output) bucketed by day, week, or month depending on range; 95th-percentile scale ceiling so one spike doesn't flatten all other bars; hover for tooltip; ▲ indicator on capped bars
- **Alerts** — heavy session warnings, budget warnings
- **Surface & model breakdown** — bar charts showing split by surface and model
- **Activity heatmap** — 7 × 24 grid showing when you use Claude most; mean + 2σ intensity normalization so variation is visible even when usage is uneven
- **Sessions table** — sortable, filterable, searchable list of every chat
- **Session drawer** — click any row for per-turn breakdown and timing
- **Dark / light mode** — toggle in the top bar, persisted across reloads
- **Tweaks panel** — density, accent colour, monthly plan budget slider (persisted in localStorage); shows daily and weekly allowance breakdown

---

## Troubleshooting

**Dashboard shows "Could not load live_usage.js"**
→ Run `python3 generate_usage_data.py` first, then reload the page.

**Script shows 0 sessions**
→ No sessions in the archive fall within the selected range. Try `--range alltime` to see everything, then narrow down.

**Switching the range in the UI shows no data**
→ Re-run `python3 generate_usage_data.py` (any `--range`) then reload the page. The script must run at least once to populate `live_usage.js` with your full archive.

**Python error: `datetime | None` syntax**
→ You need Python 3.10+. Check with `python3 --version`.
