from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Dict, List, Tuple

import httpx


# =========================
# Config
# =========================

@dataclass
class StoreConfig:
    notion_api_key: str
    database_ids: list[str]

    # Notion property names (columns)
    col_category: str = "Category"
    col_metric: str = "Metric"
    col_value: str = "Value"


def _split_ids(*vals: str) -> list[str]:
    out: list[str] = []
    for v in vals:
        if not v:
            continue
        for part in v.replace(";", ",").split(","):
            part = part.strip()
            if part:
                out.append(part)

    # de-dupe, preserve order
    seen = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq


def load_config() -> StoreConfig:
    key = os.getenv("NOTION_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing NOTION_API_KEY")

    db_single = os.getenv("NOTION_DATABASE_ID", "").strip()
    db_multi = os.getenv("NOTION_DATABASE_IDS", "").strip()
    db_ids = _split_ids(db_single, db_multi)
    if not db_ids:
        raise RuntimeError("Missing NOTION_DATABASE_ID (or NOTION_DATABASE_IDS)")

    col_category = (os.getenv("NOTION_COL_CATEGORY", "Category") or "Category").strip()
    col_metric = (os.getenv("NOTION_COL_METRIC", "Metric") or "Metric").strip()
    col_value = (os.getenv("NOTION_COL_VALUE", "Value") or "Value").strip()

    return StoreConfig(
        notion_api_key=key,
        database_ids=db_ids,
        col_category=col_category,
        col_metric=col_metric,
        col_value=col_value,
    )


# =========================
# Store (REST, version-proof)
# =========================

