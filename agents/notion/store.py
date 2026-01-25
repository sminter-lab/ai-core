from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Iterable

from notion_client import Client


# =========================
# Config + helpers
# =========================

def _env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing {name}")
    return v


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =========================
# Data model
# =========================

@dataclass
class StoreConfig:
    notion_api_key: str
    database_ids: list[str]

    col_category: str = "Category"
    col_metric: str = "Metric"
    col_value: str = "Value"
    col_ai_text: str = "AI Analysis"
    col_ai_date: str = "Last AI Run"


def load_config() -> StoreConfig:
    """
    Supports:
      NOTION_DATABASE_ID=...
      OR
      NOTION_DATABASE_IDS=id1,id2,id3
    """
    api_key = _env("NOTION_API_KEY")

    raw_ids = os.getenv("NOTION_DATABASE_IDS", "").strip()
    if raw_ids:
        dbids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    else:
        dbids = [_env("NOTION_DATABASE_ID")]

    return StoreConfig(
        notion_api_key=api_key,
        database_ids=dbids,
    )


# =========================
# Store
# =========================

class NotionDashboardStore:
    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.client = Client(auth=cfg.notion_api_key)

    # ---------- low-level request (version tolerant) ----------
    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        notion_client Client.request signature varies across versions.
        Try known calling conventions.
        """
        req = getattr(self.client, "request")

        # Newer versions
        for kwargs in (
            {"json": payload},
            {"body": payload},
            {"data": payload},
            {"payload": payload},
        ):
            try:
                return req(method, path, **kwargs)  # type: ignore[arg-type]
            except TypeError:
                pass

        # Oldest / fallback
        return req(method, path, payload)

    # ---------- compatibility layer (runner may call older method names) ----------
    def upsert_value(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        """Back-compat alias for older runners."""
        return self.upsert_metric(category, metric, value)

    def update_dashboard(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        """Another back-compat alias."""
        return self.upsert_metric(category, metric, value)

    # ---------- database query (version tolerant) ----------
    def _db_query(
        self,
        database_id: str,
        *,
        filter_obj: Optional[dict[str, Any]] = None,
        page_size: int = 1,
    ) -> dict[str, Any]:

        payload: dict[str, Any] = {"page_size": page_size}
        if filter_obj:
            payload["filter"] = filter_obj

        # Preferred path (modern client)
        try:
            if hasattr(self.client, "databases") and hasattr(self.client.databases, "query"):
                return self.client.databases.query(  # type: ignore[attr-defined]
                    database_id=database_id,
                    **payload,
                )
        except Exception:
            pass

        # Raw REST fallback
        return self._request("POST", f"databases/{database_id}/query", payload)
    # ---------- public API ----------

    def upsert_metric(self, category: str, metric: str, value: str) -> None:
        """
        Find (Category, Metric) row and update, or create if missing.
        Tries all configured DBs until one succeeds.
        """
        last_error: Optional[Exception] = None

        for dbid in self.cfg.database_ids:
            try:
                self._upsert_metric_db(dbid, category, metric, value)
                return
            except Exception as e:
                last_error = e

        raise RuntimeError(f"All Notion DBs failed. Last error: {last_error}")

    # ---------- per-DB logic ----------

    def _upsert_metric_db(
        self,
        dbid: str,
        category: str,
        metric: str,
        value: str,
    ) -> None:

        # Query existing row
        resp = self._db_query(
            dbid,
            filter_obj={
                "and": [
                    {
                        "property": self.cfg.col_category,
                        "rich_text": {"equals": category},
                    },
                    {
                        "property": self.cfg.col_metric,
                        "rich_text": {"equals": metric},
                    },
                ]
            },
            page_size=1,
        )

        results = resp.get("results", [])

        if results:
            page_id = results[0]["id"]
            self._update_page(page_id, value)
        else:
            self._create_page(dbid, category, metric, value)

    # ---------- page ops ----------

    def _update_page(self, page_id: str, value: str) -> None:
        self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.col_value: {
                    "rich_text": [{"type": "text", "text": {"content": value}}]
                },
                self.cfg.col_ai_text: {
                    "rich_text": [{"type": "text", "text": {"content": value}}]
                },
                self.cfg.col_ai_date: {
                    "date": {"start": utc_now_iso()}
                },
            },
        )

    def _create_page(
        self,
        dbid: str,
        category: str,
        metric: str,
        value: str,
    ) -> None:

        self.client.pages.create(
            parent={"database_id": dbid},
            properties={
                self.cfg.col_category: {
                    "rich_text": [{"type": "text", "text": {"content": category}}]
                },
                self.cfg.col_metric: {
                    "rich_text": [{"type": "text", "text": {"content": metric}}]
                },
                self.cfg.col_value: {
                    "rich_text": [{"type": "text", "text": {"content": value}}]
                },
                self.cfg.col_ai_text: {
                    "rich_text": [{"type": "text", "text": {"content": value}}]
                },
                self.cfg.col_ai_date: {
                    "date": {"start": utc_now_iso()}
                },
            },
        )
