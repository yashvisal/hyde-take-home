# Gold Mini-Benchmark Summary

## Purpose

This is a small generator-facing regression benchmark for known-hard Razorpay ToS questions. The generator receives the user question and retrieved ToS candidate sources, while hidden gold expectations are used only for scoring. The v2 report separates blocking failures from non-blocking quality signals so the benchmark can guide future generator improvements.

## Scope

- 9 human-curated gold cases.
- 3 cases per category: `clear_answer`, `clarification_required`, and `genuine_ambiguity`.
- Separate from the main 45-row dataset and the eval-hardening adversarial evaluator checks.
- Report v2 adds score-band diagnostics, dimension-loss analysis, pipeline gap themes, and suggested improvements.

## Aggregate Results

- Mean score: `87.56/100`
- Median score: `94/100`
- Pass count: `6/9`
- Category match count: `7/9`
- Required citation coverage count: `8/9`
- Forbidden-claim violations: `0`
- Blocking failures: `3`
- Minor behavior feedback rows: `4`
- Weakest case: `bench_case_005` at `67/100`

## Quality Signals

- Score bands: `100=2`, `95-99=0`, `80-94=5`, `below_80=2`
- Passing rows below 95: `4`
- Score loss by dimension:
  - `reference_behavior_match`: `52` points lost
  - `category_match`: `40` points lost
  - `required_clause_coverage`: `20` points lost
- Pipeline gap themes:
  - `ambiguity_gap_specificity`: `1` rows
  - `ambiguity_next_step`: `3` rows
  - `behavior_completeness`: `6` rows
  - `category_selection`: `2` rows
  - `clear_answer_obligation_specificity`: `1` rows
  - `multi_branch_explanation`: `3` rows
  - `source_retrieval_or_selection`: `1` rows

## Findings

Blocking failures are rows that failed the pass rule and should be fixed before relying on this benchmark:

- `bench_case_005`: Improve category classification, source retrieval, or answer constraints for this boundary case.
- `bench_case_006`: Improve category classification, source retrieval, or answer constraints for this boundary case.
- `bench_case_007`: Improve category classification, source retrieval, or answer constraints for this boundary case.

## Minor Behavior Feedback

- `bench_case_001` (92/100): state that refund does not remove the fee obligation
- `bench_case_004` (94/100): explain that the answer changes based on that notice
- `bench_case_008` (94/100): recommend confirming the basis and timeline externally
- `bench_case_009` (94/100): recommend checking the applicable NPCI/Razorpay guidance

## Weakest Case

`bench_case_005` scored `67/100` on `post-settlement fraud branch between RBI handling and chargeback consequences` and failed the strict gate. The gap is `Expected clarification_required but generated genuine_ambiguity. Missed behavior checks: explain that chargeback and non-chargeback paths differ; cite the fraud clauses.`. This points to a generator category-selection issue, not a source-retrieval miss, because required citation coverage still succeeded.

## Suggested Generator Improvements

- `6` row(s): make the generator explicitly cover every branch/gap implied by the chosen category
- `3` row(s): add a required branch-map sentence for clarification rows with multiple legal or operational outcomes
- `3` row(s): require ambiguity rows to state what external source or party should confirm the unresolved point
- `2` row(s): tighten category boundary instructions with contrastive examples for clarification versus ambiguity
- `1` row(s): prompt clear answers to restate the operative rule in business terms before or alongside the citation
- `1` row(s): boost required-like sibling and cross-reference retrieval before generation
- `1` row(s): prompt ambiguity rows to name the exact unresolved classification or definition

## Changes In This Report Version

- separate blocking failures from passing rows with score/behavior gaps.
- surface lower-scoring pass rows as diagnostic signals.
- summarize score loss by dimension.
- connect missed behaviors and validation repairs to likely generator pipeline gaps.
- include suggested improvements for future generator runs.

## Diagnostic Pass Rows

| Case | Score | Boundary Focus | Main Gap Signal |
|---|---:|---|---|
| `bench_case_001` | 92 | modal language around refunds and fee liability | behavior_completeness, clear_answer_obligation_specificity |
| `bench_case_004` | 94 | multi-party fraud notice condition before settlement suspension | behavior_completeness, multi_branch_explanation |
| `bench_case_008` | 94 | undefined fund-hold duration after suspension | ambiguity_next_step, behavior_completeness |
| `bench_case_009` | 94 | undefined threshold and external NPCI rule dependence | ambiguity_next_step, behavior_completeness, multi_branch_explanation |

## Per-Case Results

| Case | Expected | Generated | Score | Pass | Required Hit | Feedback |
|---|---|---|---:|---|---|---|
| `bench_case_001` | `clear_answer` | `clear_answer` | 92 | `True` | PartB.PartI.3.4 | Minor behavior gaps: state that refund does not remove the fee obligation. |
| `bench_case_002` | `clear_answer` | `clear_answer` | 100 | `True` | PartB.PartI.3.5 | Fully meets the hidden behavior expectations. |
| `bench_case_003` | `clear_answer` | `clear_answer` | 100 | `True` | PartA.2.1 | Fully meets the hidden behavior expectations. |
| `bench_case_004` | `clarification_required` | `clarification_required` | 94 | `True` | PartB.PartI.4.1 | Minor behavior gaps: explain that the answer changes based on that notice. |
| `bench_case_005` | `clarification_required` | `genuine_ambiguity` | 67 | `False` | PartB.PartI.4.2, PartB.PartI.4.3 | Expected clarification_required but generated genuine_ambiguity. Missed behavior checks: explain that chargeback and non-chargeback paths differ; cite the fraud clauses. |
| `bench_case_006` | `clarification_required` | `clarification_required` | 80 | `False` | - | Missed required clauses: PartA.17.1. |
| `bench_case_007` | `genuine_ambiguity` | `clear_answer` | 67 | `False` | PartA.14.10 | Expected genuine_ambiguity but generated clear_answer. Missed behavior checks: flag that the ToS alone does not classify the exact product; recommend confirming with Razorpay or legal counsel. |
| `bench_case_008` | `genuine_ambiguity` | `genuine_ambiguity` | 94 | `True` | PartA.16.1 | Minor behavior gaps: recommend confirming the basis and timeline externally. |
| `bench_case_009` | `genuine_ambiguity` | `genuine_ambiguity` | 94 | `True` | PartB.PartI.4.5 | Minor behavior gaps: recommend checking the applicable NPCI/Razorpay guidance. |

## Output Files

- Generated rows: `data/gold_bench/v2/generated_rows.jsonl`
- Manifest: `data/gold_bench/v2/gold_bench_manifest.json`
