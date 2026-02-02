from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# schwab-py
from schwab.auth import easy_client
from rich import print

def main() -> None:
    load_dotenv()

    client_id = os.environ.get("SCHWAB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI", "").strip()
    token_path = os.environ.get("SCHWAB_TOKEN_PATH", ".token.json").strip()

    if not client_id or not client_secret or not redirect_uri:
        raise SystemExit(
            "Missing env vars. Make sure SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET, "
            "SCHWAB_REDIRECT_URI are set in .env"
        )

    token_file = Path(token_path).expanduser().resolve()

    print("[bold cyan]Starting Schwab OAuth…[/bold cyan]")
    print(f"Token file: [yellow]{token_file}[/yellow]")
    print(f"Redirect URI: [yellow]{redirect_uri}[/yellow]")

    # This will:
    # - open a browser to Schwab login/consent
    # - receive the callback on your local loopback
    # - write/refresh tokens into token_file
    client = easy_client(
        api_key=client_id,
        app_secret=client_secret,
        callback_url=redirect_uri,
        token_path=str(token_file),
    )

    # Quick smoke test after auth:
    r = client.get_account_numbers()
    r.raise_for_status()
    print("[bold green]OAuth success![/bold green]")
    print(r.json())

if __name__ == "__main__":
    main()
