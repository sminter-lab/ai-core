from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Iterable
import os

from notion_client import Client
from notion_client.errors import APIResponseError


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

    raw_ids = os.environ.get("NOTION_DATABASE_IDS", "")
    db_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    if not db_ids:
        raise RuntimeError("NOTION_DATABASE_IDS is empty")

    return StoreConfig(
        notion_api_key=key,
        database_ids=db_ids,
    )


# =========================
# Store
# =========================

class NotionDashboardStore:
    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.client = Client(auth=cfg.notion_api_key)

    # -------------------------------------------------
    # Back-compat aliases (runner safety)
    # -------------------------------------------------

    def upsert_value(self, category: str, metric: str, value: str, *a, **k) -> None:
        return self.upsert_metric(category, metric, value)

    def update_dashboard(self, category: str, metric: str, value: str, *a, **k) -> None:
        return self.upsert_metric(category, metric, value)

    # -------------------------------------------------
    # Core public API
    # -------------------------------------------------

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

        raise RuntimeError(
            f"All Notion DBs failed. Last error: {last_error}"
        )

    # -------------------------------------------------
    # Internals
    # -------------------------------------------------

    def _find_page(
        self,
        database_id: str,
        category: str,
        metric: str,
    ) -> Optional[str]:

        payload = {
            "page_size": 1,
            "filter": {
                "and": [
                    {"property": "Category", "rich_text": {"equals": category}},
                    {"property": "Metric", "rich_text": {"equals": metric}},
                ]
            },
        }

        # Preferred modern API
        if hasattr(self.client, "databases") and hasattr(self.client.databases, "query"):
            res = self.client.databases.query(
                database_id=database_id,
                **payload,
            )
        else:
            # Explicit REST call — THIS is what fixes your error
            res = self.client.request(
                "POST",
                f"/v1/databases/{database_id}/query",
                payload,
            )

        results = res.get("results", [])
        if not results:
            return None

        return results[0]["id"]

    def _update_page(self, page_id: str, value: str) -> None:
        self.client.pages.update(
            page_id=page_id,
            properties={
                "Value": {
                    "rich_text": [
                        {"text": {"content": value}}
                    ]
                }
            },
        )

    def _create_page(
        self,
        database_id: str,
        category: str,
        metric: str,
        value: str,
    ) -> None:
        self.client.pages.create(
            parent={"database_id": database_id},
            properties={
                "Category": {
                    "rich_text": [{"text": {"content": category}}]
                },
                "Metric": {
                    "rich_text": [{"text": {"content": metric}}]
                },
                "Value": {
                    "rich_text": [{"text": {"content": value}}]
                },
            },
        )
