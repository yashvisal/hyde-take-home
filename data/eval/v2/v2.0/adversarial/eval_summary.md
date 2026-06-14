# Adversarial Pack Evaluation Summary

## Dataset Evaluated

- Dataset path: `data/tests/eval/adversarial_pack.jsonl`
- Run ID: `2026-05-22_generation_v1`
- Source hash: `sha256:684e37541a8f723d2111b3b1a18c5c18d16aa14154a706f9064babf6ca6d17f0`
- Source fetched at: `2026-05-22T19:44:43Z`
- Row count: 8
- Category distribution: {'clarification_required': 2, 'clear_answer': 4, 'genuine_ambiguity': 2}
- Generator model: `gpt-5.5`
- Judge model: `gpt-5.5`
- Cross-model judging: `False`
- Judge prompt version: `judge_v2_blind` (blind: no planned label, no deterministic-check results, no annotation fields in the judge prompt)
- Judge temperature: provider default

## Evaluation Design

This evaluation combines deterministic schema/source validators with a blind row-level LLM judge. The judge sees only the user question, assistant answer, and full cited clause text; it independently predicts which response category the situation calls for, then scores the row out of 100 against that prediction. The planned category label, deterministic check results, and structured annotation fields are withheld from the judge and recorded separately as audit metadata, so the judge cannot anchor on prior automated signals. Judge-vs-label agreement is reported as a metric. The lowest-scoring rows then receive a deeper source-adequacy review using hierarchy and taxonomy from the clause index.

## Deterministic Validation Results

| Check | Result |
|---|---:|
| Rows parsed | 8/8 |
| Category balance | 4/2/2 |
| Source clauses resolve | 7/8 |
| Relevant quotes contained | 6/8 |
| Assistant citations visible | 8/8 |
| Category fields valid | 5/8 |
| Dataset validator failures | 3 |

## Scoring Rubric

| Dimension | Points |
|---|---:|
| Category Fit | 25 |
| Groundedness | 25 |
| Citation Source Sufficiency | 15 |
| Answer Usefulness | 10 |
| No Overreach | 10 |
| Category Specific Behavior | 10 |
| User Question Realism | 5 |
| Total | 100 |

## Aggregate Results

| Metric | Result |
|---|---:|
| Mean score | 60.88/100 |
| Median score | 56.0/100 |
| Lowest score | 17/100 |
| Highest score | 99/100 |
| Judge fallback failures | 0 |
| Label agreement (judge vs planned category) | 3/8 (37.5%) |

## Results By Category

| Category | Mean score | Lowest score | Rows |
|---|---:|---:|---:|
| `clarification_required` | 80.5 | 68 | 2 |
| `clear_answer` | 47.5 | 17 | 4 |
| `genuine_ambiguity` | 68 | 40 | 2 |

## Results By Dimension

| Dimension | Mean score |
|---|---:|
| Category Fit | 14.88 |
| Groundedness | 13.62 |
| Citation Source Sufficiency | 12.62 |
| Answer Usefulness | 4.88 |
| No Overreach | 5.12 |
| Category Specific Behavior | 4.75 |
| User Question Realism | 5 |

## Common Failure Modes

| Failure mode | Count |
|---|---:|
| `low_business_value` | 1 |
| `missed_ambiguity` | 3 |
| `overstated_ambiguity` | 1 |
| `unsupported_claim` | 4 |
| `vague_clarifying_question` | 1 |
| `weak_citation` | 2 |
| `wrong_category` | 4 |

## Adversarial Catch Rate By Layer

Each row below contains one intentionally planted flaw. The table shows which evaluation layer was expected to catch it and which layers actually did. This tests the eval framework as a stack of defenses (deterministic checks, then quality lints, then the blind LLM judge) rather than treating the judge as the only safeguard.

- Flaws caught (any layer): 8/8
- Flaws caught by the expected layer: 8/8

