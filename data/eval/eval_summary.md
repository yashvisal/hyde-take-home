# LLM-as-Judge Evaluation Summary

## Dataset Evaluated

- Dataset path: `data/output/razorpay_synthetic_qa.jsonl`
- Run ID: `2026-05-22_generation_v1`
- Source hash: `sha256:684e37541a8f723d2111b3b1a18c5c18d16aa14154a706f9064babf6ca6d17f0`
- Source fetched at: `2026-05-22T19:44:43Z`
- Row count: 45
- Category distribution: {'clarification_required': 15, 'clear_answer': 15, 'genuine_ambiguity': 15}
- Judge model: `gpt-5.5`
- Judge temperature: provider default

## Evaluation Design

This evaluation combines existing deterministic schema/source validators with a row-level LLM judge. Each row is scored out of 100 using only the generated QA row and cited source clauses. The lowest-scoring rows then receive a deeper source-adequacy review using hierarchy and taxonomy from the clause index.

## Deterministic Validation Results

| Check | Result |
|---|---:|
| Rows parsed | 45/45 |
| Category balance | 15/15/15 |
| Source clauses resolve | 45/45 |
| Relevant quotes contained | 45/45 |
| Assistant citations visible | 45/45 |
| Category fields valid | 45/45 |
| Dataset validator failures | 0 |

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
| Mean score | 94.22/100 |
| Median score | 96/100 |
| Lowest score | 74/100 |
| Highest score | 100/100 |
| Judge fallback failures | 0 |

## Results By Category

| Category | Mean score | Lowest score | Rows |
|---|---:|---:|---:|
| `clarification_required` | 88.73 | 74 | 15 |
| `clear_answer` | 99 | 92 | 15 |
| `genuine_ambiguity` | 94.93 | 79 | 15 |

## Results By Dimension

| Dimension | Mean score |
|---|---:|
| Category Fit | 24.24 |
| Groundedness | 23.87 |
| Citation Source Sufficiency | 13.91 |
| Answer Usefulness | 8.93 |
| No Overreach | 9.38 |
| Category Specific Behavior | 9 |
| User Question Realism | 4.89 |

## Common Failure Modes

| Failure mode | Count |
|---|---:|
| `missed_ambiguity` | 1 |
| `missed_clarification` | 3 |
| `unsupported_claim` | 3 |
| `weak_citation` | 5 |
| `weak_source_selection` | 4 |

## Worst Examples

### 1. `rzp_clarification_009`

- Category: `clarification_required`
- Total score: 74/100
- Failure modes: ['missed_ambiguity', 'unsupported_claim', 'weak_source_selection']
- Source adequacy: 82/100; original sources sufficient: `True`

The answer asks whether Razorpay identified a specific breached clause, incorrectly implying notice specificity is legally material. This likely arose in answer generation, not source retrieval, because the cited clauses suffice. The cause was conflating actual breach ambiguity with email-disclosure requirements. A rubric/code check should flag unsupported clarification questions not grounded in cited text.

### 2. `rzp_clarification_014`

- Category: `clarification_required`
- Total score: 75/100
- Failure modes: ['missed_clarification', 'weak_citation', 'weak_source_selection', 'unsupported_claim']
- Source adequacy: 45/100; original sources sufficient: `False`

The answer asks only whether paid RTO Protection was enabled, missing several required eligibility facts, exclusions, documentation, and claim timing. This likely came from source-selection/retrieval truncating Clause 2.1 to its header plus weak Clause 2.4. The cause is treating one prerequisite as exhaustive. A rubric/check should require all cited subconditions for conditional reimbursement questions.

### 3. `rzp_ambiguity_007`

- Category: `genuine_ambiguity`
- Total score: 79/100
- Failure modes: ['weak_citation', 'weak_source_selection']
- Source adequacy: 35/100; original sources sufficient: `False`

The answer correctly identifies ambiguity but cites only a generic breach-based suspension clause, missing the directly relevant “unacceptable risk” and internal risk-score clauses. This likely arose in source retrieval/selection, caused by keyword or clause-ranking weakness. A citation-sufficiency rubric or retrieval test requiring most-specific risk-language sources would catch it.

## Interpretation

The dataset is acceptable for submission with caveats. The evaluation is intentionally stricter than schema validation: it tests category fit, source grounding, citation sufficiency, usefulness, overreach, and realism. The weakest examples show where source selection or category-boundary choices should be tightened before a larger training set is generated.

## Future Improvements

- Run source-adequacy retrieval for all rows, not only the lowest-scoring rows.
- Move candidate source retrieval upstream into dataset generation so weak citations are caught before rows are written.
- Add a user-question realism rewrite pass for overly polished or synthetic prompts.
- Add an adversarial category-fit check for close calls between clarification-required and genuine-ambiguity examples.
- Add a small human calibration set to compare judge scores against expert review.
