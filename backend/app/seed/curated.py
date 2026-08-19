"""
The curated part of the demo book: twelve contracts with a story attached, the
ones the suggestion chips in the UI refer to.

Terms are expressed as offsets from today rather than fixed dates, so the demo
stays coherent whenever it is seeded — there is always something expiring next
month and something that lapsed recently, not a book that has silently gone
entirely expired since the file was written.

All insured companies are fictional. Insurer names are real market
participants, used the way any broker demo uses them; none of these contracts
represents a real policy.
"""

from datetime import date, timedelta

# (id, product, insurer, company, industry, sum_insured, premium, deductible,
#  end_offset_days, term_months, broker, renewal_pending, renewal_count, notes)
CURATED = [
    (
        "FL-0142", "D&O", "Chubb", "Novaris Technologies GmbH", "Software",
        10_000_000, 42_500, 50_000, 42, 12, "M. Hoffmann", True, 2,
        "Side-A cover included. Board expanded by two NEDs in Q2; limit review requested.",
    ),
    (
        "FL-0143", "Cyber", "AXA XL", "Kranzler Maschinenbau AG", "Mechanical Engineering",
        5_000_000, 61_000, 100_000, 134, 12, "S. Reinhardt", False, 1,
        "OT/ICS exposure surveyed. Ransomware sublimit 2.5m.",
    ),
    (
        "FL-0144", "PI", "HDI", "Nordwind Logistik GmbH", "Transport & Logistics",
        3_000_000, 18_900, 25_000, -50, 12, "M. Hoffmann", False, 0,
        "Lapsed at expiry — client reviewing whether cover is still required.",
    ),
    (
        "FL-0145", "W&I", "Zurich", "Brenner & Soehne Beteiligungen GmbH", "Private Equity",
        25_000_000, 210_000, 250_000, 224, 12, "Dr. K. Vogel", False, 0,
        "Single-transaction W&I for the Steinbach carve-out. No claims notified.",
    ),
    (
        "FL-0146", "D&O", "Allianz", "Adlerhof Klinikgruppe SE", "Healthcare",
        15_000_000, 88_000, 100_000, 57, 12, "Dr. K. Vogel", False, 3,
        "Regulatory investigation cover extended. Two open notifications.",
    ),
    (
        "FL-0147", "Crime", "Markel", "Steinbach Chemie GmbH & Co. KG", "Chemicals",
        5_000_000, 27_400, 50_000, 165, 12, "S. Reinhardt", False, 1,
        "Social engineering fraud sublimit 1m.",
    ),
    (
        "FL-0148", "Cyber", "Hiscox", "Lumen Digital Health AG", "MedTech",
        8_000_000, 95_500, 150_000, 27, 12, "M. Hoffmann", True, 2,
        "Patient-data exposure. Insurer requesting updated pen-test before renewal.",
    ),
    (
        "FL-0149", "PI", "Ergo", "Weserstadt Immobilien GmbH", "Real Estate",
        2_000_000, 14_200, 20_000, 315, 12, "T. Albrecht", False, 4,
        "Valuation services included by endorsement.",
    ),
    (
        "FL-0150", "D&O", "VOV", "Ottweiler Praezisionstechnik GmbH", "Manufacturing",
        5_000_000, 21_800, 25_000, 103, 12, "T. Albrecht", False, 5,
        "Mittelstand standard wording. Clean loss record.",
    ),
    (
        "FL-0151", "EPLI", "AIG", "Feldmann Consulting Partner GmbH", "Professional Services",
        3_000_000, 33_600, 35_000, 12, 12, "S. Reinhardt", False, 2,
        "One discrimination claim closed in March. Expires imminently.",
    ),
    (
        "FL-0152", "D&O", "Allianz", "Rheinmark Energie AG", "Utilities",
        20_000_000, 134_000, 200_000, -35, 24, "Dr. K. Vogel", False, 1,
        "Expired without renewal instruction. Client restructuring the tower.",
    ),
    (
        "FL-0153", "Cyber", "Chubb", "Cortex Robotics GmbH", "Robotics",
        4_000_000, 38_900, 50_000, 193, 12, "T. Albrecht", False, 0,
        "First-time buyer. Contingent business interruption included.",
    ),
]

PRODUCT_PREFIX = {
    "D&O": "DO", "Cyber": "CY", "PI": "PI", "Crime": "CR", "EPLI": "EP", "W&I": "WI",
}

# Kept stable so the curated contracts always show the same policy reference.
CURATED_POLICY_DIGITS = {
    "FL-0142": "8891234", "FL-0143": "4410882", "FL-0144": "2277341",
    "FL-0145": "9930017", "FL-0146": "5512908", "FL-0147": "3348820",
    "FL-0148": "7781093", "FL-0149": "6620455", "FL-0150": "1194762",
    "FL-0151": "8834011", "FL-0152": "4405518", "FL-0153": "2298744",
}


def add_months(start: date, months: int) -> date:
    """Add whole months, clamping to the last valid day of the target month."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    day = min(start.day, days_in_month(year, month))
    return date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def curated_contracts(today: date | None = None) -> list[dict]:
    today = today or date.today()
    rows = []
    for (
        cid, product, insurer, company, industry, sum_insured, premium, deductible,
        end_offset, term_months, broker, renewal_pending, renewal_count, notes,
    ) in CURATED:
        end = today + timedelta(days=end_offset)
        start = add_months(end, -term_months)
        rows.append(
            {
                "id": cid,
                "policy_number": (
                    f"{PRODUCT_PREFIX[product]}-{CURATED_POLICY_DIGITS[cid]}-{end:%y}"
                ),
                "product": product,
                "insurer": insurer,
                "insured_company": company,
                "industry": industry,
                "sum_insured": sum_insured,
                "premium": premium,
                "deductible": deductible,
                "start_date": start,
                "end_date": end,
                "broker": broker,
                "notes": notes,
                "is_draft": False,
                "renewal_pending": renewal_pending,
                "renewal_count": renewal_count,
                "created_by_assistant": False,
            }
        )
    return rows
