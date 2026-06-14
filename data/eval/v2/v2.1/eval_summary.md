# LLM-as-Judge Evaluation Summary

## Dataset Evaluated

- Dataset path: `data/output/razorpay_synthetic_qa.jsonl`
- Run ID: `2026-05-22_generation_v1`
- Source hash: `sha256:684e37541a8f723d2111b3b1a18c5c18d16aa14154a706f9064babf6ca6d17f0`
- Source fetched at: `2026-05-22T19:44:43Z`
- Row count: 45
- Category distribution: {'clarification_required': 15, 'clear_answer': 15, 'genuine_ambiguity': 15}
- Generator model: `gpt-5.5`
- Judge model: `gpt-5.5`
- Cross-model judging: `False`
- Judge prompt version: `judge_v2_blind` (blind: no planned label, no deterministic-check results, no annotation fields in the judge prompt)
- Judge temperature: provider default

## Evaluation Design

This evaluation combines deterministic schema/source validators with a blind row-level LLM judge. The judge sees only the user question, assistant answer, and full cited clause text; it independently predicts which response category the situation calls for, then scores the row out of 100 against that prediction. The planned category label, deterministic check results, and structured annotation fields are withheld from the judge and recorded separately as audit metadata, so the judge cannot anchor on prior automated signals. Judge-vs-label agreement is reported as a metric. The lowest-scoring rows then receive a deeper source-adequacy review using hierarchy and taxonomy from the clause index.

## Deterministic Validation Results


| Check                       | Result   |
| --------------------------- | -------- |
| Rows parsed                 | 45/45    |
| Category balance            | 15/15/15 |
| Source clauses resolve      | 45/45    |
| Relevant quotes contained   | 45/45    |
| Assistant citations visible | 45/45    |
| Category fields valid       | 45/45    |
| Dataset validator failures  | 0        |


## Scoring Rubric


| Dimension                   | Points |
| --------------------------- | ------ |
| Category Fit                | 25     |
| Groundedness                | 25     |
| Citation Source Sufficiency | 15     |
| Answer Usefulness           | 10     |
| No Overreach                | 10     |
| Category Specific Behavior  | 10     |
| User Question Realism       | 5      |
| Total                       | 100    |


## Aggregate Results


| Metric                                      | Result         |
| ------------------------------------------- | -------------- |
| Mean score                                  | 93.84/100      |
| Median score                                | 95/100         |
| Lowest score                                | 68/100         |
| Highest score                               | 100/100        |
| Judge fallback failures                     | 0              |
| Label agreement (judge vs planned category) | 45/45 (100.0%) |


## Results By Category


| Category                 | Mean score | Lowest score | Rows |
| ------------------------ | ---------- | ------------ | ---- |
| `clarification_required` | 87.6       | 68           | 15   |
| `clear_answer`           | 98.6       | 85           | 15   |
| `genuine_ambiguity`      | 95.33      | 90           | 15   |


## Results By Dimension


| Dimension                   | Mean score |
| --------------------------- | ---------- |
| Category Fit                | 23.89      |
| Groundedness                | 23.93      |
| Citation Source Sufficiency | 14.16      |
| Answer Usefulness           | 8.73       |
| No Overreach                | 9.49       |
| Category Specific Behavior  | 8.73       |
| User Question Realism       | 4.91       |


## Common Failure Modes


| Failure mode                | Count |
| --------------------------- | ----- |
| `low_business_value`        | 2     |
| `missed_ambiguity`          | 1     |
| `missed_clarification`      | 2     |
| `unsupported_claim`         | 2     |
| `vague_clarifying_question` | 1     |
| `weak_citation`             | 3     |
| `weak_source_selection`     | 1     |


## Label Agreement

The blind judge predicts a category for every row without seeing the planned label. Disagreements are surfaced here and routed to human review; they indicate either a mislabeled row or a judge boundary error.

The judge's predicted category matched the planned label on every judged row.

## Worst Examples

### 1. `rzp_clarification_001`

- Category: `clarification_required`
- Total score: 68/100
- Failure modes: ['missed_clarification', 'weak_source_selection', 'low_business_value']
- Source adequacy: 72/100; original sources sufficient: `False`

