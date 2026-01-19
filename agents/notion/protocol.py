from __future__ import annotations
from dataclasses import dataclass

ALLOWED_CATEGORIES = {"Health", "Trading", "Finance", "System", "Content", "Family"}

@dataclass(frozen=True)
class UpdateBoardCommand:
    category: str
    metric: str
    value: str

def parse_update_board(text: str) -> list[UpdateBoardCommand]:
    commands: list[UpdateBoardCommand] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("UPDATE_BOARD|"):
            continue

        parts = line.split("|", 3)
        if len(parts) != 4:
            continue

        _, category, metric, value = parts
        category, metric, value = category.strip(), metric.strip(), value.strip()

        if category not in ALLOWED_CATEGORIES:
            continue
        if not metric or not value:
            continue

        commands.append(UpdateBoardCommand(category, metric, value))

    return commands
