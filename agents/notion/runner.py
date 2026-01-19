from __future__ import annotations
import sys

from .protocol import parse_update_board
from .store import NotionDashboardStore, load_config


def run(text: str) -> int:
    commands = parse_update_board(text)
    if not commands:
        print("No UPDATE_BOARD commands found.")
        return 0

    store = NotionDashboardStore(load_config())

    for cmd in commands:
        print(f"🤖 TRIGGER: {cmd.category} | {cmd.metric}")
        try:
            action = store.upsert(cmd.category, cmd.metric, cmd.value)
            print(f"✨ {action}: {cmd.metric} → {cmd.value}")
        except Exception as e:
            print(f"❌ ERROR {cmd.metric}: {e}")

    return 0


def main() -> int:
    if not sys.stdin.isatty():
        return run(sys.stdin.read())

    if len(sys.argv) < 2:
        print("Usage:")
        print("  cat ai_output.txt | python3 -m agents.notion.runner")
        print("  python3 -m agents.notion.runner ai_output.txt")
        return 2

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        return run(f.read())


if __name__ == "__main__":
    raise SystemExit(main())
