from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

from schwab.auth import client_from_token_file

def get_client():
    load_dotenv()

    client_id = os.environ.get("SCHWAB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "").strip()
    token_path = os.environ.get("SCHWAB_TOKEN_PATH", ".token.json").strip()

    if not client_id or not client_secret:
        raise RuntimeError("Missing SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET in .env")

    token_file = Path(token_path).expanduser().resolve()
    if not token_file.exists():
        raise RuntimeError(f"Token file not found at {token_file}. Run auth.py first.")

    return client_from_token_file(str(token_file), client_id, client_secret)
