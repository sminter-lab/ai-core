from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from notion_client import Client
from notion_client.errors import APIResponseError

# ----------------------------
# Helpers
# ----------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def notion_date_payload() -> dict[str, str]:
    return {"start": now_iso()}

def _safe_mkdir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)

def _split_csv_env(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]

def _is_missing_db_error(e: Exception) -> bool:
    """
    True when the integration can't see a database (not shared / wrong workspace / wrong id).
    """
    if isinstance(e, APIResponseError):
        msg = str(e).lower()
        return (
            "could not find database" in msg
            or "object not found" in msg
            or "404" in msg
            or "not found" in msg
        )
    return False

# ----------------------------
# Config
# ----------------------------

@dataclass(frozen=True)
class NotionStoreConfig:
    token: str
    database_ids: list[str]          # ordered fallback list
    cache_path: Path
    log_path: Path

    @property
    def primary_database_id(self) -> str:
        return self.database_ids[0]


def load_config() -> NotionStoreConfig:
    token = os.getenv("NOTION_API_KEY", "").strip()

    raw_ids = os.getenv("NOTION_DATABASE_IDS", "").strip()
    if raw_ids:
        database_ids = _split_csv_env(raw_ids)
    else:
        # Backward compatible
        single = os.getenv("NOTION_DATABASE_ID", "").strip()
        database_ids = [single] if single else []

    cache_path = Path(os.getenv("NOTION_PAGE_CACHE", "./data/cache/notion_page_ids.json"))
    log_path = Path(os.getenv("NOTION_UPDATES_LOG", "./logs/dashboard_updates.ndjson"))

    if not token:
        raise RuntimeError("Missing NOTION_API_KEY")
    if not database_ids or not database_ids[0]:
        raise RuntimeError("Missing NOTION_DATABASE_ID (or set NOTION_DATABASE_IDS)")

    _safe_mkdir(cache_path)
    _safe_mkdir(log_path)

    return NotionStoreConfig(token=token, database_ids=database_ids, cache_path=cache_path, log_path=log_path)

# ----------------------------
# Store
# ----------------------------

class NotionDashboardStore:
    """
    Canonical Notion dashboard updater.
    Search-first upsert:
      - Find row by Category + Metric
      - Update if exists
      - Create if missing
    Includes:
      - local cache (category|metric -> page_id)
      - ndjson log of updates
      - multi-database fallback (NOTION_DATABASE_IDS)
    """

    # Notion property names (must match your DB schema)
    PROP_CATEGORY = "Category"        # title
    PROP_METRIC = "Metric"           # rich_text
    PROP_VALUE = "Value"             # rich_text
    PROP_AI_TEXT = "AI Analysis"     # rich_text
    PROP_AI_DATE = "Last AI Run"     # date

    def __init__(self, cfg: NotionStoreConfig):
        self.cfg = cfg
        self.client = Client(auth=cfg.token)
        self.database_ids = cfg.database_ids
        self.cache_path = cfg.cache_path
        self.log_path = cfg.log_path
        self.cache = self._load_cache()

    # -------- cache / log --------

    def _cache_key(self, category: str, metric: str) -> str:
        return f"{category.strip()}|{metric.strip()}"

    def _load_cache(self) -> dict[str, str]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")

    def _append_log(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # -------- notion utils --------

    def _rich_text(self, content: str) -> dict[str, Any]:
        return {"rich_text": [{"type": "text", "text": {"content": content}}]}

    def _title(self, content: str) -> dict[str, Any]:
        return {"title": [{"type": "text", "text": {"content": content}}]}

    def _date(self) -> dict[str, Any]:
        return {"date": notion_date_payload()}

    def _find_page_id(self, category: str, metric: str) -> Optional[str]:
        """
        Try cache first, then query each database until found.
        """
        key = self._cache_key(category, metric)
        if key in self.cache:
            return self.cache[key]

        last_err: Optional[Exception] = None

        for dbid in self.database_ids:
            try:
                resp = self.client.databases.query(
                    database_id=dbid,
                    filter={
                        "and": [
                            {"property": self.PROP_CATEGORY, "title": {"equals": category}},
                            {"property": self.PROP_METRIC, "rich_text": {"equals": metric}},
                        ]
                    },
                    page_size=1,
                )
                results = resp.get("results", [])
                if not results:
                    continue

                page_id = results[0]["id"]
                self.cache[key] = page_id
                self._save_cache()
                return page_id

            except Exception as e:
                last_err = e
                # If the integration can't see this DB, try next DB
                if _is_missing_db_error(e):
                    continue
                raise

        # If every DB failed only because it was missing/not shared, treat as not found.
        # If something else happened, we would have raised already.
        _ = last_err
        return None

    def _update_page(self, page_id: str, value: str, ai_text: Optional[str]) -> None:
        props: dict[str, Any] = {
            self.PROP_VALUE: self._rich_text(value),
            self.PROP_AI_DATE: self._date(),
        }
        if ai_text is not None:
            props[self.PROP_AI_TEXT] = self._rich_text(ai_text)

        self.client.pages.update(page_id=page_id, properties=props)

    def _create_page(self, dbid: str, category: str, metric: str, value: str, ai_text: Optional[str]) -> str:
        props: dict[str, Any] = {
            self.PROP_CATEGORY: self._title(category),
            self.PROP_METRIC: self._rich_text(metric),
            self.PROP_VALUE: self._rich_text(value),
            self.PROP_AI_DATE: self._date(),
        }
        if ai_text is not None:
            props[self.PROP_AI_TEXT] = self._rich_text(ai_text)

        page = self.client.pages.create(parent={"database_id": dbid}, properties=props)
        return page["id"]

    # -------- public API --------

    def upsert_value(self, category: str, metric: str, value: str, ai_text: Optional[str] = None) -> None:
        """
        Upsert the Value + AI fields for a Category/Metric row.
        """
        category = category.strip()
        metric = metric.strip()

        page_id = self._find_page_id(category, metric)

        if page_id:
            self._update_page(page_id, value=value, ai_text=ai_text)
            self._append_log(
                {
                    "ts": now_iso(),
                    "action": "update",
                    "category": category,
                    "metric": metric,
                    "page_id": page_id,
                }
            )
            return

        # Create: try databases in order until create succeeds
        last_err: Optional[Exception] = None
        for dbid in self.database_ids:
            try:
                new_page_id = self._create_page(dbid, category, metric, value=value, ai_text=ai_text)
                key = self._cache_key(category, metric)
                self.cache[key] = new_page_id
                self._save_cache()

                self._append_log(
                    {
                        "ts": now_iso(),
                        "action": "create",
                        "category": category,
                        "metric": metric,
                        "page_id": new_page_id,
                        "database_id": dbid,
                    }
                )
                return
            except Exception as e:
                last_err = e
                if _is_missing_db_error(e):
                    continue
                raise

        raise RuntimeError(f"All Notion databases failed on create. Last error: {last_err}")