| Row | Planted flaw | Expected layer | Caught by | Caught? |
|---|---|---|---|---|
| `adv_det_001` | fabricated relevant_quote: the quoted waiver language does not exist in Clause 3.4 (the clause says the opposite - fees remain payable after refunds) | deterministic | deterministic, judge | yes |
| `adv_det_002` | citation to a clause that does not exist in the document (PartZ.9.9) | deterministic | deterministic, quality_lints | yes |
| `adv_det_003` | clarification_required row with empty missing_facts, clarifying_questions, and conditional_outcomes, and an assistant answer that never asks a question | deterministic | deterministic, quality_lints, judge | yes |
| `adv_lint_004` | schema-valid clarification row whose clarifying question is the vague 'Can you provide more details?' instead of a targeted missing-fact question | quality_lints | quality_lints, judge | yes |
| `adv_lint_005` | schema-valid ambiguity row whose only cited support is a thin section heading (the Part III preamble) instead of substantive clause text | quality_lints | quality_lints | yes |
| `adv_judge_006` | mislabeled row: the cited clause answers the question explicitly (fees always payable irrespective of refund), but the row is labeled genuine_ambiguity and the answer manufactures uncertainty | judge | judge | yes |
| `adv_judge_007` | valid citation and quote, but the answer invents a 24-hour guarantee and a fee waiver, and converts the clause's 'may initiate auto-refund within five (5) days' into a certainty | judge | judge | yes |
| `adv_judge_008` | mislabeled and overreaching: the ToS is silent on fund-hold duration (this should be genuine_ambiguity), and the answer invents a 180-day maximum that appears nowhere in the cited text | judge | judge | yes |

## Label Agreement

The blind judge predicts a category for every row without seeing the planned label. Disagreements are surfaced here and routed to human review; they indicate either a mislabeled row or a judge boundary error.

| Row | Planned category | Judge predicted | Judge score |
|---|---|---|---:|
| `adv_det_003` | `clarification_required` | `clear_answer` | 93 |
| `adv_judge_006` | `genuine_ambiguity` | `clear_answer` | 40 |
| `adv_judge_007` | `clear_answer` | `genuine_ambiguity` | 30 |
| `adv_judge_008` | `clear_answer` | `genuine_ambiguity` | 17 |
| `adv_lint_004` | `clarification_required` | `genuine_ambiguity` | 68 |

## Worst Examples

### 1. `adv_judge_008`

- Category: `clear_answer`
- Total score: 17/100
- Failure modes: ['wrong_category', 'missed_ambiguity', 'unsupported_claim', 'weak_citation']
- Source adequacy: 45/100; original sources sufficient: `False`

The answer fabricates a 180-day maximum hold and mandatory release date, though Clause 16.1 only permits suspension of services/settlements. The error likely arose in retrieval/answer generation: weak source selection plus hallucinated policy detail. A rubric/code check should require quoted support for durations and force “ToS gap/ambiguous” when no limit appears.

### 2. `adv_judge_007`

- Category: `clear_answer`
- Total score: 30/100
- Failure modes: ['wrong_category', 'missed_ambiguity', 'unsupported_claim']
- Source adequacy: 90/100; original sources sufficient: `True`

The answer incorrectly turns Clause 3.5’s discretionary “may initiate an auto-refund within five days” into a guaranteed automatic refund within 24 hours at no cost. This likely arose in answer generation, not retrieval, from modal/temporal overreach. Add rubric/code checks flagging invented deadlines, guarantees, and “may” rewritten as “will.”

### 3. `adv_judge_006`

- Category: `genuine_ambiguity`
- Total score: 40/100
- Failure modes: ['wrong_category', 'overstated_ambiguity', 'unsupported_claim', 'low_business_value']
- Source adequacy: 100/100; original sources sufficient: `True`

The answer incorrectly labels a clear contractual rule as ambiguous and withholds the needed yes/no: fees remain payable despite a full refund. This likely arose in answer generation, not retrieval, since the correct clause was cited. The model over-weighted uncertainty language. Add rubric/checks penalizing ambiguity when cited text directly resolves liability.

## Interpretation

The evaluation framework caught 8/8 intentionally planted flaws. This run exists to test the eval framework itself: every row above is deliberately defective, so low scores and failures here are the desired outcome.
