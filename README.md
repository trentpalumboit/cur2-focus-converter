# focus-bridge

**Convert AWS CUR 2.0 billing exports to FOCUS 1.2 specification.**

[![CI](https://github.com/trentpalumboit/cur2-focus-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/trentpalumboit/cur2-focus-converter/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

`focus-bridge` is an open-source Python tool that ingests AWS Cost and Usage Report (CUR) 2.0 Parquet files and emits [FOCUS 1.2](https://focus.finops.org/focus-specification/v1-2/)-compliant Parquet output. Every mapping decision is documented with a citation to the FOCUS spec, and the pipeline validates its own output before writing.

## Why this exists when AWS already publishes FOCUS-native exports

AWS launched native FOCUS 1.2 data exports in November 2025. So the first reasonable question is: why build a converter at all?

Three answers:

1. **Historical backfill.** AWS's FOCUS export only works going forward. Organizations with years of CUR 2.0 data sitting in S3 still need to convert it to FOCUS for retrospective analysis, multi-year unit economics, or trend comparison against current data.

2. **Auditable mapping logic.** AWS's native FOCUS export is a black box: it produces FOCUS rows but doesn't expose which CUR fields fed which FOCUS columns or how. For organizations with regulatory or contractual audit requirements, a code-reviewable mapping is a meaningful improvement. Every decision in this converter is documented in [`docs/mappings/decisions.md`](docs/mappings/decisions.md) with spec citations.

3. **Multi-cloud parity.** When the Azure path lands (v2 roadmap), running a single converter against both AWS and Azure billing data and comparing the output against AWS's own FOCUS export becomes a built-in conformance check on the entire pipeline.

## Why this is useful even now

The 2026 [State of FinOps](https://data.finops.org/) survey of 1,192 practitioners managing $83B+ in cloud spend found that data normalization remains a persistent friction point as FinOps scope expands across SaaS, AI, and on-premises spend. Practitioners report stitching billing data together manually, which takes time and introduces risk.

Based on first-hand experience hand-mapping CUR fields to internal cost models, plus the survey's qualitative findings on normalization effort, an automated FOCUS converter eliminates an estimated **30–40 engineering hours per cloud per quarter** that teams currently spend reconciling vendor-specific billing taxonomies. At a $150K loaded engineer rate, that's roughly **$20K/cloud/year in displaced toil** — not a savings on the bill, but a velocity gain on the FinOps team itself.

## Quickstart

```bash
git clone https://github.com/trentpalumboit/cur2-focus-converter.git
cd cur2-focus-converter
uv sync
uv run focus-bridge -i samples/inputs/cur2_sample.parquet -o /tmp/focus.parquet
```

That converts the included sample CUR file and writes a validated FOCUS 1.2 Parquet to `/tmp/focus.parquet`.

To convert your own CUR data, export it from AWS Billing Console → Data Exports → "Standard data export" with table type CUR 2.0 and compression Parquet. Point `-i` at the resulting file (or use `aws s3 cp` to pull it down first).

## What's implemented

| FOCUS 1.2 Column | Status | Notes |
|---|---|---|
| `BillingPeriodStart` | ✅ Full | ISO 8601 UTC |
| `BillingPeriodEnd` | ✅ Full | ISO 8601 UTC, exclusive bound |
| `BillingAccountId` | ✅ Full | AWS payer account ID |
| `SubAccountId` | ✅ Full | AWS linked account ID |
| `BillingCurrency` | ✅ Full | ISO 4217, normalized to uppercase |
| `ChargePeriodStart` | ✅ Full | |
| `ChargePeriodEnd` | ✅ Full | |
| `ChargeCategory` | ✅ Full | Collapses 11 CUR LineItemTypes → 5 FOCUS values |
| `ChargeClass` | ✅ Full | `Correction` for Refunds and BundledDiscounts |
| `ChargeDescription` | ✅ Full | |
| `BilledCost` | ✅ Full | Conditional on LineItemType — see decisions doc |
| `EffectiveCost` | ✅ Full | Amortized including commitments |
| `ContractedCost` | ✅ Full | |
| `ListCost` | ✅ Full | |
| `ServiceName` | ✅ Full | From CUR `product_product_name` |
| `RegionId` | ✅ Full | From CUR `product_region_code` |
| `ResourceId` | ✅ Full | |
| `PricingUnit` | ✅ Full | |
| `ConsumedQuantity` | ✅ Full | |
| `ServiceCategory` | 🟡 Roadmap | Requires manual AWS service → FOCUS category lookup |
| `ServiceSubcategory` | 🟡 Roadmap | Same as above |
| `PricingCategory` | 🟡 Roadmap | |
| `Tags` | 🟡 Roadmap | CUR's column-per-tag pattern → FOCUS JSON column |
| `CommitmentDiscountId` | 🟡 Roadmap | Requires joining with reservation/SP metadata |
| `CommitmentDiscountCategory` | 🟡 Roadmap | |
| `CommitmentDiscountType` | 🟡 Roadmap | |
| `SkuId` / `SkuPriceId` | 🟡 Roadmap | From CUR `pricing_rate_code` |

## Mapping decisions

Every non-trivial mapping decision is documented in [`docs/mappings/decisions.md`](docs/mappings/decisions.md). The most important section covers the four FOCUS cost columns (`BilledCost`, `EffectiveCost`, `ContractedCost`, `ListCost`) and how they're derived from CUR's overlapping cost fields based on `LineItemType`. If you read one doc in this repo, read that one.

## Known limitations

- **Marketplace charges** are mapped as `ChargeCategory=Purchase` but the converter does not yet distinguish reseller relationships per FOCUS 1.2 § 2.34 (`ServiceProviderName`) vs § 2.24 (`HostProviderName`).
- **Split-cost allocations** (Kubernetes pods, shared databases) are not yet supported. This is a FOCUS 1.3 feature on the roadmap.
- **Capacity reservation tracking** (FOCUS 1.2's new capacity-reservation columns) is not yet implemented.
- The synthetic CUR sample in `samples/inputs/` flattens product fields into snake_case columns (e.g., `product_region_code`) rather than the nested struct format used by real CUR 2.0 exports. The pipeline assumes the flat schema; real CUR data may require one additional unnesting step.

## Roadmap

- **v0.2**: Azure billing export → FOCUS 1.2 (additional provider)
- **v0.3**: Tags collapse (CUR column-per-tag → FOCUS JSON `Tags` column)
- **v0.4**: ServiceCategory/Subcategory via AWS service code lookup
- **v0.5**: Commitment discount metadata columns
- **v1.0**: FOCUS 1.3 columns (shared cost allocation, contract dataset, data freshness)

## Development

Requires Python 3.14+ and [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync                                    # Install dependencies
uv run pytest                              # Run tests (60 tests, ~99% coverage)
uv run pytest --cov=focus_bridge           # Run with coverage report
uv run ruff check .                        # Lint
uv run mypy src/                           # Type-check
```

Project layout:

```
src/focus_bridge/
  mappings.py      # Pure functions: CUR value → FOCUS value
  pipeline.py      # Orchestrates mappings against a DataFrame
  validation.py    # FOCUS 1.2 spec rule enforcement
  cli.py           # Typer-based command-line interface
tests/             # 60 tests across mappings, pipeline, validation
samples/inputs/    # Committed synthetic CUR 2.0 sample
samples/outputs/   # Reference FOCUS 1.2 output
docs/mappings/     # Mapping decisions with spec citations
```

## Acknowledgments

This project is built on the [FinOps Open Cost and Usage Specification](https://focus.finops.org/), maintained by the [FinOps Foundation](https://www.finops.org/). FOCUS is what makes multi-cloud cost normalization tractable as a discipline; this tool just translates one specific dialect (AWS CUR 2.0) into that common language.

## License

Apache 2.0. See [LICENSE](LICENSE).
