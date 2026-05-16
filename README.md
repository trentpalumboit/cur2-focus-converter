# focus-bridge

**Convert AWS CUR 2.0 billing exports to FOCUS 1.2 specification.**

[![CI](https://github.com/trentpalumboit/cur2-focus-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/trentpalumboit/cur2-focus-converter/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

`focus-bridge` is an open-source Python tool that ingests AWS Cost and Usage Report (CUR) 2.0 Parquet files and emits Parquet output aligned to the [FOCUS 1.2](https://focus.finops.org/focus-specification/v1-2/) specification. Every mapping decision is documented with a citation to the spec, and the pipeline runs a validation pass on its output before writing.

> **This is a learning artifact, not a certified FOCUS-conformant tool.** It implements the parts of the FOCUS 1.2 spec that are most useful for understanding the spec's cost-attribution model. It does **not** produce fully FOCUS 1.2 conformant output. See [Conformance gaps](#focus-12-column-coverage) below for the explicit list of what's missing.

## Why this exists when AWS already publishes FOCUS-native exports

AWS launched native FOCUS 1.2 data exports in November 2025. So the first reasonable question is: why build a converter at all?

Three answers:

1. **Historical backfill.** AWS's FOCUS export only works going forward. Organizations with years of CUR 2.0 data sitting in S3 still need to convert it for retrospective analysis, multi-year unit economics, or trend comparison.

2. **Auditable mapping logic.** AWS's native FOCUS export is a black box: it produces FOCUS rows but doesn't expose which CUR fields fed which FOCUS columns or how. Every decision in this converter is documented in [`docs/mappings/decisions.md`](docs/mappings/decisions.md) with spec citations. For organizations with audit requirements, a code-reviewable mapping is meaningful.

3. **The skill, not the tool.** Writing the converter forced reading the FOCUS 1.2 spec at the column-and-datatype level. The mapping decisions document is where the real work lives.

## Why this is useful even now

The 2026 [State of FinOps](https://data.finops.org/) survey of 1,192 practitioners managing $83B+ in cloud spend found data normalization is a persistent friction point as FinOps scope expands across SaaS, AI, and on-premises spend. Practitioners report stitching billing data together manually, which takes time and introduces risk.

Based on first-hand experience hand-mapping CUR fields to internal cost models, plus the survey's qualitative findings, an automated FOCUS converter eliminates an estimated **30–40 engineering hours per cloud per quarter** that teams currently spend reconciling vendor-specific billing taxonomies. At a $150K loaded engineer rate, that's roughly **$20K/cloud/year in displaced toil** — not a savings on the bill, a velocity gain on the FinOps team itself.

## Validation status

| Test | Status |
|---|---|
| 60 pytest unit + integration tests | ✅ All passing |
| Internal validation layer (subset of FOCUS rules) | ✅ Passes on all output |
| Synthetic CUR 2.0 sample (11 row archetypes) | ✅ Converts cleanly |
| Real AWS CUR 2.0 export (May 2026, 809 rows, 11 services) | ✅ Converts cleanly |
| Official FinOps Foundation FOCUS conformance check | ⏳ Not yet run |
| Diff against AWS's native FOCUS 1.2 export | ⏳ Not yet run |

The output passes the built-in validation layer, but the validation layer covers only a subset of FOCUS 1.2 rules (see [Validator coverage gaps](#validator-coverage-gaps) below).

## Quickstart

```bash
git clone https://github.com/trentpalumboit/cur2-focus-converter.git
cd cur2-focus-converter
uv sync
uv run focus-bridge -i samples/inputs/cur2_sample.parquet -o /tmp/focus.parquet
```

That converts the included synthetic CUR sample and writes a Parquet output to `/tmp/focus.parquet`.

To convert your own CUR data: export it from AWS Billing Console → Data Exports → "Standard data export" with table type CUR 2.0 and compression Parquet. Point `-i` at the resulting file.

## FOCUS 1.2 column coverage

### Columns produced

| FOCUS 1.2 Column | Status | Source / Notes |
|---|---|---|
| `BillingPeriodStart` | ✅ Full | From `bill_billing_period_start_date` |
| `BillingPeriodEnd` | ✅ Full | From `bill_billing_period_end_date` |
| `BillingAccountId` | ✅ Full | From `bill_payer_account_id` |
| `SubAccountId` | ✅ Full | From `line_item_usage_account_id` |
| `BillingCurrency` | ✅ Full | From `line_item_currency_code`, normalized uppercase |
| `ChargePeriodStart` | ✅ Full | From `line_item_usage_start_date` |
| `ChargePeriodEnd` | ✅ Full | From `line_item_usage_end_date` |
| `ChargeCategory` | ✅ Full | Conditional on `line_item_line_item_type` |
| `ChargeClass` | ✅ Full | `Correction` for Refunds/BundledDiscounts |
| `ChargeDescription` | ✅ Full | From `line_item_line_item_description` |
| `BilledCost` | ⚠️ Float not Decimal | Conditional on LineItemType — see decisions doc |
| `EffectiveCost` | ⚠️ Float not Decimal | Amortized including commitments |
| `ContractedCost` | ⚠️ Float not Decimal | Post-EDP, pre-commitment |
| `ListCost` | ⚠️ Float not Decimal | On-demand rate |
| `ServiceName` | ✅ Full | From `line_item_product_code` (e.g., `AmazonS3`) |
| `RegionId` | ✅ Full | From `product_region_code` |
| `ResourceId` | ✅ Full | |
| `PricingUnit` | ✅ Full | |
| `ConsumedQuantity` | ✅ Full | From `line_item_usage_amount` |

### Required FOCUS 1.2 columns NOT produced

These are required by the FOCUS 1.2 spec but not currently emitted. A strict FOCUS reader would reject the output for missing these.

| FOCUS 1.2 Column | Why missing | Roadmap |
|---|---|---|
| `ServiceCategory` | Requires AWS service → FOCUS category lookup table | v0.4 |
| `ServiceSubcategory` | Same lookup table needed | v0.4 |
| `PricingCategory` | Requires reasoning across LineItemType + pricing_term | v0.3 |
| `ChargeFrequency` | One-Time / Recurring / Usage-Based — needs derivation logic | v0.3 |
| `CommitmentDiscountCategory` | Spend vs Usage based — needs RI/SP context | v0.5 |
| `CommitmentDiscountType` | RI vs SP vs neither | v0.5 |
| `CommitmentDiscountId` | RI ARN or SP ARN | v0.5 |
| `CommitmentDiscountName` | | v0.5 |
| `CommitmentDiscountStatus` | | v0.5 |
| `Tags` | CUR column-per-tag → FOCUS JSON column collapse | v0.3 |
| `ProviderName` | Should always emit `"AWS"` — trivial | v0.2 |
| `PublisherName` | Marketplace-aware: AWS vs third-party publisher | v0.4 |
| `InvoiceIssuerName` | | v0.2 |
| `SkuId` | From `pricing_rate_code` | v0.4 |
| `SkuPriceId` | From `pricing_rate_id` | v0.4 |
| `AvailabilityZone` | From `line_item_availability_zone` — trivial | v0.2 |
| `ResourceName` | From resource tag `Name` when present | v0.3 |
| `ResourceType` | Requires service code → resource type mapping | v0.4 |
| `ConsumedUnit` | Standardized units (Hours, GB-Month, etc.) — needs normalization | v0.3 |
| `ContractedUnitPrice` | Per-unit equivalent of ContractedCost | v0.3 |
| `ListUnitPrice` | Per-unit equivalent of ListCost | v0.3 |
| `BillingAccountName` | From `bill_payer_account_name` — trivial | v0.2 |
| `SubAccountName` | From `line_item_usage_account_name` — trivial | v0.2 |
| `CapacityReservationId` | FOCUS 1.2 new feature — needs new row generation logic | v1.0 |
| `CapacityReservationStatus` | | v1.0 |
| `InvoiceId` | From `bill_invoice_id` — trivial | v0.2 |

**Several of these are one-line fixes that should land in v0.2.** They're separated from the harder lookup-table work to make the next release scope obvious.

## Datatype gaps

FOCUS 1.2 specifies certain column types that this converter does not strictly match:

- **Cost columns are `Float64`, not `Decimal`.** FOCUS specifies Decimal for currency precision. Float64 is "close enough" for analysis but can introduce floating-point precision errors on large aggregations. Real FinOps tools should use Decimal. Tracked for v0.3.
- **Date columns are emitted as Parquet `Timestamp[μs, UTC]`, not ISO 8601 strings.** FOCUS describes Date/Time values in ISO 8601 format; in practice most consumers accept Parquet timestamps, but a strict string-typed reader would not. Either format is correct semantically.

## Validator coverage gaps

The built-in `src/focus_bridge/validation.py` module checks a subset of FOCUS 1.2 rules:

| Rule | Validator covers it? |
|---|---|
| Required columns present | ✅ (for the 11 columns we produce) |
| Non-null constraints | ✅ (for the columns we produce) |
| `ChargeCategory` enum values | ✅ |
| `ChargeClass` enum values | ✅ |
| Currency is 3-letter uppercase | ✅ |
| `ChargePeriodStart < ChargePeriodEnd` | ✅ |
| `BillingPeriodStart < BillingPeriodEnd` | ✅ |
| All ~50 FOCUS columns present | ❌ |
| Cross-column rules (e.g., `ChargeFrequency` vs `ChargeCategory`) | ❌ (partial) |
| Decimal precision constraints | ❌ |
| Enum constraints for `ServiceCategory`, `PricingCategory`, etc. | ❌ (columns not yet produced) |
| Unit-of-measure consistency | ❌ |

**Passing this converter's internal validator is not equivalent to passing the FinOps Foundation's official FOCUS conformance check.** That check has not yet been run against this converter's output.

## Functional limitations beyond column coverage

- **Marketplace charges** are mapped as `ChargeCategory=Purchase`, but reseller relationships (which AWS account is the service provider vs invoice issuer vs publisher) are not yet distinguished per FOCUS 1.2 § 2.34.
- **Split-cost allocations** (CUR's `split_line_item_*` columns for Kubernetes/shared-database cost splitting) are entirely ignored. This is a FOCUS 1.3 feature on the long-term roadmap.
- **Cost Categories** — CUR's `cost_category` map column is not read. Organizations using AWS Cost Categories for chargeback will lose that grouping.
- **Discounts map** — CUR's `discount` map column (which can hold structured EDP/PPA/RI/SP discount details) is not fully read; only the unblended-vs-net-unblended deltas are.

## Schema and architectural limitations

- **In-memory only.** The pipeline loads the entire input Parquet file into memory. Real production CUR files for large accounts can be multiple GB; this won't scale. Streaming/chunked processing is a v0.3 item.
- **Single-file input only.** Real CUR exports are delivered as Hive-partitioned directory structures (`BILLING_PERIOD=YYYY-MM/<timestamp>/file.parquet`). This tool takes one file at a time; you have to pull the latest manually. Directory-aware loading is a v0.2 item.
- **No S3 input support.** Input must be a local file path. To process from S3, download first with `aws s3 cp`.
- **Cost column logic uses row-by-row Python (`map_elements`), not native Polars expressions.** Easier to read and test; ~10–100× slower than the native-expression alternative on large data. A v0.3 rewrite as `pl.when().then()` expressions is planned.
- **Synthetic test data has 28 columns; real CUR 2.0 has up to 125.** The mapping logic only touches the ~18 columns it cares about, so this works in practice — but the synth doesn't exercise edge cases (struct-typed columns, map columns, sparse columns).

## What real-data testing surfaced

Validation against a real AWS CUR 2.0 export (May 2026) surfaced one schema difference from the synthetic data:

- **`ServiceName` source column.** Synthetic data used `product_product_name`; real CUR 2.0 doesn't have that column. The bare `product` column in real CUR is a struct-typed field, not a string. The corrected source is `line_item_product_code` (e.g., `AmazonS3`, `AmazonEC2`).

Any future real-data testing will document additional discoveries here.

## Roadmap

- **v0.2 (immediate, ~1 week of work):** Add trivial-fix columns (`ProviderName`, `InvoiceIssuerName`, `AvailabilityZone`, `BillingAccountName`, `SubAccountName`, `InvoiceId`). Add directory-aware Hive-partitioned input.
- **v0.3:** Tags collapse, ChargeFrequency derivation, ConsumedUnit normalization, ContractedUnitPrice/ListUnitPrice, Decimal cost columns, native Polars expressions for cost derivation.
- **v0.4:** ServiceCategory/Subcategory via lookup table, SkuId/SkuPriceId, PublisherName for Marketplace.
- **v0.5:** Commitment discount metadata columns.
- **v0.6 / v1.0:** FOCUS 1.3 columns (shared cost allocation, contract dataset, capacity reservations, data freshness).
- **Multi-cloud:** Azure billing export → FOCUS 1.2.

## Development

Requires Python 3.14+ and [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync                                    # Install dependencies
uv run pytest                              # Run tests (60 tests, ~99% coverage on source)
uv run pytest --cov=focus_bridge           # Run with coverage report
uv run ruff check .                        # Lint
uv run mypy src/                           # Type-check
```

Project layout:

```
src/focus_bridge/
  mappings.py      # Pure functions: CUR value → FOCUS value
  pipeline.py      # Orchestrates mappings against a DataFrame
  validation.py    # Subset of FOCUS 1.2 spec rule enforcement
  cli.py           # Typer-based command-line interface
tests/             # 60 tests across mappings, pipeline, validation
samples/inputs/    # Synthetic CUR 2.0 sample (real CUR files gitignored)
samples/outputs/   # Reference FOCUS output
docs/mappings/     # Mapping decisions with spec citations
```

## Acknowledgments

Built on the [FinOps Open Cost and Usage Specification](https://focus.finops.org/), maintained by the [FinOps Foundation](https://www.finops.org/). FOCUS is what makes multi-cloud cost normalization tractable as a discipline; this tool translates one specific dialect (AWS CUR 2.0) into that common language, partially, as a learning exercise.

## License

Apache 2.0. See [LICENSE](LICENSE).
