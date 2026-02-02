from __future__ import annotations

from rich import print
from workers.schwab.client import get_client


def main():
    c = get_client()

    # 1) Get account list (number + hash)
    r = c.get_account_numbers()
    r.raise_for_status()
    acct_map = r.json()

    print("[bold]Account numbers:[/bold]")
    print(acct_map)

    # 2) Pick first account hash
    account_hash = acct_map[0]["hashValue"]

    # 3) Account snapshot + positions (ENUM, not string)
    r = c.get_account(account_hash, fields=[c.Account.Fields.POSITIONS])
    r.raise_for_status()
    acct = r.json()

    print("\n[bold]Account snapshot (positions included):[/bold]")
    print(acct)

    # 4) Pull account type/subtype (taxable vs IRA check)
    sec = acct.get("securitiesAccount", {})
    print("\n[bold]Account type:[/bold]")
    print(
        {
            "type": sec.get("type"),
            "accountSubType": sec.get("accountSubType"),
        }
    )

    # 5) Quote test
    r = c.get_quote("AAPL")
    r.raise_for_status()
    print("\n[bold]AAPL quote:[/bold]")
    print(r.json())


if __name__ == "__main__":
    main()
