"""
ETL pipeline: CUR 2.0 Parquet → FOCUS 1.2 Parquet.

The pipeline is intentionally thin. All mapping logic lives in mappings.py;
this module's job is to apply those functions to a DataFrame and write the
result.

Two patterns are used to apply mappings:

1. Native Polars expressions (`pl.col(...).alias(...)`) for simple renames
   and type-preserving copies. These are fastest because they run in Polars's
   Rust engine without crossing into Python.

2. `pl.struct(...).map_elements(...)` for row-wise functions like
   `derive_costs` that take multiple columns and return multiple columns.
   This is slower (Python-per-row) but readable and easy to test.

For v1 we accept the perf cost of Option 2. v2 can rewrite the conditional
cost logic as pure Polars expressions for ~10–100x speedup on large CURs.
"""

from pathlib import Path

import polars as pl

from focus_bridge import mappings


def _apply_cost_derivation(df: pl.DataFrame) -> pl.DataFrame:
    """Add the four FOCUS cost columns by calling derive_costs row-by-row."""

    def _derive(row: dict) -> dict:
        return mappings.derive_costs(
            line_item_type=row["line_item_line_item_type"],
            unblended_cost=row["line_item_unblended_cost"],
            net_unblended_cost=row["line_item_net_unblended_cost"],
            public_on_demand_cost=row["pricing_public_on_demand_cost"],
            reservation_effective_cost=row["reservation_effective_cost"],
            sp_effective_cost=row["savings_plan_savings_plan_effective_cost"],
        )

    # Schema for the struct returned by _derive. Polars needs this hint
    # because map_elements can't infer struct fields without it.
    cost_schema = pl.Struct(
        {
            "BilledCost": pl.Float64,
            "EffectiveCost": pl.Float64,
            "ContractedCost": pl.Float64,
            "ListCost": pl.Float64,
        }
    )

    cost_cols = [
        "line_item_line_item_type",
        "line_item_unblended_cost",
        "line_item_net_unblended_cost",
        "pricing_public_on_demand_cost",
        "reservation_effective_cost",
        "savings_plan_savings_plan_effective_cost",
    ]

    df = df.with_columns(
        pl.struct(cost_cols)
        .map_elements(_derive, return_dtype=cost_schema)
        .alias("_costs")
    ).unnest("_costs")

    return df


def _apply_scalar_mappings(df: pl.DataFrame) -> pl.DataFrame:
    """Apply mappings that operate one value at a time (ChargeCategory, etc.)."""
    return df.with_columns(
        pl.col("line_item_line_item_type")
        .map_elements(mappings.map_charge_category, return_dtype=pl.String)
        .alias("ChargeCategory"),
        pl.col("line_item_line_item_type")
        .map_elements(mappings.map_charge_class, return_dtype=pl.String)
        .alias("ChargeClass"),
        pl.col("line_item_currency_code")
        .map_elements(mappings.map_billing_currency, return_dtype=pl.String)
        .alias("BillingCurrency"),
    )


def convert_cur_to_focus(input_path: Path, output_path: Path) -> None:
    """
    Convert a CUR 2.0 Parquet file to a FOCUS 1.2 Parquet file.

    Args:
        input_path: Path to the CUR 2.0 Parquet file.
        output_path: Path where the FOCUS Parquet file will be written.
                     Parent directories are created if missing.
    """
    df = pl.read_parquet(input_path)

    # Step 1: Direct-copy mappings (just renames).
    direct_copies = df.select(
        pl.col("bill_billing_period_start_date").alias("BillingPeriodStart"),
        pl.col("bill_billing_period_end_date").alias("BillingPeriodEnd"),
        pl.col("line_item_usage_start_date").alias("ChargePeriodStart"),
        pl.col("line_item_usage_end_date").alias("ChargePeriodEnd"),
        pl.col("bill_payer_account_id").alias("BillingAccountId"),
        pl.col("line_item_usage_account_id").alias("SubAccountId"),
        pl.col("line_item_resource_id").alias("ResourceId"),
        pl.col("product_region_code").alias("RegionId"),
        pl.col("product_product_name").alias("ServiceName"),
        pl.col("line_item_line_item_description").alias("ChargeDescription"),
        pl.col("pricing_unit").alias("PricingUnit"),
        pl.col("line_item_usage_amount").alias("ConsumedQuantity"),
        # Keep the source LineItemType column around for the next two steps;
        # we'll drop it before writing.
        pl.col("line_item_line_item_type"),
        pl.col("line_item_unblended_cost"),
        pl.col("line_item_net_unblended_cost"),
        pl.col("pricing_public_on_demand_cost"),
        pl.col("reservation_effective_cost"),
        pl.col("savings_plan_savings_plan_effective_cost"),
        pl.col("line_item_currency_code"),
    )

    # Step 2: Apply scalar mappings (ChargeCategory, ChargeClass, BillingCurrency).
    with_scalars = _apply_scalar_mappings(direct_copies)

    # Step 3: Apply cost derivation (the four cost columns at once).
    with_costs = _apply_cost_derivation(with_scalars)

    # Step 4: Drop the intermediate CUR columns; keep only FOCUS columns.
    focus_columns = [
        "BillingPeriodStart",
        "BillingPeriodEnd",
        "ChargePeriodStart",
        "ChargePeriodEnd",
        "BillingAccountId",
        "SubAccountId",
        "ResourceId",
        "RegionId",
        "ServiceName",
        "ChargeDescription",
        "PricingUnit",
        "ConsumedQuantity",
        "BillingCurrency",
        "ChargeCategory",
        "ChargeClass",
        "BilledCost",
        "EffectiveCost",
        "ContractedCost",
        "ListCost",
    ]
    focus_df = with_costs.select(focus_columns)

    # Step 5: Write to Parquet.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    focus_df.write_parquet(output_path)