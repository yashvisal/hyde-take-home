# Gold Mini-Benchmark Summary

## Purpose

This is a small generator-facing regression benchmark for known-hard Razorpay ToS questions. The generator receives the user question and retrieved ToS candidate sources, while hidden gold expectations are used only for scoring.

## Scope

- 9 human-curated gold cases.
- 3 cases per category: `clear_answer`, `clarification_required`, and `genuine_ambiguity`.
- Separate from the main 45-row dataset and the eval-hardening adversarial evaluator checks.

## Aggregate Results

- Mean score: `95.22/100`
- Median score: `94/100`
- Pass count: `9/9`
- Category match count: `9/9`
- Required citation coverage count: `9/9`
- Forbidden-claim violations: `0`
- Blocking failures: `0`
- Minor behavior feedback rows: `5`
- Weakest case: `gold_clarification_002` at `81/100`

## Per-Case Results

| Case | Expected | Generated | Score | Pass | Required Hit | Feedback |
|---|---|---|---:|---|---|---|
| `gold_clear_001` | `clear_answer` | `clear_answer` | 100 | `True` | PartB.PartI.3.4 | Fully meets the hidden behavior expectations. |
| `gold_clear_002` | `clear_answer` | `clear_answer` | 100 | `True` | PartB.PartI.3.5 | Fully meets the hidden behavior expectations. |
| `gold_clear_003` | `clear_answer` | `clear_answer` | 100 | `True` | PartA.2.1 | Fully meets the hidden behavior expectations. |
| `gold_clarification_001` | `clarification_required` | `clarification_required` | 94 | `True` | PartB.PartI.4.1 | Minor behavior gaps: explain that the answer changes based on that notice. |
| `gold_clarification_002` | `clarification_required` | `clarification_required` | 81 | `True` | PartB.PartI.4.2, PartB.PartI.4.3 | Minor behavior gaps: explain that chargeback and non-chargeback paths differ; mention RBI or NPCI external-rule dependence where relevant; cite the fraud clauses. |
| `gold_clarification_003` | `clarification_required` | `clarification_required` | 100 | `True` | PartA.17.1 | Fully meets the hidden behavior expectations. |
| `gold_ambiguity_001` | `genuine_ambiguity` | `genuine_ambiguity` | 94 | `True` | PartA.14.10 | Minor behavior gaps: recommend confirming with Razorpay or legal counsel. |
| `gold_ambiguity_002` | `genuine_ambiguity` | `genuine_ambiguity` | 94 | `True` | PartA.16.1 | Minor behavior gaps: recommend confirming the basis and timeline externally. |
| `gold_ambiguity_003` | `genuine_ambiguity` | `genuine_ambiguity` | 94 | `True` | PartB.PartI.4.5 | Minor behavior gaps: recommend checking the applicable NPCI/Razorpay guidance. |

## Findings

All cases passed the strict gate, with no category mismatches, required-citation failures, or forbidden-claim violations.

## Minor Behavior Feedback

- `gold_clarification_001` (94/100): explain that the answer changes based on that notice
- `gold_clarification_002` (81/100): explain that chargeback and non-chargeback paths differ; mention RBI or NPCI external-rule dependence where relevant; cite the fraud clauses
- `gold_ambiguity_001` (94/100): recommend confirming with Razorpay or legal counsel
- `gold_ambiguity_002` (94/100): recommend confirming the basis and timeline externally
- `gold_ambiguity_003` (94/100): recommend checking the applicable NPCI/Razorpay guidance

## Weakest Case

`gold_clarification_002` scored `81/100`. This is the primary generator improvement target even though it may still pass the strict gate.

## Output Files

- Generated rows: `data/gold_bench/v1/generated_rows.jsonl`
- Manifest: `data/gold_bench/v1/gold_bench_manifest.json`
