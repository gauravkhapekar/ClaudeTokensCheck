#!/usr/bin/env python3
# NOTE: Only Claude Code sessions are available locally via JSONL logs.
# Claude.ai Chat and Cowork sessions are server-side only and cannot be parsed locally.
"""
generate_usage_data.py

Incremental sync workflow:
  1. Read data/last_sync.txt → only parse JSONL entries newer than that timestamp
  2. Merge new turns into data/archive.json (add new sessions, update existing ones)
  3. Write now → data/last_sync.txt
  4. Build live_usage.js for the current week from the full archive
"""

import argparse
import json
import os
import platform
import sys  # noqa: F401 — kept for callers that may use sys.executable
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path

# ── OS detection ──────────────────────────────────────────────────────────────

OS = platform.system()  # 'Windows', 'Darwin', 'Linux'

# Cross-platform day format: removes leading zero on both Windows and Unix
_DAY_FMT = '%#d' if OS == 'Windows' else '%-d'

def fmt_month_day(dt):
    """Return e.g. 'Apr 7' with no leading zero on all platforms. Accepts date or datetime."""
    return dt.strftime(f'%b {_DAY_FMT}')

def parse_local_date(iso_string):
    """Convert an ISO 8601 timestamp to the user's local calendar date."""
    dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
    return dt.astimezone().date()  # converts UTC → local timezone

def local_midnight(d):
    """Return a timezone-aware datetime at midnight local time on the given date."""
    return datetime.combine(d, datetime.min.time()).astimezone()

# ── Paths ─────────────────────────────────────────────────────────────────────

# Locate ~/.claude/projects cross-platform (Windows may use USERPROFILE)
_claude_default = Path.home() / '.claude' / 'projects'
if not _claude_default.exists() and OS == 'Windows':
    _claude_default = Path(os.environ.get('USERPROFILE', str(Path.home()))) / '.claude' / 'projects'
JSONL_DIR   = _claude_default

DATA_DIR    = Path(__file__).parent / "data"
LAST_SYNC   = DATA_DIR / "last_sync.txt"
ARCHIVE     = DATA_DIR / "archive.json"
OUTPUT_FILE = Path(__file__).parent / "live_usage.js"

# ── Constants ─────────────────────────────────────────────────────────────────

# Known surface definitions — extend this dict when new surfaces become available.
# Surfaces are detected dynamically from parsed sessions, not hardcoded into output.
SURFACE_DEFS = {
    "claudecode": {"label": "Claude Code", "color": "#4f46e5"},
}

MODELS = [
    {"id": "sonnet", "label": "Sonnet", "color": "#4f46e5"},
    {"id": "opus",   "label": "Opus",   "color": "#7c3aed"},
    {"id": "haiku",  "label": "Haiku",  "color": "#06b6d4"},
]

