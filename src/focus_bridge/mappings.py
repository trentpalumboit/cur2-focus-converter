"""
Mapping functions: CUR 2.0 columns → FOCUS 1.2 columns.

Each function is a pure function: no I/O, no side effects, no global state.
Given a CUR value (or row), it returns the corresponding FOCUS value.

The pipeline module orchestrates these mappings against a DataFrame.
This module is where 90% of the project's logic and reviewable decisions live.

Spec reference: https://focus.finops.org/focus-specification/v1-2/
"""

from datetime import datetime, timezone


# --- Date/time mappings ---------------------------------------------------


def map_iso8601_utc(value: str | datetime) -> str:
    """
    Convert a CUR datetime value to FOCUS ISO 8601 UTC format.

    FOCUS spec requires all date/time values to be:
      - In UTC (no other offsets allowed)
      - ISO 8601 extended format: 'YYYY-MM-DDTHH:mm:ssZ'

    AWS CUR 2.0 stores datetimes as Parquet timestamps in UTC, so Polars
    reads them as timezone-aware datetime objects. We accept strings too
    for testing convenience.

    Used for: BillingPeriodStart, BillingPeriodEnd, ChargePeriodStart, ChargePeriodEnd.

    Spec reference: FOCUS 1.2 § 3 (Date/Time Format)
    """
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value

    if dt.tzinfo is None:
        # Naive datetime — assume UTC per FOCUS spec.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Identity / direct-copy mappings --------------------------------------


def map_billing_account_id(value: str) -> str:
    """
    CUR 'bill_payer_account_id' → FOCUS 'BillingAccountId'.

    The account that receives the invoice. For AWS, this is the management
    (formerly 'payer') account in an Organizations setup, or the standalone
    account ID for single-account customers.

    FOCUS requires: String, MUST be present, MUST NOT be null.

    Spec reference: FOCUS 1.2 § 2.3
    """
    if not value:
        raise ValueError("BillingAccountId cannot be null per FOCUS 1.2 § 2.3")
    return str(value)


def map_sub_account_id(value: str | None) -> str | None:
    """
    CUR 'line_item_usage_account_id' → FOCUS 'SubAccountId'.

    The account that actually incurred the charge. For consolidated billing,
    this differs from BillingAccountId; for standalone accounts they match.

    FOCUS requires: String, MAY be present, MAY be null (when no sub-account
    structure exists).

    Spec reference: FOCUS 1.2 § 2.36
    """
    return str(value) if value else None


def map_billing_currency(value: str) -> str:
    """
    CUR 'line_item_currency_code' → FOCUS 'BillingCurrency'.

    FOCUS requires ISO 4217 three-letter currency code (e.g., 'USD', 'EUR').
    AWS always emits valid ISO 4217 codes.

    Spec reference: FOCUS 1.2 § 2.5
    """
    if not value or len(value) != 3:
        raise ValueError(f"Invalid currency code: {value!r}")
    return value.upper()


# --- Enum mappings --------------------------------------------------------


def map_charge_category(line_item_type: str) -> str:
    """
    CUR 'line_item_line_item_type' → FOCUS 'ChargeCategory'.

    FOCUS defines five allowed values: Usage | Purchase | Tax | Credit | Adjustment.
    AWS CUR has ~11 LineItemType values that must be collapsed into these five.

    Mapping decisions documented in docs/mappings/decisions.md.

    Spec reference: FOCUS 1.2 § 2.8
    """
    mapping = {
        "Usage": "Usage",
        "DiscountedUsage": "Usage",
        "SavingsPlanCoveredUsage": "Usage",
        "RIFee": "Purchase",
        "SavingsPlanRecurringFee": "Purchase",
        "SavingsPlanNegation": "Adjustment",
        "Tax": "Tax",
        "Fee": "Purchase",
        "Credit": "Credit",
        "Refund": "Adjustment",
        "BundledDiscount": "Adjustment",
    }
    if line_item_type not in mapping:
        raise ValueError(f"Unhandled LineItemType for ChargeCategory: {line_item_type!r}")
    return mapping[line_item_type]


def map_charge_class(line_item_type: str) -> str | None:
    """
    CUR 'line_item_line_item_type' → FOCUS 'ChargeClass'.

    FOCUS ChargeClass has one allowed value: 'Correction'. It's used to flag
    rows that adjust a previously billed charge. Null otherwise.

    Refund and BundledDiscount are corrections to earlier billed amounts.

    Spec reference: FOCUS 1.2 § 2.9
    """
    if line_item_type in ("Refund", "BundledDiscount"):
        return "Correction"
    return None


# --- Cost mappings (the core mapping decision) ----------------------------


def derive_costs(
    line_item_type: str,
    unblended_cost: float | None,
    net_unblended_cost: float | None,
    public_on_demand_cost: float | None,
    reservation_effective_cost: float | None,
    sp_effective_cost: float | None,
) -> dict[str, float]:
    """
    Derive FOCUS's four cost columns from CUR's cost columns.

    Returns a dict with keys: BilledCost, EffectiveCost, ContractedCost, ListCost.

    This is the single most important mapping decision in the converter.
    See docs/mappings/decisions.md for the full rationale.

    The four FOCUS cost columns mean:
      - BilledCost:    What appears on the invoice for this row.
      - EffectiveCost: Amortized cost including commitment discounts.
      - ContractedCost: Cost at the negotiated rate (post-EDP, pre-commitment).
      - ListCost:      Cost at the public on-demand rate (pre-any-discount).

    Spec references: FOCUS 1.2 § 2.2 (BilledCost), § 2.20 (EffectiveCost),
                     § 2.13 (ContractedCost), § 2.27 (ListCost)
    """
    # Coerce Nones to 0 for arithmetic safety.
    unblended = unblended_cost or 0.0
    net_unblended = net_unblended_cost or 0.0
    public = public_on_demand_cost or 0.0
    ri_eff = reservation_effective_cost or 0.0
    sp_eff = sp_effective_cost or 0.0

    if line_item_type == "Usage":
        return {
            "BilledCost": unblended,
            "EffectiveCost": unblended,
            "ContractedCost": net_unblended,
            "ListCost": public,
        }

    if line_item_type == "DiscountedUsage":
        # RI-covered usage. unblended_cost is 0 because the RI fee was paid
        # separately as an RIFee line. The amortized cost lives in
        # reservation_effective_cost.
        return {
            "BilledCost": 0.0,
            "EffectiveCost": ri_eff,
            "ContractedCost": ri_eff,
            "ListCost": public,
        }

    if line_item_type == "SavingsPlanCoveredUsage":
        return {
            "BilledCost": 0.0,
            "EffectiveCost": sp_eff,
            "ContractedCost": sp_eff,
            "ListCost": public,
        }

    if line_item_type in ("RIFee", "SavingsPlanRecurringFee"):
        # The commitment fee itself. EffectiveCost is 0 because the economic
        # value of this fee is amortized into the *CoveredUsage rows.
        return {
            "BilledCost": unblended,
            "EffectiveCost": 0.0,
            "ContractedCost": unblended,
            "ListCost": unblended,
        }

    if line_item_type in ("Tax", "Fee", "Credit", "Refund", "Adjustment", "BundledDiscount"):
        return {
            "BilledCost": unblended,
            "EffectiveCost": unblended,
            "ContractedCost": unblended,
            "ListCost": public if public else unblended,
        }

    raise ValueError(f"Unhandled LineItemType for cost derivation: {line_item_type!r}")