The answer asks a relevant chargeback-status clarification but frames it as the only key fact, omitting liability/recovery analysis under clauses 4.5, 2.1, 2.3, and 4.4. This likely came from source selection and answer assembly: branching clauses were prioritized over liability clauses. Rubric/code should require checking direct liability clauses for chargeback/fraud queries.

### 2. `rzp_clarification_014`

- Category: `clarification_required`
- Total score: 74/100
- Failure modes: ['missed_clarification', 'unsupported_claim', 'weak_citation']
- Source adequacy: 45/100; original sources sufficient: `False`

The answer asks one useful clarification but wrongly says only paid RTO Protection status is missing, omitting other eligibility, causation, documentary, and process conditions. This likely arose in retrieval/source selection and generation, which used a parent clause without child items. Require citing all conditional subclauses and penalize “only missing fact” overclaims.

### 3. `rzp_clarification_009`

- Category: `clarification_required`
- Total score: 79/100
- Failure modes: ['missed_ambiguity', 'vague_clarifying_question']
- Source adequacy: 82/100; original sources sufficient: `True`

The answer over-clarifies and misses the key ambiguity: the ToS permits immediate suspension for an actual breach but does not say Razorpay must identify the breached clause in the email. This is an answer-generation issue caused by treating missing notice detail as user clarification need. Add rubric/code checks for distinguishing contractual gaps from factual clarifications.

## Human Review

This is a compact audit queue, not a full worksheet. The goal is to tell a human reviewer which generated rows and cited clauses deserve attention, without repeating the full answer text or source text already present in the JSONL. The selection is risk-first with positive controls: lowest judge scores, label disagreements, deterministic/schema failures, source-review insufficiency, judge/lint flags, category-coverage backfill, and a seeded sample of perfect-score rows.

Selection settings: lowest-score rows = 3; judge/lint flagged cap = 4; perfect-score positive controls = 2; seed = 42.

Recommended review criteria: confirm the planned category, verify the cited clauses are the strongest support, check that the answer does not overstate modal language or timelines, and decide whether the row should be accepted, patched, or regenerated.


