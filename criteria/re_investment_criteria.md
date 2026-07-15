# Real Estate Investment Criteria

_Source of truth for `jobs/analyze_realestate.py` thresholds (mirrored in `jobs/re_criteria.py`)._

## Residential (1–4 units)

| Metric | Threshold |
|---|---|
| Net cash flow | ≥ $500/mo after all expenses |
| Cash-on-cash return | ≥ 8% |
| Financing assumption | 6.3% rate, 20% down, 30yr |
| Taxes / insurance / maintenance | 1.2% / 0.5% / 1.0% of price per year |
| Rent estimate | Zillow rentZestimate, else 0.75% rule |
| Price band | $80k–$600k, 2–4 beds |

## Commercial

| Metric | Threshold |
|---|---|
| Cap rate | ≥ 8% |
| DSCR | ≥ 1.25 |
| Vacancy assumption | 10% |
| NNN rent estimate (no listing data) | $12/sqft/yr |
| Minimum price | $100k |

## Soft signals (scoring, not gating)

- Landlord-friendly states: AL, AR, AZ, CO, FL, GA, IN, KY, MO, NC, NV, OK, SC, TN, TX, VA, WV
- Population-growth Southeast metros (Atlanta, Huntsville, Nashville, Charlotte, Tampa, …)

## Verdicts (analyzer output)

- Score ≥ 8 → **GO**
- Score = 7 → **REVIEW**
- Score < 7 → **NO-GO** (logged, never surfaced)
- Max **3 leads/day** surface in the morning digest.
