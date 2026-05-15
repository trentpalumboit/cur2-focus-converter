"""Tests for src/focus_bridge/mappings.py."""

from datetime import datetime, timezone

import pytest

from focus_bridge.mappings import (
    derive_costs,
    map_billing_account_id,
    map_billing_currency,
    map_charge_category,
    map_charge_class,
    map_iso8601_utc,
    map_sub_account_id,
)


class TestMapIso8601Utc:
    """FOCUS requires ISO 8601 UTC: 'YYYY-MM-DDTHH:mm:ssZ'."""

    def test_iso_string_with_z_suffix(self):
        assert map_iso8601_utc("2024-01-01T00:00:00Z") == "2024-01-01T00:00:00Z"

    def test_aware_datetime(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert map_iso8601_utc(dt) == "2024-01-15T12:00:00Z"

    def test_naive_datetime_assumed_utc(self):
        # FOCUS spec says all times MUST be UTC; we treat naive as UTC.
        dt = datetime(2024, 1, 15, 12, 0, 0)
        assert map_iso8601_utc(dt) == "2024-01-15T12:00:00Z"

    def test_non_utc_offset_normalized(self):
        # Polars shouldn't emit these, but defensive programming.
        from datetime import timedelta
        eastern = timezone(timedelta(hours=-5))
        dt = datetime(2024, 1, 15, 7, 0, 0, tzinfo=eastern)  # 7am EST
        assert map_iso8601_utc(dt) == "2024-01-15T12:00:00Z"  # noon UTC


class TestMapBillingAccountId:
    def test_typical_account_id(self):
        assert map_billing_account_id("111122223333") == "111122223333"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="cannot be null"):
            map_billing_account_id("")


class TestMapSubAccountId:
    def test_normal_value(self):
        assert map_sub_account_id("444455556666") == "444455556666"

    def test_none_allowed(self):
        assert map_sub_account_id(None) is None

    def test_empty_string_treated_as_null(self):
        assert map_sub_account_id("") is None


class TestMapBillingCurrency:
    def test_usd(self):
        assert map_billing_currency("USD") == "USD"

    def test_lowercase_normalized(self):
        assert map_billing_currency("usd") == "USD"

    def test_invalid_length_raises(self):
        with pytest.raises(ValueError, match="Invalid currency code"):
            map_billing_currency("DOLLAR")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Invalid currency code"):
            map_billing_currency("")


class TestMapChargeCategory:
    """The collapse from 11 CUR types to 5 FOCUS categories."""

    @pytest.mark.parametrize(
        "cur_type,expected",
        [
            ("Usage", "Usage"),
            ("DiscountedUsage", "Usage"),
            ("SavingsPlanCoveredUsage", "Usage"),
            ("RIFee", "Purchase"),
            ("SavingsPlanRecurringFee", "Purchase"),
            ("Fee", "Purchase"),
            ("Tax", "Tax"),
            ("Credit", "Credit"),
            ("Refund", "Adjustment"),
            ("BundledDiscount", "Adjustment"),
            ("SavingsPlanNegation", "Adjustment"),
        ],
    )
    def test_known_mappings(self, cur_type, expected):
        assert map_charge_category(cur_type) == expected

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unhandled LineItemType"):
            map_charge_category("MadeUpType")


class TestMapChargeClass:
    def test_refund_is_correction(self):
        assert map_charge_class("Refund") == "Correction"

    def test_bundled_discount_is_correction(self):
        assert map_charge_class("BundledDiscount") == "Correction"

    def test_usage_is_null(self):
        assert map_charge_class("Usage") is None

    def test_tax_is_null(self):
        assert map_charge_class("Tax") is None


class TestDeriveCosts:
    """
    The most important test class in the project.

    Each test reflects a row archetype from the synthetic sample. If a hiring
    manager reads only one test file, this is it.
    """

    def test_on_demand_usage(self):
        """On-demand EC2: all four cost columns reflect the on-demand rate."""
        result = derive_costs(
            line_item_type="Usage",
            unblended_cost=0.0416,
            net_unblended_cost=0.0416,
            public_on_demand_cost=0.0416,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result == {
            "BilledCost": 0.0416,
            "EffectiveCost": 0.0416,
            "ContractedCost": 0.0416,
            "ListCost": 0.0416,
        }

    def test_on_demand_with_edp_discount(self):
        """EDP applies: ContractedCost diverges from BilledCost."""
        result = derive_costs(
            line_item_type="Usage",
            unblended_cost=0.0416,
            net_unblended_cost=0.0354,  # 15% EDP discount
            public_on_demand_cost=0.0416,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == 0.0416
        assert result["ContractedCost"] == 0.0354
        assert result["ListCost"] == 0.0416

    def test_ri_covered_usage(self):
        """RI-covered: BilledCost is 0, EffectiveCost is the RI rate."""
        result = derive_costs(
            line_item_type="DiscountedUsage",
            unblended_cost=0.0,
            net_unblended_cost=0.0,
            public_on_demand_cost=0.0416,
            reservation_effective_cost=0.025,
            sp_effective_cost=None,
        )
        assert result == {
            "BilledCost": 0.0,
            "EffectiveCost": 0.025,
            "ContractedCost": 0.025,
            "ListCost": 0.0416,
        }

    def test_ri_fee(self):
        """The RI fee itself: invoice charge, but EffectiveCost is 0
        because the economic value is amortized into the *CoveredUsage rows."""
        result = derive_costs(
            line_item_type="RIFee",
            unblended_cost=18.25,
            net_unblended_cost=18.25,
            public_on_demand_cost=18.25,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == 18.25
        assert result["EffectiveCost"] == 0.0

    def test_savings_plan_covered_usage(self):
        result = derive_costs(
            line_item_type="SavingsPlanCoveredUsage",
            unblended_cost=0.0,
            net_unblended_cost=0.0,
            public_on_demand_cost=0.0416,
            reservation_effective_cost=None,
            sp_effective_cost=0.028,
        )
        assert result["BilledCost"] == 0.0
        assert result["EffectiveCost"] == 0.028
        assert result["ListCost"] == 0.0416

    def test_savings_plan_recurring_fee(self):
        result = derive_costs(
            line_item_type="SavingsPlanRecurringFee",
            unblended_cost=21.50,
            net_unblended_cost=21.50,
            public_on_demand_cost=21.50,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == 21.50
        assert result["EffectiveCost"] == 0.0

    def test_tax(self):
        result = derive_costs(
            line_item_type="Tax",
            unblended_cost=15.75,
            net_unblended_cost=15.75,
            public_on_demand_cost=15.75,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == 15.75
        assert result["EffectiveCost"] == 15.75

    def test_credit_is_negative(self):
        result = derive_costs(
            line_item_type="Credit",
            unblended_cost=-50.0,
            net_unblended_cost=-50.0,
            public_on_demand_cost=-50.0,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == -50.0

    def test_refund_is_negative(self):
        result = derive_costs(
            line_item_type="Refund",
            unblended_cost=-10.0,
            net_unblended_cost=-10.0,
            public_on_demand_cost=-10.0,
            reservation_effective_cost=None,
            sp_effective_cost=None,
        )
        assert result["BilledCost"] == -10.0

    def test_unknown_line_item_type_raises(self):
        with pytest.raises(ValueError, match="Unhandled LineItemType"):
            derive_costs(
                line_item_type="SomeNewType",
                unblended_cost=1.0,
                net_unblended_cost=1.0,
                public_on_demand_cost=1.0,
                reservation_effective_cost=None,
                sp_effective_cost=None,
            )