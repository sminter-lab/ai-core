from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

def now_date():
    """Return Notion-compatible date payload"""
    return {"start": datetime.now(timezone.utc).isoformat()}


@dataclass(frozen=True)
class NotionStoreConfig:
    token: str
    database_id: str
    cache_path: Path
    log_path: Path


def load_config() -> NotionStoreConfig:
    token = os.getenv("NOTION_API_KEY", "").strip()
    dbid = os.getenv("NOTION_DATABASE_ID", "").strip()

    cache_path = Path(os.getenv("NOTION_PAGE_CACHE", "./data/cache/notion_page_ids.json"))
    log_path = Path(os.getenv("NOTION_UPDATES_LOG", "./logs/dashboard_updates.ndjson"))

    if not token:
        raise RuntimeError("Missing NOTION_API_KEY")
    if not dbid:
        raise RuntimeError("Missing NOTION_DATABASE_ID")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    return NotionStoreConfig(token, dbid, cache_path, log_path)


class NotionDashboardStore:
    """
    Canonical Notion dashboard updater.
    Uses search-first upsert.
    """

    def __init__(self, cfg: NotionStoreConfig):
        self.client = Client(auth=cfg.token)
        self.database_id = cfg.database_id
        self.cache_path = cfg.cache_path
        self.log_path = cfg.log_path
        self.cache = self._load_cache()

    def _load_cache(self) -> dict[str, str]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        self.cache_path.write_text(json.dumps(self.cache, indent=2))

    def _cache_key(self, category: str, metric: str) -> str:
        return f"{category}|{metric}"

    def _log(self, payload: dict):
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def _search_page_id(self, category: str, metric: str) -> Optional[str]:
        res = self.client.search(
            query=metric,
            filter={"value": "page", "property": "object"},
            sort={"timestamp": "last_edited_time", "direction": "descending"},
        )

        for page in res.get("results", []):
            parent = page.get("parent", {})
            if parent.get("database_id") != self.database_id:
                continue

            props = page.get("properties", {})
            cat = "".join(t["plain_text"] for t in props["Category"]["title"])
            met = "".join(t["plain_text"] for t in props["Metric"]["rich_text"])

            if cat == category and met == metric:
                return page["id"]

        return None

    def _create_page(self, category: str, metric: str, value: str) -> str:
        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties={
                "Category": {"title": [{"text": {"content": category}}]},
                "Metric": {"rich_text": [{"text": {"content": metric}}]},
                "Value": {"rich_text": [{"text": {"content": value}}]},
                "Last AI Run": {"date": now_date()},
            },
        )
        return page["id"]

    def _update_page(self, page_id: str, value: str):
        self.client.pages.update(
            page_id=page_id,
            properties={
                "Value": {"rich_text": [{"text": {"content": value}}]},
                "Last AI Run": {"date": now_date()},
            },
        )

    def upsert(self, category: str, metric: str, value: str) -> str:
        key = self._cache_key(category, metric)
        page_id = self.cache.get(key)

        if not page_id:
            page_id = self._search_page_id(category, metric)
            if page_id:
                self.cache[key] = page_id
                self._save_cache()

        if page_id:
            self._update_page(page_id, value)
            action = "UPDATED"
        else:
            page_id = self._create_page(category, metric, value)
            self.cache[key] = page_id
            self._save_cache()
            action = "CREATED"

        self._log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "category": category,
            "metric": metric,
            "value": value,
            "page_id": page_id,
        })

        return action
