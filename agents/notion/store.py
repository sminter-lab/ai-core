from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

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


def _split_db_ids(*vals: str) -> list[str]:
    out: list[str] = []
    for v in vals:
        if not v:
            continue
        for part in v.replace(";", ",").split(","):
            part = part.strip()
            if part:
                out.append(part)
    # de-dupe while preserving order
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
    db_ids = _split_db_ids(db_single, db_multi)
    if not db_ids:
        raise RuntimeError("Missing NOTION_DATABASE_ID (or NOTION_DATABASE_IDS)")

    # allow overrides if you ever rename columns
    col_category = os.getenv("NOTION_COL_CATEGORY", "Category").strip() or "Category"
    col_metric = os.getenv("NOTION_COL_METRIC", "Metric").strip() or "Metric"
    col_value = os.getenv("NOTION_COL_VALUE", "Value").strip() or "Value"

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
    Direct Notion REST implementation (no notion_client dependency).
    """

    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {cfg.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        self._schema_cache: dict[str, dict[str, Any]] = {}

    # ---- compatibility layer (runner may call older names) ----
    def upsert_value(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        return self.upsert_metric(category, metric, value)

    def update_dashboard(self, category: str, metric: str, value: str, *args, **kwargs) -> None:
        return self.upsert_metric(category, metric, value)

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
                print(f"Error accessing DB {db_id}: {e}")
                last_error = e

        raise RuntimeError(f"All Notion DBs failed. Last error: {last_error}")

    # =========================
    # Internals
    # =========================

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(headers=self.headers, timeout=30.0) as client:
            resp = client.request(method, url, json=payload)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {}

    def _get_db_schema(self, database_id: str) -> dict[str, Any]:
        if database_id in self._schema_cache:
            return self._schema_cache[database_id]

        data = self._request("GET", f"/databases/{database_id}")
        props = data.get("properties", {}) or {}

        # Find which property is the Title (Notion always has exactly one "title" type)
        title_prop_name = None
        for name, spec in props.items():
            if (spec or {}).get("type") == "title":
                title_prop_name = name
                break

        schema = {
            "properties": props,
            "title_prop": title_prop_name,
        }
        self._schema_cache[database_id] = schema
        return schema

    def _prop_type(self, schema: dict[str, Any], prop_name: str) -> Optional[str]:
        props = schema.get("properties", {}) or {}
        spec = props.get(prop_name)
        if not spec:
            return None
        return spec.get("type")

    def _make_equals_filter(self, schema: dict[str, Any], prop_name: str, value: str) -> dict[str, Any]:
        """
        Builds a Notion database filter for a property, based on its type.
        Supports title / rich_text / select. Falls back to rich_text.
        """
        ptype = self._prop_type(schema, prop_name) or "rich_text"
        if ptype == "title":
            return {"property": prop_name, "title": {"equals": value}}
        if ptype == "select":
            return {"property": prop_name, "select": {"equals": value}}
        # default
        return {"property": prop_name, "rich_text": {"equals": value}}

    def _find_page(self, database_id: str, category: str, metric: str) -> Optional[str]:
        schema = self._get_db_schema(database_id)
        title_prop = schema.get("title_prop")

        # We try two layouts:
        #  A) Category is title, Metric is text
        #  B) Metric is title, Category is text
        # Because your DB(s) have changed and Notion title column may be renamed.
        attempts: list[tuple[str, str]] = []

        # preferred: if Category is actually the title prop, use that
        if title_prop == self.cfg.col_category:
            attempts.append((self.cfg.col_category, self.cfg.col_metric))
        elif title_prop == self.cfg.col_metric:
            attempts.append((self.cfg.col_metric, self.cfg.col_category))

        # also try both ways explicitly
        attempts.append((self.cfg.col_category, self.cfg.col_metric))
        attempts.append((self.cfg.col_metric, self.cfg.col_category))

        # de-dupe attempts
        seen = set()
        uniq_attempts: list[tuple[str, str]] = []
        for a in attempts:
            if a not in seen:
                uniq_attempts.append(a)
                seen.add(a)

        for title_candidate, other_candidate in uniq_attempts:
            # Build filters using detected property types
            if title_candidate == self.cfg.col_category:
                f1 = self._make_equals_filter(schema, self.cfg.col_category, category)
                f2 = self._make_equals_filter(schema, self.cfg.col_metric, metric)
            else:
                f1 = self._make_equals_filter(schema, self.cfg.col_metric, metric)
                f2 = self._make_equals_filter(schema, self.cfg.col_category, category)

            payload = {
                "page_size": 1,
                "filter": {"and": [f1, f2]},
            }

            resp = self._request("POST", f"/databases/{database_id}/query", payload)
            results = resp.get("results", []) or []
            if results:
                return results[0]["id"]

        return None

    def _set_prop_value(self, schema: dict[str, Any], prop_name: str, text: str) -> dict[str, Any]:
        """
        Builds a Notion 'properties' entry for create/update based on property type.
        """
        ptype = self._prop_type(schema, prop_name) or "rich_text"
        if ptype == "title":
            return {prop_name: {"title": [{"text": {"content": text}}]}}
        if ptype == "select":
            return {prop_name: {"select": {"name": text}}}
        return {prop_name: {"rich_text": [{"text": {"content": text}}]}}

    def _update_page(self, page_id: str, value: str) -> None:
        safe_value = (value or "")[:2000]
        # we don’t need schema for update except the Value prop type; fetch via first DB schema
        # (Value prop types should be consistent; if not, this still works for rich_text/title/select)
        schema = self._get_db_schema(self.cfg.database_ids[0])

        props = {}
        props.update(self._set_prop_value(schema, self.cfg.col_value, safe_value))

        self._request("PATCH", f"/pages/{page_id}", {"properties": props})

    def _create_page(self, database_id: str, category: str, metric: str, value: str) -> None:
        schema = self._get_db_schema(database_id)
        safe_value = (value or "")[:2000]

        props: dict[str, Any] = {}

        # Category + Metric can be title/rich_text/select; we set both according to schema
        props.update(self._set_prop_value(schema, self.cfg.col_category, category))
        props.update(self._set_prop_value(schema, self.cfg.col_metric, metric))
        props.update(self._set_prop_value(schema, self.cfg.col_value, safe_value))

        payload = {
            "parent": {"database_id": database_id},
            "properties": props,
        }
        self._request("POST", "/pages", payload)