PRICE = {
    "sonnet": {"in": 3.00,  "cache_creation": 3.75,  "cache_read": 0.30,  "out": 15.00},
    "opus":   {"in": 15.00, "cache_creation": 18.75, "cache_read": 1.50,  "out": 75.00},
    "haiku":  {"in": 0.80,  "cache_creation": 1.00,  "cache_read": 0.08,  "out": 4.00},
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def model_key(model_str):
    m = (model_str or "").lower()
    if "opus"  in m: return "opus"
    if "haiku" in m: return "haiku"
    return "sonnet"

def parse_iso(ts, filename=None):
    """Parse an ISO 8601 timestamp string. Never treats ts as a Unix epoch integer."""
    if not ts:
        return None
    raw = str(ts)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.year < 2023:
        loc = f" in file {filename}" if filename else ""
        print(f"WARNING: Suspicious timestamp found: {raw}{loc}")
        return None
    return dt

def week_bounds(ref):
    monday = (ref - timedelta(days=ref.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=7)

def turn_cost(usage, model):
    r = PRICE[model]
    return (
        (usage.get("input_tokens",               0) or 0) / 1e6 * r["in"]
      + (usage.get("cache_creation_input_tokens", 0) or 0) / 1e6 * r["cache_creation"]
      + (usage.get("cache_read_input_tokens",     0) or 0) / 1e6 * r["cache_read"]
      + (usage.get("output_tokens",              0) or 0) / 1e6 * r["out"]
    )

def build_surfaces(sessions):
    """Detect surfaces that actually appear in sessions; fall back to SURFACE_DEFS for metadata."""
    seen = []
    for s in sessions:
        sid = s.get("surface", "claudecode")
        if sid not in seen:
            seen.append(sid)
    result = []
    for sid in seen:
        defn = SURFACE_DEFS.get(sid, {"label": sid, "color": "#6b7280"})
        result.append({"id": sid, **defn})
    return result

# ── Persistence helpers ───────────────────────────────────────────────────────

def read_last_sync():
    """Return last sync datetime, or epoch (= first run, parse everything)."""
    if LAST_SYNC.exists():
        ts = parse_iso(LAST_SYNC.read_text(encoding="utf-8").strip())
        if ts:
            return ts
    return datetime.fromtimestamp(0, tz=timezone.utc)

def read_archive():
    """Return archive dict {sessionId: session_object}, or {} if none yet."""
    if ARCHIVE.exists():
        try:
            return json.loads(ARCHIVE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def write_archive(archive):
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE.write_text(json.dumps(archive, indent=2), encoding="utf-8")

def write_last_sync(ts):
    DATA_DIR.mkdir(exist_ok=True)
    LAST_SYNC.write_text(ts.isoformat(), encoding="utf-8")

# ── Scanning ──────────────────────────────────────────────────────────────────

def scan_new_turns(since):
    """
    Walk all *.jsonl files under JSONL_DIR.
    Only collect type=='assistant' entries with timestamp > since.
    Returns {sessionId: [turn, ...]} with _ts already parsed onto each turn.
    """
    buckets = defaultdict(list)
    for fpath in sorted(JSONL_DIR.rglob("*.jsonl")):
        try:
            with open(fpath, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "assistant":
                        continue
                    sid = entry.get("sessionId")
                    if not sid:
                        continue
                    ts = parse_iso(entry.get("timestamp", ""), filename=fpath)
                    if ts is None or ts <= since:
                        continue

                    entry["_ts"] = ts
                    buckets[sid].append(entry)
        except OSError:
            continue
    return buckets

# ── Session building & merging ────────────────────────────────────────────────

def aggregate_turns(turns):
    """Sum tokens/cost from a list of assistant turns. Returns a partial dict."""
    total_input  = total_output = total_cost = 0
    model_votes  = defaultdict(int)
    daily        = defaultdict(lambda: {"inputTokens": 0, "outputTokens": 0, "cost": 0.0})

    for t in turns:
        msg   = t.get("message", {})
        usage = msg.get("usage", {})
        mk    = model_key(msg.get("model", ""))
        model_votes[mk] += 1
        in_tok  = usage.get("input_tokens",  0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        c       = turn_cost(usage, mk)
        total_input  += in_tok
        total_output += out_tok
        total_cost   += c
        # Track per-day breakdown using the turn's local date
        local_date = t["_ts"].astimezone().date().isoformat()
        daily[local_date]["inputTokens"]  += in_tok
        daily[local_date]["outputTokens"] += out_tok
        daily[local_date]["cost"]         += c

    model = max(model_votes, key=model_votes.get) if model_votes else "sonnet"
    return {"inputTokens": total_input, "outputTokens": total_output,
            "totalTokens": total_input + total_output, "cost": total_cost,
            "model": model, "turns": len(turns), "dailyTokens": dict(daily)}

def title_from_turns(sid, turns):
    for t in reversed(turns):
        cwd = t.get("cwd", "").strip()
        if cwd:
            return Path(cwd).name or sid[:12]
    return sid[:12]

def merge_into_archive(archive, new_buckets):
    """
    For each sessionId in new_buckets:
      - If not in archive → create a fresh session entry.
      - If already in archive → add the new turns' tokens/cost on top.
    Returns (added, updated) counts.
    """
    added = updated = 0

    for sid, turns in new_buckets.items():
        if not turns:
            continue

        turns_sorted = sorted(turns, key=lambda e: e["_ts"])
        agg          = aggregate_turns(turns_sorted)
        first_ts     = turns_sorted[0]["_ts"]
        last_ts      = turns_sorted[-1]["_ts"]

        if sid not in archive:
            # Brand-new session
            total_tokens = agg["totalTokens"]
            archive[sid] = {
                "id":           sid,
                "title":        title_from_turns(sid, turns_sorted),
                "surface":      "claudecode",
                "model":        agg["model"],
                "turns":        agg["turns"],
                "start":        first_ts.isoformat(),
                "end":          last_ts.isoformat(),
                "durationMin":  max(1, round((last_ts - first_ts).total_seconds() / 60)),
                "inputTokens":  agg["inputTokens"],
                "outputTokens": agg["outputTokens"],
                "totalTokens":  total_tokens,
                "cost":         agg["cost"],
                "spike":        False,
                "heavy":        total_tokens > 280_000,
                "dayIndex":     first_ts.astimezone().weekday(),  # local-time weekday, Mon=0
                "dailyTokens":  agg["dailyTokens"],
            }
            added += 1
        else:
            # Existing session — accumulate new turns on top
            ex = archive[sid]
            ex["inputTokens"]  += agg["inputTokens"]
            ex["outputTokens"] += agg["outputTokens"]
            ex["totalTokens"]  += agg["totalTokens"]
            ex["cost"]         += agg["cost"]
            ex["turns"]        += agg["turns"]
            ex["heavy"]         = ex["totalTokens"] > 280_000

            # Merge new daily breakdown into existing
            existing_daily = ex.setdefault("dailyTokens", {})
            for date_str, d in agg["dailyTokens"].items():
                if date_str in existing_daily:
                    existing_daily[date_str]["inputTokens"]  += d["inputTokens"]
                    existing_daily[date_str]["outputTokens"] += d["outputTokens"]
                    existing_daily[date_str]["cost"]         += d["cost"]
                else:
                    existing_daily[date_str] = dict(d)

            # Extend end time if these turns are later
            if last_ts.isoformat() > ex["end"]:
                ex["end"] = last_ts.isoformat()
                start_ts  = parse_iso(ex["start"])
                if start_ts:
                    ex["durationMin"] = max(1, round((last_ts - start_ts).total_seconds() / 60))

            updated += 1

    return added, updated

# ── Range bounds ──────────────────────────────────────────────────────────────

def get_range_bounds(range_key, now):
    """Return (start, end, prev_start, prev_end) for the chosen range."""
    if range_key == 'week':
        start, end   = week_bounds(now)
        prev_start, prev_end = week_bounds(start - timedelta(days=1))

    elif range_key == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end   = start.replace(month=start.month % 12 + 1,
                              year=start.year + (1 if start.month == 12 else 0))
        prev_end   = start
        prev_month = start.month - 1 or 12
        prev_start = start.replace(month=prev_month,
                                   year=start.year - (1 if start.month == 1 else 0))

    elif range_key == 'last30':
        end        = now
        start      = now - timedelta(days=30)
        prev_end   = start
        prev_start = start - timedelta(days=30)

    elif range_key == 'quarter':
        q           = (now.month - 1) // 3
        q_start_mon = q * 3 + 1
        start = now.replace(month=q_start_mon, day=1,
                            hour=0, minute=0, second=0, microsecond=0)
        end_mon = q_start_mon + 3
        end = start.replace(month=end_mon % 12 or 12,
                            year=start.year + (1 if end_mon > 12 else 0))
        prev_end   = start
        pq_mon     = q_start_mon - 3
        prev_start = start.replace(month=pq_mon % 12 or 12,
                                   year=start.year - (1 if pq_mon <= 0 else 0))

    elif range_key == 'year':
        start      = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end        = start.replace(year=start.year + 1)
        prev_start = start.replace(year=start.year - 1)
        prev_end   = start

    else:  # alltime
        start      = datetime.fromtimestamp(0, tz=timezone.utc)
        end        = now + timedelta(days=1)
        prev_start = prev_end = start

    return start, end, prev_start, prev_end

# ── Backfill ──────────────────────────────────────────────────────────────────

def backfill_daily_tokens(archive):
    """One-time: rebuild dailyTokens for any session that's missing it by re-scanning JSONL."""
    needs = {sid for sid, s in archive.items() if "dailyTokens" not in s}
    if not needs:
        return
    print(f"Backfilling dailyTokens for {len(needs)} older session(s) — one-time scan…")
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    all_buckets = scan_new_turns(epoch)
    for sid in needs:
        turns = sorted(all_buckets.get(sid, []), key=lambda e: e["_ts"])
        daily = defaultdict(lambda: {"inputTokens": 0, "outputTokens": 0, "cost": 0.0})
        for t in turns:
            local_date = t["_ts"].astimezone().date().isoformat()
            msg   = t.get("message", {})
            usage = msg.get("usage", {})
            mk    = model_key(msg.get("model", ""))
            in_tok  = usage.get("input_tokens",  0) or 0
            out_tok = usage.get("output_tokens", 0) or 0
            daily[local_date]["inputTokens"]  += in_tok
            daily[local_date]["outputTokens"] += out_tok
            daily[local_date]["cost"]         += turn_cost(usage, mk)
        archive[sid]["dailyTokens"] = dict(daily)

# ── Output builders ───────────────────────────────────────────────────────────

def _bucket_entry(bucket_start, bucket_end, label, date_label, sessions):
    """Sum tokens for a time bucket using per-day turn data (dailyTokens)."""
    # Collect all local date strings that fall within this bucket
    bucket_dates = set()
    d = bucket_start
    while d < bucket_end:
        bucket_dates.add(d.astimezone().date().isoformat())
        d += timedelta(days=1)

    in_tok = out_tok = cost = session_count = 0
    for s in sessions:
        daily = s.get("dailyTokens")
        if daily:
            hit = False
            for date_str, dd in daily.items():
                if date_str in bucket_dates:
                    in_tok  += dd["inputTokens"]
                    out_tok += dd["outputTokens"]
                    cost    += dd["cost"]
                    hit      = True
            if hit:
                session_count += 1
        else:
            # Fallback for sessions backfilled with empty dailyTokens
            if bucket_start <= parse_iso(s["start"]) < bucket_end:
                in_tok        += s["inputTokens"]
                out_tok       += s["outputTokens"]
                cost          += s["cost"]
                session_count += 1

    return {
        "date":         bucket_start.isoformat(),
        "label":        label,
        "dateLabel":    date_label,
        "inputTokens":  in_tok,
        "outputTokens": out_tok,
        "totalTokens":  in_tok + out_tok,
        "cost":         cost,
        "sessions":     session_count,
    }

def build_daily(sessions, range_key, start, end, now=None):
    """Build trend-chart buckets sized appropriately for the range."""
    if now is None:
        now = datetime.now(timezone.utc)
    # Never generate buckets for days strictly after today
    today_ceil = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    result = []

    if range_key == 'week':
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        # Use LOCAL date for today so timezone± users see the correct current day
        local_today  = now.astimezone().date()
        local_monday = local_today - timedelta(days=local_today.weekday())
        for i in range(7):
            local_day = local_monday + timedelta(days=i)
            # Bucket boundaries at local midnight so sessions near midnight land on the right day
            d_start = local_midnight(local_day)
            d_end   = d_start + timedelta(days=1)
            entry = _bucket_entry(d_start, d_end, day_labels[i], fmt_month_day(local_day), sessions)
            if local_day > local_today:
                entry["future"] = True
            result.append(entry)

    elif range_key in ('month', 'last30'):
        # One bar per calendar day — stop at today (never show future days)
        d   = start.replace(hour=0, minute=0, second=0, microsecond=0)
        cap = min(end, today_ceil)
        while d < cap:
            d_end = min(d + timedelta(days=1), cap)
            lbl = fmt_month_day(d)
            result.append(_bucket_entry(d, d_end, lbl, lbl, sessions))
            d += timedelta(days=1)

    elif range_key == 'quarter':
        # One bar per week — stop at today
        d, week_num = start, 1
        cap = min(end, today_ceil)
        while d < cap:
            w_end = min(d + timedelta(days=7), cap)
            result.append(_bucket_entry(d, w_end,
                                        f"W{week_num}", fmt_month_day(d),
                                        sessions))
            d = w_end
            week_num += 1

    else:  # year / alltime — one bar per calendar month
        if range_key == 'alltime' and sessions:
            # Use earliest actual session date so we don't generate buckets from 1970
            earliest_ts = min(parse_iso(s["start"]) for s in sessions if parse_iso(s["start"]))
            d = earliest_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            d = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while d < end:
            next_month = d.month % 12 + 1
            next_year  = d.year + (1 if d.month == 12 else 0)
            m_end = min(d.replace(month=next_month, year=next_year), end)
            result.append(_bucket_entry(d, m_end,
                                        d.strftime("%b"), d.strftime("%b %Y"),
                                        sessions))
            d = m_end

    return result

def build_heatmap(sessions):
    """7×24 token-intensity grid by day-of-week × hour, normalised 0–1."""
    grid = [[0.0] * 24 for _ in range(7)]
    for s in sessions:
        ts = parse_iso(s["start"])
        if ts is None:
            continue
        di, h = ts.weekday(), ts.hour
        if 0 <= di < 7 and 0 <= h < 24:
            grid[di][h] += s["totalTokens"]
    flat_max = max((v for row in grid for v in row), default=1) or 1
    return [[round(grid[di][h] / flat_max, 4) for h in range(24)] for di in range(7)]

# ── Main ──────────────────────────────────────────────────────────────────────

RANGE_LABELS = {
    'week':    'This week',
    'month':   'This month',
    'last30':  'Last 30 days',
    'quarter': 'This quarter',
    'year':    'This year',
    'alltime': 'All time',
}

def main():
    parser = argparse.ArgumentParser(description="Generate Claude token usage data.")
    parser.add_argument(
        '--range',
        dest='range_key',
        choices=RANGE_LABELS.keys(),
        default='week',
        help='Time range to include in live_usage.js (default: week)',
    )
    args = parser.parse_args()
    range_key = args.range_key

    print(f"Running on: {OS} (Python {sys.version.split()[0]})")

    now = datetime.now(timezone.utc)

    # ── Step 1: read state ────────────────────────────────────────────────────
    last_sync = read_last_sync()
    archive   = read_archive()
    first_run = last_sync.timestamp() == 0

    print(f"Scanning : {JSONL_DIR}")
    print(f"Last sync: {'never — full scan' if first_run else last_sync.strftime('%b %d %Y %H:%M UTC')}")
    print(f"Archive  : {len(archive)} sessions on disk")

    # ── Step 1b: backfill dailyTokens for any old sessions that lack it ───────
    backfill_daily_tokens(archive)

    # ── Step 2: scan for new turns ────────────────────────────────────────────
    new_buckets  = scan_new_turns(last_sync)
    new_turn_cnt = sum(len(v) for v in new_buckets.values())
    print(f"New turns: {new_turn_cnt} across {len(new_buckets)} sessions")

    # ── Step 3: merge into archive ────────────────────────────────────────────
    added, updated = merge_into_archive(archive, new_buckets)
    print(f"Merged   : +{added} new  ~{updated} updated  →  {len(archive)} total")

    # ── Step 4: persist ───────────────────────────────────────────────────────
    write_archive(archive)
    write_last_sync(now)

    # ── Step 5: filter archive for chosen range ───────────────────────────────
    start, end, prev_start, prev_end = get_range_bounds(range_key, now)
    all_sessions = list(archive.values())

    sessions = sorted(
        [s for s in all_sessions if start <= parse_iso(s["start"]) < end],
        key=lambda s: s["start"], reverse=True,
    )
    prev_sessions = [s for s in all_sessions
                     if prev_start <= parse_iso(s["start"]) < prev_end]

    prev_total = sum(s["totalTokens"] for s in prev_sessions)
    prev_cost  = sum(s["cost"]        for s in prev_sessions)

    # ── Step 6: write live_usage.js ───────────────────────────────────────────
    price_for_js = {k: {"in": v["in"], "out": v["out"]} for k, v in PRICE.items()}

    # Build days array: local-midnight ISO strings Mon → today (never beyond today)
    # Uses local date so UTC± users see the correct current day
    local_today  = now.astimezone().date()
    local_monday = local_today - timedelta(days=local_today.weekday())
    days = []
    d = local_monday
    while d <= local_today:          # <= includes today, never exceeds it
        days.append(local_midnight(d).isoformat())
        d += timedelta(days=1)
    week_mon = local_monday          # used in summary print below

    daily_data = build_daily(sessions, range_key, start, end, now=now)

    payload = {
        "SURFACES":      build_surfaces(all_sessions),
        "MODELS":        MODELS,
        "PRICE":         price_for_js,
        "rangeKey":      range_key,
        "sessions":      sessions,       # range-filtered, used for initial render
        "allSessions":   all_sessions,   # full archive — JS uses this for UI range switching
        "daily":         daily_data,
        "heatmap":       build_heatmap(sessions),
        "days":          days,
        "weekStart":     start.isoformat(),
        "prevWeekTotal": prev_total,
        "prevWeekCost":  prev_cost,
        # Plan context — API-equivalent cost vs flat Pro subscription
        "apiValue":      round(sum(s["cost"] for s in all_sessions), 6),
        "planCost":      20,
        "planType":      "pro_monthly",
    }

    OUTPUT_FILE.write_text(
        "window.USAGE = " + json.dumps(payload, indent=2) + ";",
        encoding="utf-8"
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    total_tok  = sum(s["totalTokens"] for s in sessions)
    total_cost = sum(s["cost"]        for s in sessions)
    delta_pct  = ((total_tok - prev_total) / prev_total * 100) if prev_total else 0

    # Earliest / latest across entire archive (not just this range)
    all_starts = [parse_iso(s["start"]) for s in all_sessions if parse_iso(s["start"])]
    if all_starts:
        earliest = min(all_starts).strftime("%Y-%m-%d")
        latest   = max(all_starts).strftime("%Y-%m-%d")
    else:
        earliest = latest = "n/a"

    days_included = sum(1 for d in daily_data if not d.get("future"))

    print()
    print("─" * 46)
    day_start_str = week_mon.strftime(f"%a %b {_DAY_FMT}")
    day_end_str   = local_today.strftime(f"%a %b {_DAY_FMT}")
    print(f"  Range     {RANGE_LABELS[range_key]}")
    print(f"  Days in range: {day_start_str} → {day_end_str} ({days_included} days)")
    print(f"  Sessions  {len(sessions)}")
    print(f"  Tokens    {total_tok:>18,}")
    print(f"  Cost      {'${:.4f}'.format(total_cost):>18}")
    print(f"  vs prev   {delta_pct:>+17.1f}%")
    print("─" * 46)
    print(f"  Earliest session: {earliest}")
    print(f"  Latest session:   {latest}")
    print("─" * 46)
    print(f"  Archive → {ARCHIVE}")
    print(f"  Output  → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