| Row                     | Planned -> Judge                                     | Score | Why review                                                                                                                                               | Clauses to inspect                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------- | ---------------------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rzp_clear_001`         | `clear_answer` -> `clear_answer`                     | 100   | perfect_score_positive_control; failure modes: none                                                                                                      | `PartB.PartI.3.4`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.4, REFUNDS                                                                                                                                                                                                                                                                                                                           |
| `rzp_clear_002`         | `clear_answer` -> `clear_answer`                     | 85    | category_coverage_backfill; failure modes: `unsupported_claim`                                                                                           | `PartB.PartI.3.5`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.5, REFUNDS                                                                                                                                                                                                                                                                                                                           |
| `rzp_clear_015`         | `clear_answer` -> `clear_answer`                     | 100   | perfect_score_positive_control; failure modes: none                                                                                                      | `PartA.2.1`: Part A GENERAL TERMS AND CONDITIONS, Clause 2.1, USAGE OF THE SERVICES BY THE USER                                                                                                                                                                                                                                                                                                                                                                |
| `rzp_clarification_001` | `clarification_required` -> `clarification_required` | 68    | judge_or_lint_flag, lowest_judge_score, source_review_insufficient; failure modes: `missed_clarification`, `weak_source_selection`, `low_business_value` | `PartB.PartI.4.2`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.2, FRAUDULENT TRANSACTIONS `PartB.PartI.4.3`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.3, FRAUDULENT TRANSACTIONS `PartB.PartI.4.5`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.5, FRAUDULENT TRANSACTIONS |
| `rzp_clarification_007` | `clarification_required` -> `clarification_required` | 83    | judge_or_lint_flag; failure modes: `weak_citation`                                                                                                       | `PartB.PartII.3`: Part B SPECIFIC TERMS AND CONDITIONS, Part II Specific Terms For E-Mandate Services, Clause 3, Preamble `PartB.PartII.2`: Part B SPECIFIC TERMS AND CONDITIONS, Part II Specific Terms For E-Mandate Services, Clause 2, Preamble                                                                                                                                                                                                            |
| `rzp_clarification_009` | `clarification_required` -> `clarification_required` | 79    | judge_or_lint_flag, lowest_judge_score; failure modes: `missed_ambiguity`, `vague_clarifying_question`                                                   | `PartA.16.1`: Part A GENERAL TERMS AND CONDITIONS, Clause 16.1, SUSPENSION AND TERMINATION `PartA.16.1.item001`: Part A GENERAL TERMS AND CONDITIONS, Clause 16.1.item001, SUSPENSION AND TERMINATION                                                                                                                                                                                                                                                          |
| `rzp_clarification_014` | `clarification_required` -> `clarification_required` | 74    | judge_or_lint_flag, lowest_judge_score, source_review_insufficient; failure modes: `missed_clarification`, `unsupported_claim`, `weak_citation`          | `PartB.PartVI.2.1`: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.1, RTO Protection `PartB.PartVI.2.4`: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.4, RTO Protection                                                                                                                                                                                                                                      |
| `rzp_ambiguity_014`     | `genuine_ambiguity` -> `genuine_ambiguity`           | 90    | category_coverage_backfill; failure modes: none                                                                                                          | `PartB.PartVI.2.1`: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.1, RTO Protection                                                                                                                                                                                                                                                                                                                                                   |


## Changes Since V1

V1 generated this dataset and evaluated it with a category-aware judge that shared the generator model and saw the planned label plus deterministic-check results. Feedback identified possible same-model bias/leakage and a lack of gold human checks and adversarial checks. V2 hardens the evaluation mechanism and re-evaluates the same, unchanged dataset:

- Blind judging: the judge no longer sees the planned category, deterministic-check results, or annotation fields, and must predict the category itself.
- Cross-model judging supported via --judge-model / OPENAI_JUDGE_MODEL; this run reused the generator model, so blind prompting is the active mitigation.
- Human review: a compact, pipeline-selected audit queue listing the rows and cited clauses a reviewer should inspect.
- Adversarial pack: intentionally flawed rows with a standalone per-layer catch-rate report.

Because the v2 judge uses a different prompt and model, score deltas against v1 are directional rather than point-comparable. Failure-mode counts and label agreement are the like-for-like evidence for the dataset run; the adversarial catch rate is reported separately in `adversarial_report.md`.


| Metric                                          | V1                        | V2               |
| ----------------------------------------------- | ------------------------- | ---------------- |
| Judge model                                     | `gpt-5.5`                 | `gpt-5.5`        |
| Judge prompt                                    | `judge_v1_category_aware` | `judge_v2_blind` |
| Judge sees planned label / deterministic checks | yes                       | no               |
| Cross-model judging                             | no                        | no               |
| Mean score                                      | 94.22                     | 93.84            |
| Median score                                    | 96                        | 95               |
| Lowest score                                    | 74                        | 68               |
| Mean score (`clarification_required`)           | 88.73                     | 87.6             |
| Mean score (`clear_answer`)                     | 99                        | 98.6             |
| Mean score (`genuine_ambiguity`)                | 94.93                     | 95.33            |
| Failure mode `low_business_value`               | 0                         | 2                |
| Failure mode `missed_ambiguity`                 | 1                         | 1                |
| Failure mode `missed_clarification`             | 3                         | 2                |
| Failure mode `unsupported_claim`                | 3                         | 2                |
| Failure mode `vague_clarifying_question`        | 0                         | 1                |
| Failure mode `weak_citation`                    | 5                         | 3                |
| Failure mode `weak_source_selection`            | 4                         | 1                |
| Label agreement                                 | not measured              | 45/45            |


## Interpretation

The dataset is strong overall with review caveats. The evaluation is intentionally stricter than schema validation: it tests category fit, source grounding, citation sufficiency, usefulness, overreach, and realism. Because the judge mechanism changed in v2 (blind judging, optional cross-model), scores are not directly point-comparable to v1; failure modes and label agreement are the like-for-like evidence for the dataset run, while the adversarial catch rate is documented separately in `adversarial_report.md`.

## Future Improvements

- Run source-adequacy retrieval for all rows, not only the lowest-scoring rows.
- Move candidate source retrieval upstream into dataset generation so weak citations are caught before rows are written.
- Add a user-question realism rewrite pass for overly polished or synthetic prompts.
- Regenerate or patch rows that fail human review, then re-run this evaluation.

