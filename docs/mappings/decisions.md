# CUR 2.0 → FOCUS 1.2 Mapping Decisions

This document explains every non-obvious mapping decision made by `focus-bridge`. Mechanical 1:1 mappings (e.g., `bill_payer_account_id` → `BillingAccountId`) are documented as docstrings on the relevant functions in `src/focus_bridge/mappings.py`. This document covers the decisions that required judgment.

Spec references throughout cite the FOCUS 1.2 specification: https://focus.finops.org/focus-specification/v1-2/

---

## 1. The four cost columns: `BilledCost`, `EffectiveCost`, `ContractedCost`, `ListCost`

This is the single most important mapping decision in the converter. It's also the section a FinOps reviewer should read most carefully.

### What FOCUS says

FOCUS 1.2 defines four cost columns with distinct semantic meaning:

- **`BilledCost`** (§ 2.2) — The cost that appears on the provider's invoice for this charge. This is what was actually billed, regardless of any commitments or amortization.
- **`EffectiveCost`** (§ 2.20) — The amortized cost including commitment discounts. Commitment fees (RIs, Savings Plans) are spread across the usage rows they cover so that EffectiveCost sums to true total cost without double-counting the commitment fee.
- **`ContractedCost`** (§ 2.13) — The cost at the negotiated rate, after volume/EDP/PPA discounts but before commitment amortization.
- **`ListCost`** (§ 2.27) — The cost at the public on-demand rate, before any discount.

### What CUR gives us

CUR 2.0 has several cost-shaped fields that overlap and shift meaning depending on `line_item_line_item_type`:

- `line_item_unblended_cost` — The raw cost AWS charges before EDP discounts.
- `line_item_net_unblended_cost` — Same, but after EDP/PPA discounts.
- `pricing_public_on_demand_cost` — What the same usage would cost at on-demand rates.
- `reservation_effective_cost` — For RI-covered usage, the amortized RI rate.
- `savings_plan_savings_plan_effective_cost` — For SP-covered usage, the amortized SP rate.

### The mapping

The mapping is conditional on `line_item_line_item_type`:

| CUR LineItemType | BilledCost | EffectiveCost | ContractedCost | ListCost |
|---|---|---|---|---|
| `Usage` (on-demand) | unblended | unblended | net_unblended | public_on_demand |
| `DiscountedUsage` (RI-covered) | **0** | reservation_effective | reservation_effective | public_on_demand |
| `SavingsPlanCoveredUsage` | **0** | sp_effective | sp_effective | public_on_demand |
| `RIFee` | unblended | **0** | unblended | unblended |
| `SavingsPlanRecurringFee` | unblended | **0** | unblended | unblended |
| `Tax` / `Fee` | unblended | unblended | unblended | public_on_demand or unblended |
| `Credit` / `Refund` / `BundledDiscount` | unblended | unblended | unblended | unblended |

### Why `BilledCost = 0` for `DiscountedUsage` and `SavingsPlanCoveredUsage`

AWS doesn't double-bill commitment-covered usage. The commitment fee is invoiced separately as a `RIFee` or `SavingsPlanRecurringFee` line. The corresponding usage rows have `unblended_cost = 0` because the invoice already captured that cost via the commitment fee row. We pass that 0 through to `BilledCost` to preserve invoice fidelity.

### Why `EffectiveCost = 0` for `RIFee` and `SavingsPlanRecurringFee`

This is the inverse of the above. If we recorded the commitment fee's full cost in *both* `BilledCost` *and* `EffectiveCost`, then summed `EffectiveCost` across all rows for a true amortized total, we'd double-count. FOCUS resolves this by attributing the economic value of the commitment to the `DiscountedUsage` / `SavingsPlanCoveredUsage` rows it covers (via `reservation_effective_cost` / `savings_plan_savings_plan_effective_cost`), and zeroing out `EffectiveCost` on the commitment fee row itself.

This produces the desired invariant: **summing `EffectiveCost` across all rows for a billing period yields the true amortized cost of cloud spend in that period.**

### Worked example: one RI-covered EC2 hour

Imagine one hour of `t3.medium` in `us-east-1`, covered by a 1-year No Upfront RI. AWS emits two rows for that hour:

**Row A**: `LineItemType = DiscountedUsage`
- `unblended_cost = 0.0`
- `public_on_demand_cost = 0.0416` (on-demand rate)
- `reservation_effective_cost = 0.025` (amortized RI rate)

