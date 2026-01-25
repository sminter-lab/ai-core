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
        # Initialize client once
        self.client = Client(auth=cfg.notion_api_key)

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
                # If successful, return immediately
                return
            except Exception as e:
                # Log error but try next DB if available
                print(f"Error accessing DB {db_id}: {e}")
                last_error = e

        raise RuntimeError(f"All Notion DBs failed. Last error: {last_error}")

    # =========================
    # Internals
    # =========================

    def _find_page(self, database_id: str, category: str, metric: str) -> Optional[str]:
        # NOTE: This assumes "Metric" is the database Title/Name property.
        # If "Category" is a 'Select' column in Notion, change 'rich_text' to 'select' below.
        
        response = self.client.databases.query(
            database_id=database_id,
            page_size=1,
            filter={
                "and": [
                    {
                        "property": "Category", 
                        "rich_text": {"equals": category}
                    },
                    {
                        # CRITICAL FIX: The primary column must be queried as 'title', not 'rich_text'
                        "property": "Metric", 
                        "title": {"equals": metric}
                    },
                ]
            }
        )

        results = response.get("results", [])
        if not results:
            return None
        return results[0]["id"]

    def _update_page(self, page_id: str, value: str) -> None:
        # NOTE: Notion API limits text blocks to 2000 chars. Truncating to be safe.
        safe_value = value[:2000] 
        
        self.client.pages.update(
            page_id=page_id,
            properties={
                "Value": {
                    "rich_text": [{"text": {"content": safe_value}}]
                }
            },
        )

    def _create_page(self, database_id: str, category: str, metric: str, value: str) -> None:
        safe_value = value[:2000]
        
        self.client.pages.create(
            parent={"database_id": database_id},
            properties={
                "Category": {"rich_text": [{"text": {"content": category}}]},
                "Metric": {
                    # CRITICAL FIX: Creating the Title property
                    "title": [{"text": {"content": metric}}]
                },
                "Value": {"rich_text": [{"text": {"content": safe_value}}]},
            },
        )
