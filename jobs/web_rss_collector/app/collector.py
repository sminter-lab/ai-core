from __future__ import annotations

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone

import httpx
import feedparser


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.isoformat()


def safe_name(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "")).strip("_")
    return s[:120] if s else "feed"


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def main() -> None:
    vault_root = Path(os.environ.get("AI_VAULT_ROOT", "/srv/ai-vault"))
    job_spec_path = os.environ.get("JOB_SPEC_PATH")

    if not job_spec_path:
        raise SystemExit("Missing JOB_SPEC_PATH")

    spec_file = Path(job_spec_path)
    if spec_file.is_dir():
        raise SystemExit(f"JOB_SPEC_PATH is a directory, expected file: {spec_file}")

    spec = json.loads(spec_file.read_text(encoding="utf-8"))

    job_id = spec.get("job_id") or f"rss_{now_utc().strftime('%Y-%m-%d')}"
    feeds = spec.get("feeds", [])
    max_items = int(spec.get("max_items", 25))
    timeout_s = float(spec.get("timeout_s", 20.0))

    out_raw = vault_root / "raw" / "rss" / job_id
    out_proc = vault_root / "processed" / "rss" / job_id
    out_raw.mkdir(parents=True, exist_ok=True)
    out_proc.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers={"User-Agent": "ai-core-web-rss-collector/1.0"},
    )

    collected_index = {
        "job_id": job_id,
        "generated_at": iso(now_utc()),
        "feeds": [],
    }

    for f in feeds:
        if isinstance(f, str):
            # Optional: accept string URLs too
            url = f
            name = safe_name(url)
        else:
            url = f["url"]
            name = safe_name(f.get("name") or url)

        raw_path = out_raw / f"{name}.xml"
        proc_path = out_proc / f"{name}.json"

        r = client.get(url)
        r.raise_for_status()
        raw_text = r.text
        write_text(raw_path, raw_text)

        parsed = feedparser.parse(raw_text)

        items = []
        for e in parsed.entries[:max_items]:
            items.append(
                {
                    "title": getattr(e, "title", None),
                    "link": getattr(e, "link", None),
                    "published": getattr(e, "published", None),
                    "summary": getattr(e, "summary", None),
                    "source": name,
                }
            )

        proc_obj = {
            "source": name,
            "url": url,
            "fetched_at": iso(now_utc()),
            "items": items,
        }
        write_text(proc_path, json.dumps(proc_obj, indent=2, ensure_ascii=False))

        collected_index["feeds"].append(
            {
                "source": name,
                "url": url,
                "raw_path": str(raw_path),
                "processed_path": str(proc_path),
                "count": len(items),
            }
        )

    index_path = out_proc / "_index.json"
    write_text(index_path, json.dumps(collected_index, indent=2, ensure_ascii=False))

    print(json.dumps({"ok": True, "job_id": job_id, "index": str(index_path)}, indent=2))


if __name__ == "__main__":
    main()
