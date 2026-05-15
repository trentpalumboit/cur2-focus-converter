"""
Generate a synthetic AWS CUR 2.0 Parquet file for testing the converter.

This produces a small (~11 rows) file covering the main row archetypes the
mapping logic needs to handle:

- On-demand EC2 usage
- RI-covered usage (DiscountedUsage) + the matching RIFee
- SP-covered usage (SavingsPlanCoveredUsage) + the matching SavingsPlanRecurringFee
- S3 storage
- CloudFront data transfer
- AWS Marketplace third-party software charge
- Tax line
- Credit
- Refund

Run from project root:
    python scripts/generate_sample_cur.py

Writes to samples/inputs/cur2_sample.parquet.

NOTE: Real AWS CUR 2.0 nests some fields (e.g. `product` is a struct with
sub-fields like product.region_code). For readability this synthesizer
flattens those into snake_case columns (product_region_code). The mapping
logic in this project assumes the flat schema. Document this in the README
as a known simplification.
"""

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

BILLING_PERIOD_START = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
BILLING_PERIOD_END = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
PAYER_ACCOUNT_ID = "111122223333"
USAGE_ACCOUNT_ID = "444455556666"


def make_row(**overrides) -> dict:
    """Build a CUR row with sensible on-demand-EC2 defaults; override as needed."""
    base = {
        # Bill-level fields (same across every row in one export)
        "bill_billing_period_start_date": BILLING_PERIOD_START,
        "bill_billing_period_end_date": BILLING_PERIOD_END,
        "bill_payer_account_id": PAYER_ACCOUNT_ID,
        "bill_bill_type": "Anniversary",
        # Line-item fields
        "line_item_usage_account_id": USAGE_ACCOUNT_ID,
        "line_item_line_item_type": "Usage",
        "line_item_usage_start_date": datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        "line_item_usage_end_date": datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc),
        "line_item_product_code": "AmazonEC2",
        "line_item_usage_type": "BoxUsage:t3.medium",
        "line_item_operation": "RunInstances",
        "line_item_currency_code": "USD",
        "line_item_resource_id": "i-0abc123def456789",
        "line_item_usage_amount": 1.0,
        "line_item_unblended_cost": 0.0416,
        "line_item_net_unblended_cost": 0.0416,
        "line_item_line_item_description": "$0.0416 per On Demand Linux t3.medium Instance Hour",
        # Product fields (flattened from the struct in real CUR 2.0)
        "product_product_name": "Amazon Elastic Compute Cloud",
        "product_product_family": "Compute Instance",
        "product_region_code": "us-east-1",
        "product_servicecode": "AmazonEC2",
        # Pricing fields
        "pricing_public_on_demand_cost": 0.0416,
        "pricing_unit": "Hrs",
        "pricing_term": "OnDemand",
        # Commitment fields (null for non-RI/SP rows)
        "reservation_effective_cost": None,
        "savings_plan_savings_plan_effective_cost": None,
        # User tags (CUR uses one column per tag)
        "resource_tags_user_environment": "production",
        "resource_tags_user_team": "platform",
    }
    base.update(overrides)
    return base


