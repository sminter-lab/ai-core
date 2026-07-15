"""Financial math and pre-filter thresholds for real estate deal scoring."""

from __future__ import annotations

MORTGAGE_RATE     = 0.063   # 6.3% — update via RE_MORTGAGE_RATE env var in run.py
DOWN_PCT          = 0.20    # 20% default down payment
MIN_CASH_FLOW     = 500.0   # $/mo net after all expenses
MIN_COC           = 0.08    # 8% cash-on-cash return
MIN_CAP_RATE      = 0.08    # 8% cap rate
MIN_DSCR          = 1.25    # debt service coverage ratio
VACANCY_RATE      = 0.10    # 10% vacancy for commercial NOI calculation
TAX_RATE_ANNUAL   = 0.012   # 1.2% of purchase price (low-tax Southeast assumption)
INSURANCE_ANNUAL  = 0.005   # 0.5% of purchase price
MAINT_RATE        = 0.01    # 1% of purchase price per year

# States with landlord-friendly landlord-tenant law
LANDLORD_FRIENDLY_STATES = {
    "AL", "AR", "AZ", "CO", "FL", "GA", "IN", "KY",
    "MO", "NC", "NV", "OK", "SC", "TN", "TX", "VA", "WV",
}

# Population-growth metros used as a soft scoring signal
GROWTH_MARKETS = {
    "atlanta", "huntsville", "birmingham", "nashville", "charlotte",
    "raleigh", "charleston", "jacksonville", "tampa", "orlando",
    "memphis", "chattanooga", "greenville", "savannah", "augusta",
    "columbus", "macon", "knoxville", "columbia", "durham", "richmond",
    "virginia beach", "lexington", "louisville", "little rock",
    "baton rouge", "new orleans", "gainesville", "pensacola", "mobile",
    "montgomery", "henry county", "forsyth county", "gwinnett",
}


def monthly_payment(principal: float, annual_rate: float, years: int = 30) -> float:
    r = annual_rate / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def residential_metrics(
    price: float,
    rent: float,
    down_pct: float = DOWN_PCT,
    annual_rate: float = MORTGAGE_RATE,
    tax_annual: float | None = None,
    insurance_annual: float | None = None,
) -> dict:
    down       = price * down_pct
    loan       = price - down
    mortgage   = monthly_payment(loan, annual_rate)
    taxes      = (tax_annual if tax_annual is not None else price * TAX_RATE_ANNUAL) / 12
    insurance  = (insurance_annual if insurance_annual is not None else price * INSURANCE_ANNUAL) / 12
    maint      = price * MAINT_RATE / 12
    expenses   = mortgage + taxes + insurance + maint
    cash_flow  = rent - expenses
    coc        = (cash_flow * 12) / down if down > 0 else 0.0
    return {
        "down":         down,
        "loan":         loan,
        "mortgage":     mortgage,
        "taxes_mo":     taxes,
        "insurance_mo": insurance,
        "maint_mo":     maint,
        "expenses":     expenses,
        "cash_flow":    cash_flow,
        "coc":          coc,
    }


def commercial_metrics(
    price: float,
    gross_annual_income: float,
    down_pct: float = DOWN_PCT,
    annual_rate: float = MORTGAGE_RATE,
    vacancy_rate: float = VACANCY_RATE,
) -> dict:
    noi             = gross_annual_income * (1 - vacancy_rate)
    cap_rate        = noi / price if price > 0 else 0.0
    down            = price * down_pct
    loan            = price - down
    annual_debt_svc = monthly_payment(loan, annual_rate) * 12
    dscr            = noi / annual_debt_svc if annual_debt_svc > 0 else 0.0
    return {
        "noi":         noi,
        "cap_rate":    cap_rate,
        "down":        down,
        "annual_debt": annual_debt_svc,
        "dscr":        dscr,
    }


def residential_passes(m: dict) -> bool:
    return m["cash_flow"] >= MIN_CASH_FLOW and m["coc"] >= MIN_COC


def commercial_passes(m: dict) -> bool:
    return m["cap_rate"] >= MIN_CAP_RATE and m["dscr"] >= MIN_DSCR


def in_landlord_friendly_state(state: str) -> bool:
    return (state or "").upper().strip() in LANDLORD_FRIENDLY_STATES


def in_growth_market(city: str) -> bool:
    return (city or "").lower().strip() in GROWTH_MARKETS
