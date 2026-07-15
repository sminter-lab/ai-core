# ai-core
# ai-core

**ai-core** is the foundational backend repository for local and distributed AI systems.
It serves as the central “brain” for agents, workers, services, and infrastructure running
across macOS and Linux hosts.

This repository is intentionally backend-only:
- No UI assumptions
- No single application constraints
- Designed for automation, orchestration, and long-running intelligence

---

## 🔭 Scope (re-scoped 2026-07)

**MintWorker collects. Mac Studio analyzes. Claude handles outlook.**

Role separation is a hard boundary (see `docs/framework_architecture.md`):

- **MintWorker — COLLECTOR:** all external data fetching (RapidAPI, scrapes,
  Schwab collectors, RSS/market capture). Lowest safe cadence, M–F only,
  API budgets, enable-flag kill switches, failure auto-disable. Writes raw
  JSON to shared NAS storage. Never analyzes, never alerts.
- **Mac Studio — ANALYST:** reads raw data, runs Ollama scoring + financial
  modeling, applies decision criteria, selects top leads, writes decisions
  and morning digests. Never collects.
- **Offsite (Claude):** daily outlook, market analysis, trade review — reads
  `data/health.json`, `data/schwab/schwab.sqlite3`, and NAS captures; writes
  `data/outlook/`
- First framework implementation: **real estate deal pipeline**
  (`jobs/collect_realestate.py` → `jobs/analyze_realestate.py`, 3 leads/day M–F)
- Retired code lives in `archive/analysis/`

---

## 🧠 Core Philosophy

ai-core follows three principles:

1. **Separation of Thinking and Doing**
   - *Agents* reason, decide, and plan (offsite)
   - *Workers* capture, verify, and sort data (local)

2. **Local-First, Cloud-Optional**
   - Designed to run on local machines (macOS, Linux servers)
   - Can be extended to cloud infrastructure when needed

3. **Infrastructure Is Code**
   - Repeatable setups
   - No manual configuration drift
   - Automation over ad-hoc fixes

---

## 🚫 What This Repository Is Not

- Not a frontend or UI application
- Not a single-purpose AI demo
- Not model weights or training data
- Not tied to any specific LLM provider

ai-core is system infrastructure, not an end product.

---

## 🗂 Repository Structure

```text
ai-core/
├── jobs/          # collect_* (MintWorker) / analyze_* (Studio) + templates
├── config/        # Per-project pipeline config (JSON)
├── criteria/      # Human-readable decision criteria docs
├── control/       # Enable flags & locks (contents gitignored)
├── docs/          # Architecture, decisions, setup notes
├── infra/         # Docker, systemd, Terraform, Ansible
├── agents/        # LLM-driven decision-makers
├── workers/       # Headless executors & daemons
├── services/      # APIs, webhooks, long-running services
├── models/        # Prompts & model configuration (NO weights)
├── scripts/       # Utilities, migrations, maintenance
├── archive/       # Retired code (local analysis/LLM, kept for reference)
├── data/          # Local-only runtime data (gitignored; outlook/ + health.json)
├── tests/         # Unit & integration tests
├── logs/          # Runtime logs (gitignored)
└── README.md
