from __future__ import annotations
import json
from pathlib import Path
from tools.memory.store import write_daily, write_decision, write_fact, write_run

INBOX = Path("data/bot_inbox")
PROCESSED = INBOX / "processed"

def main():
    PROCESSED.mkdir(parents=True, exist_ok=True)

    for p in sorted(INBOX.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            kind = payload.get("kind")
            data = payload.get("data", {})

            if kind == "daily":
                write_daily(data["summary"], data.get("sources", []), name=data.get("name", "daily"))
            elif kind == "decision":
                write_decision(data["title"], data["rationale"], data.get("consequences", []), name=data.get("name", "decision"))
            elif kind == "fact":
                write_fact(data["key"], data["value"], float(data.get("confidence", 1.0)))
            elif kind == "run":
                write_run(data["run_id"], data["status"], data.get("notes"))
            else:
                raise ValueError(f"Unknown kind: {kind}")

            p.rename(PROCESSED / p.name)
            print(f"processed: {p.name}")

        except Exception as e:
            print(f"ERROR {p.name}: {e}")

if __name__ == "__main__":
    main()
