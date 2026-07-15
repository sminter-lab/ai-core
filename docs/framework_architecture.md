# Search → Collect → Analyze Framework

_Foundation architecture for Eidolon data pipelines_
_v1.0 — Real Estate is the first implementation_

---

## Role Separation (Hard Boundary)

### MintWorker — COLLECTOR

**Job: scrape, fetch, and store raw data. Nothing else.**

- Executes all external data collection (RapidAPI calls, scrapes)
- Runs on lowest safe cadence — **1 run per day, M–F only**
- Writes raw results to shared storage: `raw_root()/{project}/raw/{YYYY-MM-DD}.json`
  (NAS RawSourceLibrary via `tools/shared_store.py`; falls back to local `data/raw/`)
- NEVER analyzes, scores, or filters beyond basic spec matching
- NEVER sends alerts or digests
- Rate-limit aware: hard caps on API calls per run
- Kill switch: checks `control/{project}.enabled` flag before every run —
  if file missing or contains "0", exits immediately

### Mac Studio — ANALYST

**Job: read raw data, analyze, decide, report. Never collects.**

- Reads raw JSON from `raw_root()/{project}/raw/`
- Runs analysis (Ollama scoring, financial modeling)
- Applies decision criteria (fit / no-fit)
- Selects top N leads (real estate: **3 leads/day max**)
- Writes decisions to `raw_root()/{project}/decisions/{YYYY-MM-DD}.json`
- Generates morning digest content
- Owns all outlook/trend tracking over time

**Why this boundary matters:** if analysis logic changes, collection never breaks.
If a scraper breaks, analysis still works on prior data. Each side can be
debugged, replaced, or scaled independently. This is the template for every
future Eidolon data pipeline.

---

## Data Flow

```
[RapidAPI / Web Sources]
        │
        ▼
┌──────────────────────┐
│  MINTWORKER          │   cron: M–F 6:00am
│  collect_{project}.py│   • fetch via RapidAPI
│                      │   • basic spec filter (price, location, type)
│                      │   • write raw JSON
└─────────┬────────────┘
          │  raw_root()/{project}/raw/YYYY-MM-DD.json
          ▼
┌──────────────────────┐
│  MAC STUDIO          │   cron: M–F 7:00am
│  analyze_{project}.py│   • load raw data
│                      │   • Ollama scoring vs criteria
│                      │   • select top 3 leads
│                      │   • write decisions JSON
│                      │   • update digest
└─────────┬────────────┘
          │  raw_root()/{project}/decisions/YYYY-MM-DD.json
          ▼
   [Morning Digest: 3 leads with GO / NO-GO + reasons]
```

---

## Runaway Protection (Required in Every Collector)

1. **Enable flag**: `control/{project}.enabled` must contain "1" or job exits
2. **API budget**: max API calls per run defined in config (`max_api_calls: 50` to start)
3. **Weekday guard**: job exits immediately on Sat/Sun
4. **Single-run lock**: PID lockfile prevents overlapping runs
5. **Failure backoff**: if 3 consecutive runs fail, job writes DISABLED to enable flag
   and status WARN — requires manual re-enable
6. **Cost log**: every RapidAPI call logged with timestamp to `logs/{project}_api_usage.log`

---

## Real Estate Implementation (Project 1)

| Component | Value |
|---|---|
| Collector | `jobs/collect_realestate.py` on MintWorker |
| Analyzer | `jobs/analyze_realestate.py` on Mac Studio |
| Data source | RapidAPI (Zillow API) + Craigslist RSS fallback |
| Cadence | M–F, 1 collection run + 1 analysis run per day |
| Output | 3 best leads/day in morning digest |
| Criteria | Per `criteria/re_investment_criteria.md` (residential + commercial) |
| Starting API budget | 50 calls/run — raise only after 2 clean weeks |

### Lead Selection Logic (Analyzer)

1. Load all raw properties from today's collection
2. Financial pre-filter (cash flow, CoC, cap rate, DSCR per criteria doc)
3. Ollama 8B scores survivors 1–10
4. Rank by score, take top 3
5. Each lead gets: GO / NO-GO / REVIEW verdict + 3 bullet reasons + financials
6. Anything below score 7 is logged but never surfaces

### Cron entries

```cron
# MintWorker (crontab -e as sam)
0 6 * * 1-5  cd /path/to/ai-core && .venv/bin/python -m jobs.collect_realestate >> logs/collect_realestate.log 2>&1

# Mac Studio (crontab -e)
0 7 * * 1-5  cd /Users/samuelminter/ai-core && .venv/bin/python -m jobs.analyze_realestate >> logs/analyze_realestate.log 2>&1
```

---

## Template for Future Projects

To spin up a new data pipeline (any data type):

1. Copy `jobs/collect_TEMPLATE.py` → rename for project
2. Copy `jobs/analyze_TEMPLATE.py` → rename for project
3. Write project criteria doc (like `criteria/re_investment_criteria.md`)
4. Create `control/{project}.enabled` with "1"
5. Add two cron entries (collector on MintWorker, analyzer on Studio)
6. Start at lowest cadence, expand only after stability proven

---

## Git Structure

```
ai-core/
├── jobs/
│   ├── collect_realestate.py      # MintWorker
│   ├── analyze_realestate.py      # Mac Studio
│   ├── collect_TEMPLATE.py        # reusable pattern
│   ├── analyze_TEMPLATE.py        # reusable pattern
│   └── re_criteria.py             # financial math shared by analyzer
├── config/
│   └── realestate.json            # single config, both jobs read it
├── criteria/
│   └── re_investment_criteria.md
├── control/                       # enable flags (contents gitignored)
└── docs/
    └── framework_architecture.md  # this file
```

Note: config is JSON (not yaml) — zero extra dependencies on either machine.
