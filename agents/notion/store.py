from __future__ import annotations

import os
import requests
from dataclasses import dataclass
from typing import Any, Optional

from notion_client import Client


# =========================
# Config
# =========================

@dataclass
class StoreConfig:
    notion_api_key: str
    database_ids: list[str]


def load_config() -> StoreConfig:
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise RuntimeError("NOTION_API_KEY is not set")

    raw = os.environ.get("NOTION_DATABASE_IDS", "")
    dbs = [x.strip() for x in raw.split(",") if x.strip()]
    if not dbs:
        raise RuntimeError("NOTION_DATABASE_IDS is empty")

    return StoreConfig(key, dbs)


# =========================
# Store
# =========================

class NotionDashboardStore:
    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.client = Client(auth=cfg.notion_api_key)
        self.headers = {
            "Authorization": f"Bearer {cfg.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    # ---- compatibility layer ----
    def upsert_value(self, category: str, metric: str, value: str, *_, **__) -> None:
        self.upsert_metric(category, metric, value)

    def update_dashboard(self, category: str, metric: str, value: str, *_, **__) -> None:
        self.upsert_metric(category, metric, value)

    # ---- public API ----
    def upsert_metric(self, category: str, metric: str, value: str) -> None:
        last_error: Optional[Exception] = None

        for db_id in self.cfg.database_ids:
            try:
                page_id = self._find_page(db_id, category, metric)
                if page_id:
                    self._update_page(page_id, value)
                else:
                    self._create_page(db_id, category, metric, value)
                return
            except Exception as e:
                last_error = e

        raise RuntimeError(f"All Notion DBs failed. Last error: {last_error}")

    # =========================
    # Internals
    # =========================

    def _find_page(self, database_id: str, category: str, metric: str) -> Optional[str]:
        url = f"https://api.notion.com/v1/databases/{database_id}/query"

        payload = {
            "page_size": 1,
            "filter": {
                "and": [
                    {"property": "Category", "rich_text": {"equals": category}},
                    {"property": "Metric", "rich_text": {"equals": metric}},
                ]
            },
        }

        r = requests.post(url, headers=self.headers, json=payload, timeout=20)
        r.raise_for_status()

        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        return results[0]["id"]

    def _update_page(self, page_id: str, value: str) -> None:
        self.client.pages.update(
            page_id=page_id,
            properties={
                "Value": {
                    "rich_text": [{"text": {"content": value}}]
                }
            },
        )

    def _create_page(self, database_id: str, category: str, metric: str, value: str) -> None:
        self.client.pages.create(
            parent={"database_id": database_id},
            properties={
                "Category": {"rich_text": [{"text": {"content": category}}]},
                "Metric": {"rich_text": [{"text": {"content": metric}}]},
                "Value": {"rich_text": [{"text": {"content": value}}]},
            },
        )
