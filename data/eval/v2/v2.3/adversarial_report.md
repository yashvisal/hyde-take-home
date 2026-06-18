# Adversarial Evaluation Report

## Purpose

This report stress-tests the evaluation framework with intentionally flawed rows. It is not a second dataset evaluation run; it is a targeted check that the layered eval stack can distinguish known-bad examples from strong generated rows.

The adversarial pack is deliberately small and hand-authored so each row isolates one failure mode. The rows are grouped by the layer expected to catch them: deterministic validation, advisory quality lints, or the blind LLM judge.

## Why These Eight Examples

- `adv_det_001` plants a fabricated quote against a real clause to test quote containment.
- `adv_det_002` cites a nonexistent clause ID to test source resolution and crash resistance.
- `adv_det_003` violates the category-specific schema shape for a clarification row.
- `adv_lint_004` is schema-valid but uses the vague clarifier `Can you provide more details?`.
- `adv_lint_005` is schema-valid but relies on a thin section preamble as ambiguity support.
- `adv_judge_006` manufactures ambiguity where the cited clause directly answers the question.
- `adv_judge_007` invents a 24-hour refund guarantee and fee waiver unsupported by the cited clause.
- `adv_judge_008` invents a 180-day fund-hold cap where the ToS is silent on duration.

Together these cover the main risks in this assignment: citation integrity, schema/category shape, clarification quality, source strength, unsupported claims, modal/timeline overreach, and category-boundary mistakes.

## Targeted Eval Layers

| Expected layer | Rows | What it should catch |
|---|---:|---|
| deterministic | 3 | Hard failures: unknown sources, unsupported quotes, invalid category-specific fields |
| quality_lints | 2 | Schema-valid but weak examples: vague clarifiers and thin source support |
| judge | 3 | Semantic failures that pass lower layers: wrong category, unsupported claims, overreach |

## Performance

Catch layers are non-exclusive: a row can be caught by the intended lower layer and also noticed by later layers such as the judge.

- Flaws caught by any layer: 8/8
- Flaws caught by the expected layer: 8/8
- Mean adversarial judge score: 58.75/100
- Lowest adversarial judge score: 17/100
- Judge fallback failures: 0
- Catches by deterministic layer: 3
- Catches by quality-lint layer: 4
- Catches by judge layer: 6

| Row | Planted flaw | Expected layer | Caught by | Judge score | Planned -> Judge | Failure modes |
|---|---|---|---|---:|---|---|
| `adv_det_001` | fabricated relevant_quote: the quoted waiver language does not exist in Clause 3.4 (the clause says the opposite - fees remain payable after refunds) | deterministic | deterministic, judge | 40 | `clear_answer` -> `clear_answer` | `unsupported_claim`, `low_business_value` |
| `adv_det_002` | citation to a clause that does not exist in the document (PartZ.9.9) | deterministic | deterministic, quality_lints | 98 | `clear_answer` -> `clear_answer` | none |
| `adv_det_003` | clarification_required row with empty missing_facts, clarifying_questions, and conditional_outcomes, and an assistant answer that never asks a question | deterministic | deterministic, quality_lints, judge | 84 | `clarification_required` -> `clear_answer` | `unsupported_claim` |
| `adv_lint_004` | schema-valid clarification row whose clarifying question is the vague 'Can you provide more details?' instead of a targeted missing-fact question | quality_lints | quality_lints, judge | 70 | `clarification_required` -> `genuine_ambiguity` | `wrong_category`, `missed_ambiguity`, `vague_clarifying_question` |
| `adv_lint_005` | schema-valid ambiguity row whose only cited support is a thin section heading (the Part III preamble) instead of substantive clause text | quality_lints | quality_lints | 97 | `genuine_ambiguity` -> `genuine_ambiguity` | none |
| `adv_judge_006` | mislabeled row: the cited clause answers the question explicitly (fees always payable irrespective of refund), but the row is labeled genuine_ambiguity and the answer manufactures uncertainty | judge | judge | 36 | `genuine_ambiguity` -> `clear_answer` | `wrong_category`, `overstated_ambiguity`, `unsupported_claim`, `low_business_value` |
| `adv_judge_007` | valid citation and quote, but the answer invents a 24-hour guarantee and a fee waiver, and converts the clause's 'may initiate auto-refund within five (5) days' into a certainty | judge | judge | 28 | `clear_answer` -> `genuine_ambiguity` | `wrong_category`, `missed_ambiguity`, `unsupported_claim` |
| `adv_judge_008` | mislabeled and overreaching: the ToS is silent on fund-hold duration (this should be genuine_ambiguity), and the answer invents a 180-day maximum that appears nowhere in the cited text | judge | judge | 17 | `clear_answer` -> `genuine_ambiguity` | `wrong_category`, `missed_ambiguity`, `unsupported_claim`, `weak_citation` |

## Interpretation

The eval stack performed well on the targeted pack: it caught 8/8 planted flaws, and every flaw was caught by the layer it was designed to exercise. The deterministic layer handled source/shape integrity, the advisory lints caught weak-but-valid construction, and the blind judge caught the semantic failures that lower layers intentionally cannot see.

Judge scores are most meaningful for the judge-targeted rows. For deterministic- and lint-targeted rows, the expected lower layer is the source of truth, so a high judge score can still coexist with a correctly caught source-resolution or schema failure.

The most useful signal is the judge-only subset. Those rows were constructed to pass schema validation and advisory lints, so low scores, category disagreements, and `unsupported_claim`/`wrong_category`-style failure modes show that the LLM judge is adding semantic coverage rather than duplicating deterministic checks.

Run metadata: judge model `gpt-5.5`, generator model `gpt-5.5`, cross-model judging `False`, prompt `judge_v2_blind_neutral_ids`.
