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

## 🧠 Core Philosophy

ai-core follows three principles:

1. **Separation of Thinking and Doing**
   - *Agents* reason, decide, and plan
   - *Workers* execute deterministic tasks

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
├── docs/          # Architecture, decisions, setup notes
├── infra/         # Docker, systemd, Terraform, Ansible
├── agents/        # LLM-driven decision-makers
├── workers/       # Headless executors & daemons
├── services/      # APIs, webhooks, long-running services
├── models/        # Prompts & model configuration (NO weights)
├── scripts/       # Utilities, migrations, maintenance
├── data/          # Local-only runtime data (gitignored)
├── tests/         # Unit & integration tests
├── logs/          # Runtime logs (gitignored)
└── README.md
