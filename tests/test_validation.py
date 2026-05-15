"""Tests for src/focus_bridge/validation.py."""

from datetime import datetime, timezone

import polars as pl
import pytest

from focus_bridge.validation import (
    FocusValidationError,
    validate,
    validate_strict,
)


def _valid_df() -> pl.DataFrame:
    """Build a minimally-valid FOCUS DataFrame for use as a test baseline."""
    return pl.DataFrame(
        {
            "BillingPeriodStart": [datetime(2024, 1, 1, tzinfo=timezone.utc)],
            "BillingPeriodEnd": [datetime(2024, 2, 1, tzinfo=timezone.utc)],
            "BillingAccountId": ["111122223333"],
            "BillingCurrency": ["USD"],
            "ChargePeriodStart": [datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)],
            "ChargePeriodEnd": [datetime(2024, 1, 15, 13, 0, tzinfo=timezone.utc)],
            "ChargeCategory": ["Usage"],
            "BilledCost": [0.0416],
            "EffectiveCost": [0.0416],
            "ContractedCost": [0.0416],
            "ListCost": [0.0416],
        }
    )


class TestValidValidates:
    def test_minimal_valid_df_passes(self):
        assert validate(_valid_df()) == []

    def test_strict_does_not_raise_on_valid(self):
        validate_strict(_valid_df())  # Should not raise.


class TestRequiredColumns:
    def test_missing_billing_account_id(self):
        df = _valid_df().drop("BillingAccountId")
        errors = validate(df)
        assert any("Missing required columns" in e for e in errors)
        assert any("BillingAccountId" in e for e in errors)

    def test_strict_raises_on_missing(self):
        df = _valid_df().drop("ChargeCategory")
        with pytest.raises(FocusValidationError, match="Missing required"):
            validate_strict(df)


class TestNullability:
    def test_null_billing_account_id_caught(self):
        df = _valid_df().with_columns(
            pl.lit(None).cast(pl.String).alias("BillingAccountId")
        )
        errors = validate(df)
        assert any("BillingAccountId" in e and "null" in e for e in errors)


class TestEnumConstraints:
    def test_invalid_charge_category_caught(self):
        df = _valid_df().with_columns(pl.lit("InvalidCategory").alias("ChargeCategory"))
        errors = validate(df)
        assert any("ChargeCategory" in e and "InvalidCategory" in e for e in errors)

    def test_invalid_charge_class_caught(self):
        df = _valid_df().with_columns(pl.lit("BadClass").alias("ChargeClass"))
        errors = validate(df)
        assert any("ChargeClass" in e for e in errors)


class TestCrossColumnRules:
    def test_charge_period_end_before_start_caught(self):
        df = _valid_df().with_columns(
            pl.lit(datetime(2024, 1, 14, tzinfo=timezone.utc)).alias("ChargePeriodEnd")
        )
        errors = validate(df)
        assert any("ChargePeriodStart >= ChargePeriodEnd" in e for e in errors)