from __future__ import annotations
import json, os, re
from pathlib import Path
from datetime import datetime, timezone
import httpx, feedparser

def now_utc(): return datetime.now(timezone.utc)
def iso(ts): return ts.isoformat()

def safe_name(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_")
    return s[:120] if s else "feed"

def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")

def main():
    vault = Path(os.environ.get("AI_VAULT_ROOT", "/srv/ai-vault"))
    out_raw = vault / "raw" / "rss"
    out_proc = vault / "processed" / "rss"
    out_raw.mkdir(parents=True, exist_ok=True)
    out_proc.mkdir(parents=True, exist_ok=True)

    spec_path = os.environ.get("JOB_SPEC_PATH")
    if not spec_path:
        raise SystemExit("Missing JOB_SPEC_PATH")

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    feeds = spec.get("feeds", [])
    job_id = spec.get("job_id", f"rss_{now_utc().strftime('%Y-%m-%d')}")
    max_items = int(spec.get("max_items", 25))
    timeout_s = float(spec.get("timeout_s", 20.0))

    client = httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers={"User-Agent": "ai-core-rss-collector/1.0"},
    )

    collected = {"job_id": job_id, "generated_at": iso(now_utc()), "feeds": []}

    for f in feeds:
        url = f["url"]
        name = safe_name(f.get("name") or url)

        feed_raw_path = out_raw / job_id / f"{name}.xml"
        feed_proc_path = out_proc / job_id / f"{name}.json"

        r = client.get(url)
        r.raise_for_status()
        write(feed_raw_path, r.text)

        parsed = feedparser.parse(r.text)
        items = []
        for e in parsed.entries[:max_items]:
            items.append({
                "title": getattr(e, "title", None),
                "link": getattr(e, "link", None),
                "published": getattr(e, "published", None),
                "summary": getattr(e, "summary", None),
                "source": name,
            })

        proc = {"source": name, "url": url, "fetched_at": iso(now_utc()), "items": items}
        write(feed_proc_path, json.dumps(proc, indent=2, ensure_ascii=False))

        collected["feeds"].append({
            "source": name,
            "url": url,
            "raw_path": str(feed_raw_path),
            "processed_path": str(feed_proc_path),
            "count": len(items),
        })

    index_path = out_proc / job_id / "_index.json"
    write(index_path, json.dumps(collected, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "job_id": job_id, "index": str(index_path)}, indent=2))

if __name__ == "__main__":
    main()