**Row B**: `LineItemType = RIFee` (recurring monthly fee for the RI)
- `unblended_cost = 18.25` (one month's RI fee)
- `public_on_demand_cost = 18.25`

After conversion, FOCUS gives:

| Row | ChargeCategory | BilledCost | EffectiveCost | ContractedCost | ListCost |
|---|---|---|---|---|---|
| A (DiscountedUsage) | Usage | 0.0 | 0.025 | 0.025 | 0.0416 |
| B (RIFee) | Purchase | 18.25 | 0.0 | 18.25 | 18.25 |

Reading this:
- **`BilledCost`**: Row B is what hit the invoice. Row A was "free" at the line level. Total invoice impact: $18.25 (paid up front for the month, amortizes across all RI-covered hours).
- **`EffectiveCost`**: Sums to $0.025 for this hour. That's the true economic cost of one RI-covered hour. Across all 730 hours in a month it sums to $18.25 = the RI fee. No double-counting.
- **`ListCost`**: The on-demand equivalent, $0.0416/hour. The gap between `ListCost` and `EffectiveCost` ($0.0166/hour) is the RI savings on this hour.

This is the entire point of FOCUS's four-column cost model: it lets you ask "what was the bill?" and "what was the true cost?" and "what would on-demand have cost us?" with three separate, non-conflicting columns.

---

## 2. `ChargeCategory`: collapsing 11 CUR types into 5 FOCUS values

FOCUS 1.2 § 2.8 defines `ChargeCategory` as an enum of five values:

- `Usage` — Charges for consuming a service.
- `Purchase` — One-time or recurring fees for the right to use something (commitments, marketplace subscriptions).
- `Tax` — Government-imposed levies.
- `Credit` — Promotional credits or refunds initiated by the provider.
- `Adjustment` — Corrections to previously billed amounts.

CUR has ~11 distinct `LineItemType` values. The collapse:

| CUR LineItemType | FOCUS ChargeCategory | Reasoning |
|---|---|---|
| `Usage` | `Usage` | Direct mapping. |
| `DiscountedUsage` | `Usage` | Still usage from an economic standpoint; the discount is reflected in the cost columns, not the category. |
| `SavingsPlanCoveredUsage` | `Usage` | Same reasoning. |
| `RIFee` | `Purchase` | The commitment is a purchase of future capacity at a discount. |
| `SavingsPlanRecurringFee` | `Purchase` | Same reasoning. |
| `Fee` | `Purchase` | Marketplace subscriptions, AWS Support fees. The customer is purchasing a right. |
| `Tax` | `Tax` | Direct mapping. |
| `Credit` | `Credit` | Promotional credits, billing concessions. |
| `Refund` | `Adjustment` | A refund corrects a prior charge; FOCUS treats this as an adjustment, with `ChargeClass = "Correction"`. |
| `BundledDiscount` | `Adjustment` | Same reasoning — it adjusts a prior cost. |
| `SavingsPlanNegation` | `Adjustment` | Reverses an over-application of SP coverage. |

### Why `Refund` ≠ `Credit`

This was a non-obvious decision. Both refunds and credits result in negative cost. But FOCUS distinguishes them semantically: `Credit` is a provider-initiated reduction (promotional credit, goodwill concession) and `Adjustment` is a correction to a previously billed amount. AWS `Refund` rows are the latter — they reverse a specific prior charge. Hence `Adjustment` + `ChargeClass = "Correction"`.

### Why Marketplace `Fee` rows aren't commitments

The `Fee` LineItemType covers a heterogeneous set: AWS Support fees, Marketplace third-party software subscriptions, etc. These are mapped to `ChargeCategory = Purchase`, but unlike `RIFee` and `SavingsPlanRecurringFee` they do **not** get the "amortized into covered usage" treatment — there's no usage row to amortize *into*. A Marketplace `Fee` row therefore has `EffectiveCost = BilledCost`, not `EffectiveCost = 0`.

The test suite includes an explicit counter-test (`test_marketplace_fee_has_nonzero_effective_cost`) that codifies this distinction. A `Purchase` row with `EffectiveCost = 0` must be a commitment fee; a `Purchase` row with `EffectiveCost = BilledCost` must be a one-shot purchase.

---

## 3. `ChargeClass`: when is a row a correction?

FOCUS 1.2 § 2.9 defines `ChargeClass` as a String with one allowed non-null value: `"Correction"`. Null means the row is a normal charge; `"Correction"` means it adjusts a previously billed amount.

The mapping:

- `Refund` → `"Correction"`
- `BundledDiscount` → `"Correction"`
- All other LineItemTypes → null

`SavingsPlanNegation` is an edge case worth flagging — it does correct a prior over-allocation, but the FOCUS spec is ambiguous about whether negation events count as `Correction`. The current converter leaves these as null; this should be revisited against more real-world data.

---

## 4. `BillingCurrency`: normalization

The CUR `line_item_currency_code` field is reliable but inconsistent in case (`USD`, `usd`, etc.) across some historical exports. The converter normalizes to uppercase. ISO 4217 codes are always uppercase by spec, so this is safe.

---

## 5. Date/time columns: timezone handling

FOCUS 1.2 § 3 requires all date/time values to be in UTC and formatted as ISO 8601 (`YYYY-MM-DDTHH:mm:ssZ`).

AWS CUR 2.0 stores datetimes as UTC timestamps in Parquet, so Polars reads them as timezone-aware `datetime` objects in UTC. The converter:

1. If the value is a string with offset notation, parses it.
2. If the value is timezone-naive (shouldn't happen with real CUR data, but defensive), assumes UTC.
3. If the value has a non-UTC offset (also shouldn't happen), normalizes to UTC.
4. Formats as `%Y-%m-%dT%H:%M:%SZ`.

The pipeline currently passes these through as Polars `Datetime[μs, UTC]` columns to Parquet rather than as ISO 8601 strings. This is more storage-efficient and FOCUS-readers can format on output. If a strict string-typed output is required, change `pipeline.py` to apply `map_iso8601_utc` via `map_elements`.

---

## 6. `ChargePeriodEnd` exclusivity

FOCUS 1.2 § 2.12 specifies that `ChargePeriodEnd` is **exclusive** — a row with `ChargePeriodStart = 2024-01-01T00:00:00Z` and `ChargePeriodEnd = 2024-01-02T00:00:00Z` covers January 1 only, not January 2.

AWS CUR 2.0 uses the same exclusivity convention for `line_item_usage_end_date`, so this is a direct copy. No transformation needed, but worth documenting because vendors don't always agree on this convention and the next provider (Azure) does this differently in some export formats.

---

## 7. Service categorization (roadmap)

FOCUS 1.2 § 2.34 / § 2.35 define `ServiceCategory` and `ServiceSubcategory` as required-with-allowed-values enums. The allowed values are FOCUS-defined and don't map 1:1 to AWS service codes.

The converter currently does **not** populate these columns. Doing so requires a lookup table from AWS service codes (`product_servicecode`) to FOCUS categories. There are roughly 200 AWS services, but the top 30 by typical spend cover ~95% of customers' bills.

The v0.4 roadmap will introduce this lookup as a YAML file at `src/focus_bridge/data/aws_service_categories.yaml`, hand-curated against the FOCUS-published guidance.

---

## 8. Open questions and known gaps

Items currently left unimplemented or partially implemented, with reasoning:

- **`Tags`** — CUR represents user tags as one column per tag (`resource_tags_user_environment`, `resource_tags_user_team`, etc.). FOCUS uses a single JSON-typed `Tags` column. The collapse is mechanical but not yet implemented because the synthetic sample only has two tag columns; collapsing them well requires testing against real-world CUR exports with hundreds of dynamic tag columns.
- **Marketplace reseller relationships** — FOCUS 1.2 introduced `ServiceProviderName` and `HostProviderName` to disambiguate situations like "AWS bills me for Datadog." The converter currently treats AWS as both, which is incorrect for Marketplace rows. Fix requires inspecting `bill_invoicing_entity` and possibly the resource ARN.
- **Split-cost allocations** — Not implemented. FOCUS 1.3 added columns specifically for this. Target for v1.0.
- **Capacity reservations** — FOCUS 1.2 § 2.11 (`CapacityReservationId`, `CapacityReservationStatus`) added support for tracking reserved-but-unused capacity. Not yet supported; requires changes to row generation (one row per reservation hour, not just per usage hour).

---

## Spec references

All section numbers in this document refer to the [FOCUS 1.2 specification](https://focus.finops.org/focus-specification/v1-2/). The spec is the canonical source; this document explains how `focus-bridge` interprets it against AWS CUR 2.0 specifically.
