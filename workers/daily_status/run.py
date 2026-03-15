#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.shared_store import raw_root, docs_root


def _tz() -> ZoneInfo:
    # Prefer explicit TZ; fallback to system/local.
    tzname = os.environ.get("TZ") or os.environ.get("AI_TZ") or "America/New_York"
    try:
        return ZoneInfo(tzname)
    except Exception:
        return ZoneInfo("UTC")


def _today_str() -> str:
    now = datetime.now(_tz())
    return now.date().isoformat()


def _parse_date(s: str) -> date:
    # Accept YYYY-MM-DD or "today"
    if s.strip().lower() == "today":
        return datetime.now(_tz()).date()
    return date.fromisoformat(s)


@dataclass
class DailyStatus:
    day: str
    generated_at: str
    tz: str
    highlights: list[str]
    counts: dict[str, int]
    evidence: dict[str, list[str]]  # label -> list of paths/notes


def _safe_read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _files_modified_on(root: Path, day: date) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            m = datetime.fromtimestamp(p.stat().st_mtime, _tz()).date()
        except Exception:
            continue
        if m == day:
            out.append(p)
    return sorted(out)


def _collect_rss(ai_vault_root: Path, day: date) -> tuple[int, list[str]]:
    # Expect indexes at /srv/ai-vault/processed/rss/<job_id>/_index.json
    rss_root = ai_vault_root / "processed" / "rss"
    if not rss_root.exists():
        return 0, []
    evidence: list[str] = []
    total_items = 0

    for idx in rss_root.rglob("_index.json"):
        # Only count if the index file itself was modified today
        try:
            idx_day = datetime.fromtimestamp(idx.stat().st_mtime, _tz()).date()
        except Exception:
            continue
        if idx_day != day:
            continue

        data = _safe_read_json(idx)
        # We support either {"items":[...]} or {"count":N} or "total"
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                total_items += len(data["items"])
            elif isinstance(data.get("count"), int):
                total_items += int(data["count"])
            elif isinstance(data.get("total"), int):
                total_items += int(data["total"])
        evidence.append(str(idx))

    return total_items, evidence


def _collect_bot_inbox(ai_vault_root: Path, day: date) -> tuple[int, int, list[str]]:
    inbox_root = ai_vault_root / "inbox" / "bot"
    processed = inbox_root / "processed"
    if not inbox_root.exists():
        return 0, 0, []

    inbox_today = _files_modified_on(inbox_root, day)
    processed_today = _files_modified_on(processed, day)

    # Only count JSON payloads
    inbox_json = [p for p in inbox_today if p.suffix.lower() == ".json"]
    processed_json = [p for p in processed_today if p.suffix.lower() == ".json"]

    evidence = []
    if inbox_json:
        evidence.append(f"{len(inbox_json)} inbox json(s) modified today under {inbox_root}")
    if processed_json:
        evidence.append(f"{len(processed_json)} processed json(s) modified today under {processed}")

    return len(inbox_json), len(processed_json), evidence


def _collect_ai_core_state(day: date) -> tuple[int, list[str]]:
    # Optional: /var/lib/ai-core/state/** touched today
    state_root = Path("/var/lib/ai-core/state")
    if not state_root.exists():
        return 0, []
    touched = _files_modified_on(state_root, day)
    # keep evidence small
    evidence = [str(p) for p in touched[:15]]
    return len(touched), evidence


def _collect_ai_vault_memory(ai_vault_root: Path, day: date) -> tuple[int, list[str]]:
    mem_root = ai_vault_root / "memory"
    if not mem_root.exists():
        return 0, []
    touched = _files_modified_on(mem_root, day)
    evidence = [str(p) for p in touched[:15]]
    return len(touched), evidence


def build_daily_status(ai_vault_root: Path, day: date) -> DailyStatus:
    tz = _tz()
    now = datetime.now(tz)

    rss_items, rss_ev = _collect_rss(ai_vault_root, day)
    inbox_in, inbox_done, inbox_ev = _collect_bot_inbox(ai_vault_root, day)
    state_count, state_ev = _collect_ai_core_state(day)
    mem_touched, mem_ev = _collect_ai_vault_memory(ai_vault_root, day)

    highlights: list[str] = []
    if rss_items > 0:
        highlights.append(f"RSS collected: ~{rss_items} item(s) indexed today.")
    if inbox_done > 0:
        highlights.append(f"Bot inbox processed: {inbox_done} payload(s) moved to processed.")
    if state_count > 0:
        highlights.append(f"ai-core state updated: {state_count} file(s) touched under /var/lib/ai-core/state.")
    if not highlights:
        highlights.append("No confirmed worker artifacts found for today yet (or paths not wired).")

    counts = {
        "rss_items_indexed": int(rss_items),
        "bot_inbox_json_modified": int(inbox_in),
        "bot_inbox_processed_json_modified": int(inbox_done),
        "ai_core_state_files_touched": int(state_count),
        "ai_vault_memory_files_touched": int(mem_touched),
    }

    evidence: dict[str, list[str]] = {
        "rss_indexes": rss_ev,
        "bot_inbox": inbox_ev,
        "ai_core_state_samples": state_ev,
        "ai_vault_memory_samples": mem_ev,
    }

    return DailyStatus(
        day=day.isoformat(),
        generated_at=now.isoformat(),
        tz=str(tz.key),
        highlights=highlights,
        counts=counts,
        evidence=evidence,
    )


def render_markdown(ds: DailyStatus) -> str:
    lines = []
    lines.append(f"# Daily Status — {ds.day}")
    lines.append("")
    lines.append(f"- Generated at: `{ds.generated_at}` ({ds.tz})")
    lines.append("")
    lines.append("## Highlights")
    for h in ds.highlights:
        lines.append(f"- {h}")
    lines.append("")
    lines.append("## Counts")
    for k, v in ds.counts.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Evidence (paths / notes)")
    for label, items in ds.evidence.items():
        if not items:
            continue
        lines.append(f"### {label}")
        for it in items[:20]:
            lines.append(f"- `{it}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(ai_vault_root: Path, ds: DailyStatus) -> tuple[Path, Path]:
    # studio writes processed daily summaries to the NAS Documents share
    out_dir = docs_root() / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / f"{ds.day}.md"
    json_path = out_dir / f"{ds.day}.json"

    md_path.write_text(render_markdown(ds), encoding="utf-8")
    json_path.write_text(json.dumps(ds.__dict__, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a daily status digest from ai-vault/state artifacts.")
    ap.add_argument("--date", default="today", help="YYYY-MM-DD or 'today'")
    # mintworker writes raw data to NAS_RAW_ROOT; fall back to AI_VAULT_ROOT for
    # backwards compatibility, then the shared_store default.
    ap.add_argument(
        "--vault-root",
        default=os.environ.get("AI_VAULT_ROOT", str(raw_root())),
    )
    ap.add_argument("--format", default="text", choices=["text", "json"], help="Output format to stdout")
    ap.add_argument("--no-write", action="store_true", help="Do not write to ai-vault, only print")
    args = ap.parse_args()

    day = _parse_date(args.date)
    vault_root = Path(args.vault_root)

    ds = build_daily_status(vault_root, day)

    md_path = json_path = None
    if not args.no_write:
        md_path, json_path = write_outputs(vault_root, ds)

    if args.format == "json":
        print(json.dumps(ds.__dict__, indent=2, ensure_ascii=False))
    else:
        # Print a compact, chat-friendly response
        print(f"Daily Status ({ds.day})")
        for h in ds.highlights:
            print(f"- {h}")
        if md_path and json_path:
            print(f"- Saved: {md_path}")
            print(f"- Saved: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
