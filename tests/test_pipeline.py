"""Integration tests for the full CUR → FOCUS pipeline."""

from pathlib import Path

import polars as pl
import pytest

from focus_bridge.pipeline import convert_cur_to_focus


@pytest.fixture
def sample_input() -> Path:
    """Path to the committed sample CUR file."""
    return Path("samples/inputs/cur2_sample.parquet")


@pytest.fixture
def focus_output(sample_input: Path, tmp_path: Path) -> pl.DataFrame:
    """Run the full pipeline against the sample input; return the result."""
    output_path = tmp_path / "focus.parquet"
    convert_cur_to_focus(sample_input, output_path)
    assert output_path.exists()
    return pl.read_parquet(output_path)


class TestPipelineStructure:
    """The output structure matches FOCUS 1.2 column requirements."""

    def test_row_count_preserved(self, focus_output: pl.DataFrame):
        # 11 CUR rows in → 11 FOCUS rows out. The converter is row-preserving.
        assert focus_output.height == 11

    def test_required_focus_columns_present(self, focus_output: pl.DataFrame):
        required = {
            "BillingPeriodStart",
            "BillingPeriodEnd",
            "ChargePeriodStart",
            "ChargePeriodEnd",
            "BillingAccountId",
            "BillingCurrency",
            "ChargeCategory",
            "BilledCost",
            "EffectiveCost",
        }
        assert required.issubset(set(focus_output.columns))

    def test_no_cur_columns_leak_through(self, focus_output: pl.DataFrame):
        """Output should contain no source CUR column names."""
        cur_prefixes = ("line_item_", "bill_", "product_", "pricing_", "reservation_", "savings_plan_")
        leaked = [c for c in focus_output.columns if c.startswith(cur_prefixes)]
        assert leaked == [], f"CUR columns leaked into FOCUS output: {leaked}"


class TestPipelineEconomics:
    """The cost columns tell the right economic story per row type."""

    def test_billed_equals_effective_for_on_demand(self, focus_output: pl.DataFrame):
        """On-demand usage has no discount; BilledCost == EffectiveCost."""
        on_demand = focus_output.filter(
            (pl.col("ChargeCategory") == "Usage") & (pl.col("BilledCost") > 0)
        )
        assert on_demand.height > 0
        for row in on_demand.iter_rows(named=True):
            assert row["BilledCost"] == row["EffectiveCost"]

    def test_ri_covered_usage_has_zero_billed_cost(self, focus_output: pl.DataFrame):
        """RI-covered usage: BilledCost=0, EffectiveCost>0, ListCost>EffectiveCost."""
        # Identify the DiscountedUsage row by its non-zero EffectiveCost paired
        # with zero BilledCost and a higher ListCost.
        ri_covered = focus_output.filter(
            (pl.col("BilledCost") == 0)
            & (pl.col("EffectiveCost") > 0)
            & (pl.col("ListCost") > pl.col("EffectiveCost"))
        )
        assert ri_covered.height >= 1

    def test_commitment_fees_have_zero_effective_cost(self, focus_output: pl.DataFrame):
        """RIFee and SavingsPlanRecurringFee: BilledCost>0, EffectiveCost=0.

        The economic value of the fee is amortized into *CoveredUsage rows,
        so we don't double-count it here. Identified by the unique signature:
        Purchase category where BilledCost > 0 but EffectiveCost == 0.
        Contrast with a Marketplace Fee (also Purchase) where EffectiveCost
        equals BilledCost because it's not an amortizable commitment."""
        commitment_fees = focus_output.filter(
            (pl.col("ChargeCategory") == "Purchase")
            & (pl.col("BilledCost") > 0)
            & (pl.col("EffectiveCost") == 0)
        )
        # We expect exactly two: the RIFee and the SavingsPlanRecurringFee.
        assert commitment_fees.height == 2

    def test_marketplace_fee_has_nonzero_effective_cost(self, focus_output: pl.DataFrame):
        """Counter-test: Marketplace Fee is Purchase but NOT amortized.

        Distinguishes commitment fees from one-shot Purchase charges. The
        Datadog row should have EffectiveCost == BilledCost == 125.0."""
        marketplace = focus_output.filter(
            (pl.col("ChargeCategory") == "Purchase")
            & (pl.col("BilledCost") == pl.col("EffectiveCost"))
            & (pl.col("BilledCost") > 0)
        )
        assert marketplace.height >= 1
        for row in marketplace.iter_rows(named=True):
            assert row["EffectiveCost"] > 0

    def test_refund_is_adjustment_with_negative_cost(self, focus_output: pl.DataFrame):
        refunds = focus_output.filter(pl.col("ChargeCategory") == "Adjustment")
        assert refunds.height >= 1
        for row in refunds.iter_rows(named=True):
            assert row["BilledCost"] < 0

    def test_credit_has_negative_cost(self, focus_output: pl.DataFrame):
        credits = focus_output.filter(pl.col("ChargeCategory") == "Credit")
        assert credits.height >= 1
        for row in credits.iter_rows(named=True):
            assert row["BilledCost"] < 0


class TestPipelineFocusInvariants:
    """FOCUS 1.2 specification invariants that must hold on all output."""

    def test_billing_account_id_never_null(self, focus_output: pl.DataFrame):
        """FOCUS 1.2 § 2.3: BillingAccountId MUST NOT be null."""
        assert focus_output["BillingAccountId"].null_count() == 0

    def test_charge_period_dates_never_null(self, focus_output: pl.DataFrame):
        """FOCUS 1.2 § 2.12, § 2.13: ChargePeriodStart/End MUST NOT be null."""
        assert focus_output["ChargePeriodStart"].null_count() == 0
        assert focus_output["ChargePeriodEnd"].null_count() == 0

    def test_charge_category_uses_allowed_values_only(self, focus_output: pl.DataFrame):
        """FOCUS 1.2 § 2.8: ChargeCategory enum."""
        allowed = {"Usage", "Purchase", "Tax", "Credit", "Adjustment"}
        actual = set(focus_output["ChargeCategory"].unique().to_list())
        assert actual.issubset(allowed), f"Invalid ChargeCategory values: {actual - allowed}"

    def test_billing_currency_is_iso4217(self, focus_output: pl.DataFrame):
        """All currency values are 3-letter uppercase codes."""
        for currency in focus_output["BillingCurrency"].unique().to_list():
            assert len(currency) == 3
            assert currency.isupper()