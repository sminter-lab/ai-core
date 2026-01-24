diff --git a/agents/notion/store.py b/agents/notion/store.py
index 0000000..1111111 100644
--- a/agents/notion/store.py
+++ b/agents/notion/store.py
@@ -1,18 +1,34 @@
 from __future__ import annotations
+from typing import Iterable, Optional, Tuple
 from dotenv import load_dotenv
 load_dotenv()
 import json
 import os
 from dataclasses import dataclass
 from datetime import datetime, timezone
 from pathlib import Path
 
 from notion_client import Client
+from notion_client.errors import APIResponseError
 
 
 @dataclass
 class NotionConfig:
     notion_api_key: str
-    notion_database_id: str
+    # Primary DB (backward compatible)
+    notion_database_id: str
+    # Optional list of DBs to try in order
+    notion_database_ids: list[str]
 
 
 def load_config() -> NotionConfig:
     api_key = os.getenv("NOTION_API_KEY", "").strip()
     if not api_key:
         raise RuntimeError("Missing NOTION_API_KEY")
 
-    dbid = os.getenv("NOTION_DATABASE_ID", "").strip()
-    if not dbid:
-        raise RuntimeError("Missing NOTION_DATABASE_ID")
+    # New: support multiple DBs (comma-separated), while staying compatible with NOTION_DATABASE_ID
+    raw_ids = os.getenv("NOTION_DATABASE_IDS", "").strip()
+    if raw_ids:
+        dbids = [x.strip() for x in raw_ids.split(",") if x.strip()]
+        if not dbids:
+            raise RuntimeError("NOTION_DATABASE_IDS is set but empty after parsing")
+        dbid = dbids[0]
+    else:
+        dbid = os.getenv("NOTION_DATABASE_ID", "").strip()
+        if not dbid:
+            raise RuntimeError("Missing NOTION_DATABASE_ID (or set NOTION_DATABASE_IDS)")
+        dbids = [dbid]
 
-    return NotionConfig(notion_api_key=api_key, notion_database_id=dbid)
+    return NotionConfig(notion_api_key=api_key, notion_database_id=dbid, notion_database_ids=dbids)
 
 
 class NotionDashboardStore:
     def __init__(self, config: NotionConfig):
         self.config = config
         self.client = Client(auth=config.notion_api_key)
+        self.dbids = config.notion_database_ids or [config.notion_database_id]
+
+    def _iter_dbids(self) -> Iterable[str]:
+        # Always try configured list first
+        for d in self.dbids:
+            if d:
+                yield d
+
+    def _is_db_missing_error(self, e: Exception) -> bool:
+        # Notion "Could not find database with ID" / 404s etc.
+        if isinstance(e, APIResponseError):
+            msg = str(e).lower()
+            return ("could not find database" in msg) or ("object not found" in msg) or ("404" in msg)
+        return False
 
     def _find_row_page_id(self, category: str, metric: str) -> str | None:
-        resp = self.client.databases.query(
-            database_id=self.config.notion_database_id,
-            filter={
-                "and": [
-                    {"property": "Category", "title": {"equals": category}},
-                    {"property": "Metric", "rich_text": {"equals": metric}},
-                ]
-            },
-        )
-        results = resp.get("results", [])
-        if not results:
-            return None
-        return results[0]["id"]
+        last_err: Exception | None = None
+        for dbid in self._iter_dbids():
+            try:
+                resp = self.client.databases.query(
+                    database_id=dbid,
+                    filter={
+                        "and": [
+                            {"property": "Category", "title": {"equals": category}},
+                            {"property": "Metric", "rich_text": {"equals": metric}},
+                        ]
+                    },
+                )
+                results = resp.get("results", [])
+                if not results:
+                    # Nothing found in this DB; try the next one
+                    continue
+                return results[0]["id"]
+            except Exception as e:
+                last_err = e
+                # If DB missing/not shared, fall through to next DB
+                if self._is_db_missing_error(e):
+                    continue
+                # If it's some other error, bubble it up
+                raise
+        # If we tried everything and got only missing DB errors, return None (so caller can create)
+        # If there was some error, raise the last one only if it wasn't a missing-db kind.
+        return None
 
     def upsert_value(self, category: str, metric: str, value: str, ai_text: str | None = None) -> None:
         page_id = self._find_row_page_id(category, metric)
         now = datetime.now(timezone.utc).isoformat()
 
         if page_id:
             props = {
                 "Value": {"rich_text": [{"type": "text", "text": {"content": value}}]},
                 "Last AI Run": {"date": {"start": now}},
             }
             if ai_text is not None:
                 props["AI Analysis"] = {"rich_text": [{"type": "text", "text": {"content": ai_text}}]}
 
             self.client.pages.update(page_id=page_id, properties=props)
             return
 
-        # create if not found
-        props = {
-            "Category": {"title": [{"type": "text", "text": {"content": category}}]},
-            "Metric": {"rich_text": [{"type": "text", "text": {"content": metric}}]},
-            "Value": {"rich_text": [{"type": "text", "text": {"content": value}}]},
-            "Last AI Run": {"date": {"start": now}},
-        }
-        if ai_text is not None:
-            props["AI Analysis"] = {"rich_text": [{"type": "text", "text": {"content": ai_text}}]}
-
-        self.client.pages.create(
-            parent={"database_id": self.config.notion_database_id},
-            properties=props,
-        )
+        # create if not found: try each DB until create succeeds
+        props = {
+            "Category": {"title": [{"type": "text", "text": {"content": category}}]},
+            "Metric": {"rich_text": [{"type": "text", "text": {"content": metric}}]},
+            "Value": {"rich_text": [{"type": "text", "text": {"content": value}}]},
+            "Last AI Run": {"date": {"start": now}},
+        }
+        if ai_text is not None:
+            props["AI Analysis"] = {"rich_text": [{"type": "text", "text": {"content": ai_text}}]}
+
+        last_err: Exception | None = None
+        for dbid in self._iter_dbids():
+            try:
+                self.client.pages.create(
+                    parent={"database_id": dbid},
+                    properties=props,
+                )
+                return
+            except Exception as e:
+                last_err = e
+                if self._is_db_missing_error(e):
+                    continue
+                raise
+
+        raise RuntimeError(f"All Notion databases failed on create. Last error: {last_err}")