class NotionDashboardStore:
    """
    REST implementation that supports BOTH:
      - classic Notion databases (/databases/{id}/query)
      - new multi-source models via /data_sources/{id}/query
    """

    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {cfg.notion_api_key}",
            "Notion-Version": (os.getenv("NOTION_VERSION", "2022-06-28") or "2022-06-28").strip(),
            "Content-Type": "application/json",
        }
        # id -> "data_source" | "database"
        self._id_type_cache: Dict[str, str] = {}

    # ---- compatibility layer (runner may call older names) ----
    def upsert_value(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        return self.upsert_metric(category, metric, value)

    def update_dashboard(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        return self.upsert_metric(category, metric, value)

    # ---- public API ----
    def upsert_metric(self, category: str, metric: str, value: str) -> None:
        last_error: Optional[Exception] = None

        for target_id in self.cfg.database_ids:
            try:
                page_id = self._find_page(target_id, category, metric)
                if page_id:
                    self._update_page(page_id, value)
                else:
                    self._create_page(target_id, category, metric, value)
                return
            except Exception as e:
                print(f"Error accessing target {target_id}: {e}")
                last_error = e

        raise RuntimeError(f"All Notion targets failed. Last error: {last_error}")

    # =========================
    # Internals
    # =========================

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(headers=self.headers, timeout=30.0) as client:
            resp = client.request(method, url, json=payload)
            resp.raise_for_status()
            if resp.text:
                return resp.json()
        return {}

    def _detect_id_type(self, id_: str) -> str:
        """
        Determines whether id_ is a data_source_id or database_id.
        Caches the result.
        """
        if id_ in self._id_type_cache:
            return self._id_type_cache[id_]

        # Prefer GET /data_sources/{id} (no side effects, fast)
        try:
            self._request("GET", f"/data_sources/{id_}")
            self._id_type_cache[id_] = "data_source"
            return "data_source"
        except Exception:
            pass

        # Fallback: classic database
        try:
            self._request("GET", f"/databases/{id_}")
            self._id_type_cache[id_] = "database"
            return "database"
        except Exception:
            pass

        raise RuntimeError(f"ID is neither a data_source nor a database: {id_}")

    def _query_endpoint(self, target_id: str) -> str:
        t = self._detect_id_type(target_id)
        if t == "data_source":
            return f"/data_sources/{target_id}/query"
        return f"/databases/{target_id}/query"

    def _parent_payload(self, target_id: str) -> dict[str, str]:
        """
        For page creation, parent must match the target type.
        """
        t = self._detect_id_type(target_id)
        if t == "data_source":
            return {"data_source_id": target_id}
        return {"database_id": target_id}

    # ---- query helpers ----

    def _rich_text_equals(self, prop: str, value: str) -> dict[str, Any]:
        return {"property": prop, "rich_text": {"equals": value}}

    def _title_equals(self, prop: str, value: str) -> dict[str, Any]:
        return {"property": prop, "title": {"equals": value}}

    def _select_equals(self, prop: str, value: str) -> dict[str, Any]:
        return {"property": prop, "select": {"equals": value}}

    def _find_page(self, target_id: str, category: str, metric: str) -> Optional[str]:
        """
        Find an existing page for (category, metric).

        We avoid schema calls entirely. Instead we try both layouts:
          A) Category is title, Metric is rich_text/select
          B) Metric is title, Category is rich_text/select

        For non-title columns, we try rich_text first; if that errors, try select.
        """
        query_path = self._query_endpoint(target_id)

        attempts: List[Tuple[str, str]] = [
            (self.cfg.col_category, self.cfg.col_metric),
            (self.cfg.col_metric, self.cfg.col_category),
        ]

        # de-dupe attempts
        seen = set()
        uniq: List[Tuple[str, str]] = []
        for a in attempts:
            if a not in seen:
                uniq.append(a)
                seen.add(a)

        for title_prop, other_prop in uniq:
            title_value = category if title_prop == self.cfg.col_category else metric
            other_value = metric if title_prop == self.cfg.col_category else category

            # First try: other as rich_text
            payload = {
                "page_size": 1,
                "filter": {
                    "and": [
                        self._title_equals(title_prop, title_value),
                        self._rich_text_equals(other_prop, other_value),
                    ]
                },
            }
            try:
                resp = self._request("POST", query_path, payload)
                results = resp.get("results", []) or []
                if results:
                    return results[0]["id"]
            except Exception:
                pass

            # Second try: other as select
            payload = {
                "page_size": 1,
                "filter": {
                    "and": [
                        self._title_equals(title_prop, title_value),
                        self._select_equals(other_prop, other_value),
                    ]
                },
            }
            try:
                resp = self._request("POST", query_path, payload)
                results = resp.get("results", []) or []
                if results:
                    return results[0]["id"]
            except Exception:
                pass

        return None

    # ---- property builders ----

    def _prop_title(self, prop: str, text: str) -> dict[str, Any]:
        return {prop: {"title": [{"text": {"content": text}}]}}

    def _prop_rich_text(self, prop: str, text: str) -> dict[str, Any]:
        return {prop: {"rich_text": [{"text": {"content": text}}]}}

    def _prop_select(self, prop: str, name: str) -> dict[str, Any]:
        return {prop: {"select": {"name": name}}}

    # ---- write operations ----

    def _update_page(self, page_id: str, value: str) -> None:
        safe_value = (value or "")[:2000]
        props: dict[str, Any] = {}
        # Value in your DB is rich_text (from your successful query output)
        props.update(self._prop_rich_text(self.cfg.col_value, safe_value))
        self._request("PATCH", f"/pages/{page_id}", {"properties": props})

    def _create_page(self, target_id: str, category: str, metric: str, value: str) -> None:
        """
        Create a new row. We don't know which of Category/Metric is the title prop,
        so we attempt both layouts.
        """
        safe_value = (value or "")[:2000]
        parent = self._parent_payload(target_id)

        # Layout A: Category is title, Metric is rich_text/select
        props_a: dict[str, Any] = {}
        props_a.update(self._prop_title(self.cfg.col_category, category))

        # Metric could be rich_text OR select — try rich_text first at call-site
        props_a.update(self._prop_rich_text(self.cfg.col_metric, metric))
        props_a.update(self._prop_rich_text(self.cfg.col_value, safe_value))

        payload_a = {"parent": parent, "properties": props_a}

        try:
            self._request("POST", "/pages", payload_a)
            return
        except Exception:
            # Try Metric as select if rich_text failed
            props_a2: dict[str, Any] = {}
            props_a2.update(self._prop_title(self.cfg.col_category, category))
            props_a2.update(self._prop_select(self.cfg.col_metric, metric))
            props_a2.update(self._prop_rich_text(self.cfg.col_value, safe_value))
            payload_a2 = {"parent": parent, "properties": props_a2}
            try:
                self._request("POST", "/pages", payload_a2)
                return
            except Exception:
                pass

        # Layout B: Metric is title, Category is rich_text/select
        props_b: dict[str, Any] = {}
        props_b.update(self._prop_title(self.cfg.col_metric, metric))
        props_b.update(self._prop_rich_text(self.cfg.col_category, category))
        props_b.update(self._prop_rich_text(self.cfg.col_value, safe_value))
        payload_b = {"parent": parent, "properties": props_b}

        try:
            self._request("POST", "/pages", payload_b)
            return
        except Exception:
            # Try Category as select if rich_text failed
            props_b2: dict[str, Any] = {}
            props_b2.update(self._prop_title(self.cfg.col_metric, metric))
            props_b2.update(self._prop_select(self.cfg.col_category, category))
            props_b2.update(self._prop_rich_text(self.cfg.col_value, safe_value))
            payload_b2 = {"parent": parent, "properties": props_b2}
            self._request("POST", "/pages", payload_b2)
            return
