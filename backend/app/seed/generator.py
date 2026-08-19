"""
Deterministic generator for the rest of the demo book.

Two things matter for a portfolio that is useful to demo against:

1. **Correlated figures.** Premium is a rate on the limit, not a random number,
   and the rate band depends on the product. Deductibles scale with the limit.
   Otherwise the table reads as noise and nothing the assistant reports about it
   sounds credible.

2. **A realistic expiry pipeline.** Terms are placed relative to today against a
   target status mix, so there is always a handful of contracts expiring in the
   next few weeks, a couple that lapsed recently, and a long tail of active
   business — which is what makes "what needs renewing?" a real question.

Seeded with a fixed value, so the same book comes back every time. Dates are
relative to today, so re-seeding months later still produces a live-looking book
rather than one that has entirely expired.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from .curated import PRODUCT_PREFIX, add_months

# (company, industry) — all fictional.
COMPANIES = [
    ("Haldenbach Automation GmbH", "Industrial Automation"),
    ("Seeblick Reisen AG", "Travel"),
    ("Trostberg Pharma GmbH", "Pharmaceuticals"),
    ("Kirchner Netzwerke GmbH", "Telecoms"),
    ("Wollmar Textilwerke GmbH & Co. KG", "Textiles"),
    ("Auenthal Agrartechnik AG", "Agritech"),
    ("Perlberg Finanzberatung GmbH", "Financial Advisory"),
    ("Drosselweg Verlag GmbH", "Publishing"),
    ("Silberbrunn Getraenke AG", "Food & Beverage"),
    ("Marbeck Kunststofftechnik GmbH", "Plastics"),
    ("Uferstadt Stadtwerke AG", "Utilities"),
    ("Falkenstein Baugruppe GmbH", "Construction"),
    ("Zeitler Halbleiter AG", "Semiconductors"),
    ("Rosenhain Kliniken GmbH", "Healthcare"),
    ("Lindtal Logistikzentrum GmbH", "Transport & Logistics"),
    ("Brackwede Recycling AG", "Waste Management"),
    ("Neuhof Beteiligungsgesellschaft mbH", "Private Equity"),
    ("Talmuehle Muehlenwerke GmbH", "Food Processing"),
    ("Ostwind Windkraft AG", "Renewable Energy"),
    ("Gruenberg Analytik GmbH", "Laboratory Services"),
    ("Hasselbrook Immobilien AG", "Real Estate"),
    ("Sandkrug Werkzeugbau GmbH", "Tooling"),
    ("Vierlanden Versandhandel GmbH", "E-commerce"),
    ("Moorfeld Biotech AG", "Biotech"),
    ("Reuterhof Steuerberatung GmbH", "Professional Services"),
    ("Kaltenbach Kaeltetechnik GmbH", "Refrigeration"),
    ("Elmsheim Sicherheitsdienste GmbH", "Security Services"),
    ("Widdersdorf Maschinenhandel GmbH", "Machinery Trade"),
    ("Poppenbuettel Software AG", "Software"),
    ("Ahrensfelde Elektronik GmbH", "Electronics"),
    ("Suederhof Fischerei AG", "Fisheries"),
    ("Bergedorf Praezision GmbH", "Precision Engineering"),
    ("Klarwasser Umwelttechnik GmbH", "Environmental Tech"),
    ("Rittersbach Weingut AG", "Beverages"),
    ("Nordheide Datenzentren GmbH", "Data Centres"),
    ("Steinfurth Chemiehandel GmbH", "Chemical Distribution"),
    ("Lauenburg Personaldienste GmbH", "Staffing"),
    ("Weidenau Kabelwerke AG", "Cables"),
    ("Ohlsdorf Medizintechnik GmbH", "MedTech"),
    ("Barmstedt Fahrzeugbau GmbH", "Vehicle Manufacturing"),
]

INSURERS = [
    "Allianz", "AXA XL", "HDI", "Zurich", "Chubb",
    "Markel", "AIG", "Ergo", "VOV", "Hiscox",
]

BROKERS = [
    "M. Hoffmann", "S. Reinhardt", "Dr. K. Vogel",
    "T. Albrecht", "J. Kessler", "A. Brandt",
]

# limits: available lines. rate: premium as a fraction of the limit.
# ded: deductible as a fraction of the limit.
PRODUCT_PROFILE = {
    "D&O": {
        "limits": [2_000_000, 5_000_000, 10_000_000, 15_000_000, 20_000_000, 25_000_000],
        "rate": (0.0035, 0.0075),
        "ded": (0.004, 0.010),
        "weight": 30,
    },
    "Cyber": {
        "limits": [1_000_000, 2_000_000, 3_000_000, 5_000_000, 8_000_000, 10_000_000],
        "rate": (0.0080, 0.0160),
        "ded": (0.010, 0.025),
        "weight": 26,
    },
    "PI": {
        "limits": [1_000_000, 2_000_000, 3_000_000, 5_000_000],
        "rate": (0.0050, 0.0110),
        "ded": (0.005, 0.015),
        "weight": 18,
    },
    "Crime": {
        "limits": [1_000_000, 2_500_000, 5_000_000],
        "rate": (0.0040, 0.0080),
        "ded": (0.005, 0.015),
        "weight": 10,
    },
    "EPLI": {
        "limits": [1_000_000, 2_000_000, 3_000_000, 5_000_000],
        "rate": (0.0080, 0.0150),
        "ded": (0.008, 0.020),
        "weight": 9,
    },
    "W&I": {
        "limits": [10_000_000, 15_000_000, 25_000_000, 40_000_000],
        "rate": (0.0070, 0.0140),
        "ded": (0.008, 0.015),
        "weight": 7,
    },
}

NOTE_TEMPLATES = {
    "D&O": [
        "Side-A DIC in place. {n} directors covered.",
        "Mittelstand wording. No notifications this period.",
        "Entity cover for securities claims excluded.",
        "Retired-director run-off agreed for six years.",
        "Limit stepped up at last renewal following board expansion.",
    ],
    "Cyber": [
        "Ransomware sublimit {sub}m. MFA verified across all admin accounts.",
        "Business interruption waiting period 8 hours.",
        "Insurer requires an annual pen-test as a condition precedent.",
        "Incident response retainer included via the insurer's panel.",
        "Contingent BI extended to the two largest suppliers.",
    ],
    "PI": [
        "Retroactive cover to {year}. No circumstances notified.",
        "Design-and-construct activities included by endorsement.",
        "Aggregate limit, reinstatement not available.",
        "Sub-consultant liability written back in.",
    ],
    "Crime": [
        "Social engineering fraud sublimit {sub}m.",
        "Third-party crime included alongside employee dishonesty.",
        "Dual-authorisation controls evidenced at survey.",
    ],
    "EPLI": [
        "Wage-and-hour defence costs sublimit {sub}m.",
        "Works council consultation exposure noted.",
        "One claim closed without payment in the prior period.",
    ],
    "W&I": [
        "Single-transaction buy-side policy. Retention drops after 12 months.",
        "Tax deed of covenant excluded from cover.",
        "Synthetic warranties agreed for two schedules.",
    ],
}


def _round_to(value: float, step: int) -> int:
    return max(step, int(round(value / step)) * step)


def generate_contracts(
    count: int, start_index: int, today: date | None = None, seed: int = 20260819
) -> list[dict]:
    """
    Build `count` contracts with ids continuing from FL-{start_index:04d}.

    The status mix is targeted rather than left to chance: roughly 10% already
    expired, 25% expiring inside the 90-day window, 61% comfortably active and
    2 drafts, so every filter in the UI has something to find.
    """
    rng = random.Random(seed)
    today = today or date.today()

    companies = COMPANIES[:]
    rng.shuffle(companies)

    products = list(PRODUCT_PROFILE)
    weights = [PRODUCT_PROFILE[p]["weight"] for p in products]

    # Target status mix, laid out then shuffled so ids aren't grouped by status.
    n_expired = max(1, round(count * 0.10))
    n_expiring = max(2, round(count * 0.25))
    n_draft = 2 if count >= 20 else 0
    n_active = max(0, count - n_expired - n_expiring - n_draft)
    buckets = (
        ["expired"] * n_expired
        + ["expiring"] * n_expiring
        + ["draft"] * n_draft
        + ["active"] * n_active
    )
    rng.shuffle(buckets)

    rows = []
    for i, bucket in enumerate(buckets):
        cid = f"FL-{start_index + i:04d}"
        company, industry = companies[i % len(companies)]
        product = rng.choices(products, weights=weights, k=1)[0]
        profile = PRODUCT_PROFILE[product]

        sum_insured = rng.choice(profile["limits"])
        rate = rng.uniform(*profile["rate"])
        premium = _round_to(sum_insured * rate, 100)
        deductible = _round_to(sum_insured * rng.uniform(*profile["ded"]), 5_000)

        term_months = 24 if product == "W&I" and rng.random() < 0.35 else 12
        is_draft = bucket == "draft"

        if is_draft:
            # A draft has not incepted yet: term starts in the near future.
            start = today + timedelta(days=rng.randint(7, 60))
            end = add_months(start, term_months)
        else:
            if bucket == "expired":
                end = today - timedelta(days=rng.randint(5, 240))
            elif bucket == "expiring":
                end = today + timedelta(days=rng.randint(3, 90))
            else:
                end = today + timedelta(days=rng.randint(95, 400))
            start = add_months(end, -term_months)

        # Older business has been renewed more often.
        years_on_book = max(0, (today - start).days // 365)
        renewal_count = min(6, max(0, years_on_book + rng.randint(0, 3)))

        # Brokers flag renewals as expiry approaches, not before.
        renewal_pending = bucket == "expiring" and rng.random() < 0.45

        note = rng.choice(NOTE_TEMPLATES[product]).format(
            n=rng.randint(4, 11),
            sub=rng.choice([1, 1.5, 2, 2.5]),
            year=start.year - rng.randint(1, 6),
        )

        digits = f"{rng.randint(1_000_000, 9_999_999)}"
        rows.append(
            {
                "id": cid,
                "policy_number": f"{PRODUCT_PREFIX[product]}-{digits}-{end:%y}",
                "product": product,
                "insurer": rng.choice(INSURERS),
                "insured_company": company,
                "industry": industry,
                "sum_insured": sum_insured,
                "premium": premium,
                "deductible": deductible,
                "start_date": start,
                "end_date": end,
                "broker": rng.choice(BROKERS),
                "notes": note,
                "is_draft": is_draft,
                "renewal_pending": renewal_pending,
                "renewal_count": 0 if is_draft else renewal_count,
                "created_by_assistant": False,
            }
        )
    return rows


def full_book(total: int = 50, today: date | None = None) -> list[dict]:
    """The curated twelve plus generated contracts up to `total`."""
    from .curated import curated_contracts

    curated = curated_contracts(today)
    remaining = max(0, total - len(curated))
    return curated + generate_contracts(remaining, start_index=154, today=today)
