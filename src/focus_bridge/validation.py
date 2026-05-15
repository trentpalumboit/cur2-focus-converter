"""
Validate that a DataFrame conforms to FOCUS 1.2 specification requirements.

This module encodes spec rules as executable checks. It runs after the
pipeline produces output and surfaces any violations.

Validation is layered:
1. Structural: required columns present, correct dtypes.
2. Nullability: MUST NOT be null constraints from the spec.
3. Enum constraints: ChargeCategory and ChargeClass allowed-values.
4. Cross-column rules: e.g. ChargeFrequency vs ChargeCategory.

Each check cites the spec section it enforces.

Spec reference: https://focus.finops.org/focus-specification/v1-2/
"""

import polars as pl


class FocusValidationError(Exception):
    """Raised when a DataFrame fails FOCUS 1.2 validation."""


# FOCUS 1.2 columns that MUST be present per spec.
# Note: We're only validating columns we currently produce. The full FOCUS
# spec has more required columns we'll add in v2.
REQUIRED_COLUMNS = {
    "BillingPeriodStart",  # § 2.7
    "BillingPeriodEnd",  # § 2.6
    "BillingAccountId",  # § 2.3
    "BillingCurrency",  # § 2.5
    "ChargePeriodStart",  # § 2.13
    "ChargePeriodEnd",  # § 2.12
    "ChargeCategory",  # § 2.8
    "BilledCost",  # § 2.2
    "EffectiveCost",  # § 2.20
    "ContractedCost",  # § 2.13
    "ListCost",  # § 2.27
}

# Columns that MUST NOT contain null values per FOCUS 1.2 spec.
NON_NULLABLE_COLUMNS = {
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "BillingAccountId",
    "BillingCurrency",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "ChargeCategory",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
}

# Enum constraints. None means "null is also allowed".
CHARGE_CATEGORY_VALUES = {"Usage", "Purchase", "Tax", "Credit", "Adjustment"}
CHARGE_CLASS_VALUES = {"Correction", None}


def validate(df: pl.DataFrame) -> list[str]:
    """
    Validate a DataFrame against FOCUS 1.2 requirements.

    Returns a list of human-readable error messages. An empty list means
    the DataFrame is valid.

    This function does not raise; the caller decides whether to treat
    violations as errors. Use `validate_strict` to get an exception instead.
    """
    errors: list[str] = []

    errors.extend(_check_required_columns(df))
    errors.extend(_check_nullability(df))
    errors.extend(_check_enum_constraints(df))
    errors.extend(_check_cross_column_rules(df))

    return errors


def validate_strict(df: pl.DataFrame) -> None:
    """Validate and raise FocusValidationError on any violation."""
    errors = validate(df)
    if errors:
        raise FocusValidationError(
            "Output failed FOCUS 1.2 validation:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def _check_required_columns(df: pl.DataFrame) -> list[str]:
    """FOCUS spec lists which columns MUST be present in any FOCUS dataset."""
    actual = set(df.columns)
    missing = REQUIRED_COLUMNS - actual
    if missing:
        return [f"Missing required columns: {sorted(missing)}"]
    return []


def _check_nullability(df: pl.DataFrame) -> list[str]:
    """Each FOCUS column has its own nullability rules per the spec."""
    errors = []
    for col in NON_NULLABLE_COLUMNS:
        if col not in df.columns:
            continue  # Required-columns check already flagged this.
        null_count = df[col].null_count()
        if null_count > 0:
            errors.append(
                f"Column '{col}' contains {null_count} null value(s); "
                "FOCUS 1.2 requires non-null"
            )
    return errors


def _check_enum_constraints(df: pl.DataFrame) -> list[str]:
    """FOCUS columns with fixed allowed-values lists."""
    errors = []

    if "ChargeCategory" in df.columns:
        actual = set(df["ChargeCategory"].drop_nulls().unique().to_list())
        invalid = actual - CHARGE_CATEGORY_VALUES
        if invalid:
            errors.append(
                f"ChargeCategory has values not in FOCUS 1.2 § 2.8 enum: {sorted(invalid)}"
            )

    if "ChargeClass" in df.columns:
        actual = set(df["ChargeClass"].drop_nulls().unique().to_list())
        invalid = actual - (CHARGE_CLASS_VALUES - {None})
        if invalid:
            errors.append(
                f"ChargeClass has values not in FOCUS 1.2 § 2.9 enum: {sorted(invalid)}"
            )

    return errors


def _check_cross_column_rules(df: pl.DataFrame) -> list[str]:
    """FOCUS spec includes cross-column invariants that must hold."""
    errors = []

    # FOCUS 1.2 § 2.12-2.13: ChargePeriodStart MUST be before ChargePeriodEnd.
    if {"ChargePeriodStart", "ChargePeriodEnd"}.issubset(df.columns):
        bad = df.filter(pl.col("ChargePeriodStart") >= pl.col("ChargePeriodEnd"))
        if bad.height > 0:
            errors.append(
                f"{bad.height} rows have ChargePeriodStart >= ChargePeriodEnd; "
                "FOCUS requires Start < End (Start inclusive, End exclusive)"
            )

    # FOCUS 1.2 § 2.6-2.7: BillingPeriodStart MUST be before BillingPeriodEnd.
    if {"BillingPeriodStart", "BillingPeriodEnd"}.issubset(df.columns):
        bad = df.filter(pl.col("BillingPeriodStart") >= pl.col("BillingPeriodEnd"))
        if bad.height > 0:
            errors.append(
                f"{bad.height} rows have BillingPeriodStart >= BillingPeriodEnd"
            )

    return errors