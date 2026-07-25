import requests
from datetime import datetime, timezone

NOTION_API = "https://api.notion.com/v1"
# Data-source IDs (both parent databases are multi-source, so the pre-2025
# API versions cannot address them — 2025-09-03 with data_source_id is required).
AUTOMATIONS_LOG_DB_ID = "2c419e8c-02fd-8006-851a-000bd9529e5f"
LIVE_STATUS_DB_ID = "2c419e8c-02fd-8003-89e7-000b28fed4b1"


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json",
    }


def log_automation(token, name, job_type, status, source, details=""):
    """Append a row to Automations Log 2026 — one call per job run."""
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": AUTOMATIONS_LOG_DB_ID},
        "properties": {
            "Date": {"title": [{"text": {"content": name}}]},
            "Type": {"rich_text": [{"text": {"content": job_type}}]},
            "Status": {"rich_text": [{"text": {"content": status}}]},
            "Source": {"rich_text": [{"text": {"content": source}}]},
            "Details": {"rich_text": [{"text": {"content": details}}]},
        },
    }
    r = requests.post(f"{NOTION_API}/pages", headers=_headers(token), json=payload)
    r.raise_for_status()
    return r.json()


def upsert_heartbeat(token, machine_name, value, category="AI Systems", notes=""):
    """Find-or-create the row for this machine in Live Status Backend, then update it."""
    query = {"filter": {"property": "Metric", "title": {"equals": machine_name}}}
    r = requests.post(
        f"{NOTION_API}/data_sources/{LIVE_STATUS_DB_ID}/query",
        headers=_headers(token),
        json=query,
    )
    r.raise_for_status()
    results = r.json().get("results", [])

    props = {
        "Value": {"rich_text": [{"text": {"content": value}}]},
        "Category": {"rich_text": [{"text": {"content": category}}]},
        "Last Updated": {
            "rich_text": [{"text": {"content": datetime.now(timezone.utc).isoformat()}}]
        },
        "Notes": {"rich_text": [{"text": {"content": notes}}]},
    }

    if results:
        page_id = results[0]["id"]
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=_headers(token),
            json={"properties": props},
        )
    else:
        props["Metric"] = {"title": [{"text": {"content": machine_name}}]}
        r = requests.post(
            f"{NOTION_API}/pages",
            headers=_headers(token),
            json={
                "parent": {"type": "data_source_id", "data_source_id": LIVE_STATUS_DB_ID},
                "properties": props,
            },
        )
    r.raise_for_status()
    return r.json()
