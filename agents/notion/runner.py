from __future__ import annotations

import sys

from .protocol import parse_update_board
from .store import NotionDashboardStore, load_config


def run(text: str) -> int:
    # Normalize input (systemd + tools sometimes inject CRLF or weird whitespace)
    if text is None:
        text = ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    commands = parse_update_board(text)

    if not commands:
        # Debug: show a tiny hint of what we received without dumping secrets
        preview = text[:200].replace("\n", "\\n")
        print("No UPDATE_BOARD commands found.")
        if preview:
            print(f"(debug) input preview: {preview}")
        else:
            print("(debug) input was empty after normalization")
        return 0

    store = NotionDashboardStore(load_config())

    for cmd in commands:
        print(f"🤖 TRIGGER: {cmd.category} | {cmd.metric}")
        try:
            # store method name in your repo is upsert_value() in the rewritten store.py,
            # but you previously had store.upsert(). We'll support both safely.
            if hasattr(store, "upsert"):
                action = store.upsert(cmd.category, cmd.metric, cmd.value)  # type: ignore[attr-defined]
            else:
                store.upsert_value(cmd.category, cmd.metric, cmd.value, ai_text=None)
                action = "UPDATED"

            # Keep output short; values can be long
            shown = cmd.value if len(cmd.value) <= 120 else (cmd.value[:117] + "...")
            print(f"✨ {action}: {cmd.metric} → {shown}")
        except Exception as e:
            print(f"❌ ERROR {cmd.metric}: {e}")

    return 0


def main() -> int:
    # If data is piped in, read stdin
    if not sys.stdin.isatty():
        return run(sys.stdin.read())

    # Otherwise expect a file path arg
    if len(sys.argv) < 2:
        print("Usage:")
        print("  cat ai_output.txt | python3 -m agents.notion.runner")
        print("  python3 -m agents.notion.runner ai_output.txt")
        return 2

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        return run(f.read())


if __name__ == "__main__":
    raise SystemExit(main())