def build_sample_rows() -> list[dict]:
    """Row archetypes that exercise every meaningful mapping decision."""
    rows = []

    # 1. Plain on-demand EC2 usage (the default from make_row)
    rows.append(make_row())

    # 2. RI-covered usage. unblended_cost is 0 because the RI fee was paid
    #    separately as an RIFee line. reservation_effective_cost holds the
    #    amortized RI rate that FOCUS will surface as EffectiveCost.
    rows.append(make_row(
        line_item_line_item_type="DiscountedUsage",
        line_item_resource_id="i-0def456ghi789012",
        line_item_unblended_cost=0.0,
        line_item_net_unblended_cost=0.0,
        reservation_effective_cost=0.0250,
    ))

    # 3. The RIFee row (the commitment payment itself). This and row 2
    #    together describe the full economics of one RI-covered usage hour.
    rows.append(make_row(
        line_item_line_item_type="RIFee",
        line_item_usage_type="DiscountedUsage:t3.medium",
        line_item_unblended_cost=18.25,
        line_item_net_unblended_cost=18.25,
        pricing_public_on_demand_cost=18.25,
        line_item_line_item_description="Reserved Instance recurring fee",
    ))

    # 4. SP-covered usage. Same pattern as RI: cost is 0 here, sits in
    #    savings_plan_savings_plan_effective_cost for amortization.
    rows.append(make_row(
        line_item_line_item_type="SavingsPlanCoveredUsage",
        line_item_resource_id="i-0ghi789jkl012345",
        line_item_unblended_cost=0.0,
        line_item_net_unblended_cost=0.0,
        savings_plan_savings_plan_effective_cost=0.0280,
    ))

    # 5. The matching SavingsPlanRecurringFee.
    rows.append(make_row(
        line_item_line_item_type="SavingsPlanRecurringFee",
        line_item_product_code="ComputeSavingsPlans",
        line_item_unblended_cost=21.50,
        line_item_net_unblended_cost=21.50,
        pricing_public_on_demand_cost=21.50,
        line_item_line_item_description="Savings Plan recurring fee",
        product_product_name="Compute Savings Plans",
        product_product_family="Savings Plan",
        product_servicecode="ComputeSavingsPlans",
    ))

    # 6. S3 storage.
    rows.append(make_row(
        line_item_product_code="AmazonS3",
        line_item_usage_type="TimedStorage-ByteHrs",
        line_item_operation="StandardStorage",
        line_item_resource_id="arn:aws:s3:::my-data-bucket",
        line_item_usage_amount=1024.0,
        line_item_unblended_cost=23.55,
        line_item_net_unblended_cost=23.55,
        pricing_public_on_demand_cost=23.55,
        pricing_unit="GB-Mo",
        product_product_name="Amazon Simple Storage Service",
        product_product_family="Storage",
        product_servicecode="AmazonS3",
    ))

    # 7. CloudFront data transfer.
    rows.append(make_row(
        line_item_product_code="AmazonCloudFront",
        line_item_usage_type="DataTransfer-Out-Bytes",
        line_item_operation="GET",
        line_item_resource_id="E1ABCDEFGHIJK",
        line_item_usage_amount=500.0,
        line_item_unblended_cost=42.50,
        line_item_net_unblended_cost=42.50,
        pricing_public_on_demand_cost=42.50,
        pricing_unit="GB",
        product_product_name="Amazon CloudFront",
        product_product_family="Data Transfer",
        product_servicecode="AmazonCloudFront",
    ))

    # 8. AWS Marketplace charge (third-party software billed through AWS).
    #    LineItemType=Fee tests that ChargeCategory maps to Purchase.
    rows.append(make_row(
        line_item_line_item_type="Fee",
        line_item_product_code="AWSMarketplace",
        line_item_usage_type="SoftwareUsage:Datadog",
        line_item_operation="Hourly",
        line_item_resource_id="arn:aws:marketplace:us-east-1:123456789012:subscription/abc",
        line_item_usage_amount=1.0,
        line_item_unblended_cost=125.00,
        line_item_net_unblended_cost=125.00,
        pricing_public_on_demand_cost=125.00,
        line_item_line_item_description="Datadog Pro subscription",
        product_product_name="Datadog",
        product_product_family="Software",
        product_servicecode="AWSMarketplace",
    ))

    # 9. Tax line. No resource, no usage amount, just a cost.
    rows.append(make_row(
        line_item_line_item_type="Tax",
        line_item_usage_amount=0.0,
        line_item_unblended_cost=15.75,
        line_item_net_unblended_cost=15.75,
        pricing_public_on_demand_cost=15.75,
        line_item_line_item_description="US sales tax",
        line_item_resource_id="",
    ))

    # 10. Credit (promotional credit). Note the negative cost.
    rows.append(make_row(
        line_item_line_item_type="Credit",
        line_item_usage_amount=0.0,
        line_item_unblended_cost=-50.00,
        line_item_net_unblended_cost=-50.00,
        pricing_public_on_demand_cost=-50.00,
        line_item_line_item_description="AWS promotional credit",
        line_item_resource_id="",
    ))

    # 11. Refund. Maps to FOCUS ChargeCategory=Adjustment, ChargeClass=Correction.
    rows.append(make_row(
        line_item_line_item_type="Refund",
        line_item_usage_amount=0.0,
        line_item_unblended_cost=-10.00,
        line_item_net_unblended_cost=-10.00,
        pricing_public_on_demand_cost=-10.00,
        line_item_line_item_description="Refund for billing correction",
        line_item_resource_id="",
    ))

    return rows


def main() -> None:
    rows = build_sample_rows()
    df = pl.DataFrame(rows)

    output_path = Path("samples/inputs/cur2_sample.parquet")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)

    print(f"Wrote {len(rows)} rows to {output_path}")
    print(f"Shape: {df.shape}")
    print("\nLineItemType breakdown:")
    print(df.group_by("line_item_line_item_type").len().sort("line_item_line_item_type"))


if __name__ == "__main__":
    main()