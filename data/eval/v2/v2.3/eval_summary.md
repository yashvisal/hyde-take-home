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
- Judge prompt version: `judge_v2_blind_neutral_ids` (blind: no planned label, no original row ID, no deterministic-check results, no annotation fields in the judge prompt)
- Judge temperature: provider default

## Evaluation Design

This evaluation combines deterministic schema/source validators with a blind row-level LLM judge. The judge sees only the user question, assistant answer, full cited clause text, and a neutral prompt row ID; it independently predicts which response category the situation calls for, then scores the row out of 100 against that prediction. The planned category label, original dataset/adversarial row ID, deterministic check results, and structured annotation fields are withheld from the judge and recorded separately as audit metadata, so the judge cannot anchor on prior automated signals. Judge-vs-label agreement is reported as a metric. The lowest-scoring rows then receive a deeper source-adequacy review using hierarchy and taxonomy from the clause index.

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
| Mean score | 93.04/100 |
| Median score | 96/100 |
| Lowest score | 51/100 |
| Highest score | 100/100 |
| Judge fallback failures | 0 |
| Label agreement (judge vs planned category) | 43/45 (95.6%) |

## Results By Category

| Category | Mean score | Lowest score | Rows |
|---|---:|---:|---:|
| `clarification_required` | 85.53 | 51 | 15 |
| `clear_answer` | 98.53 | 88 | 15 |
| `genuine_ambiguity` | 95.07 | 87 | 15 |

## Results By Dimension

| Dimension | Mean score |
|---|---:|
| Category Fit | 23.58 |
| Groundedness | 23.71 |
| Citation Source Sufficiency | 14.11 |
| Answer Usefulness | 8.64 |
| No Overreach | 9.4 |
| Category Specific Behavior | 8.73 |
| User Question Realism | 4.87 |

## Common Failure Modes

| Failure mode | Count |
|---|---:|
| `low_business_value` | 2 |
| `missed_ambiguity` | 2 |
| `missed_clarification` | 1 |
| `synthetic_user_question` | 1 |
| `unsupported_claim` | 1 |
| `weak_citation` | 4 |
| `weak_source_selection` | 3 |
| `wrong_category` | 2 |

## Label Agreement

The blind judge predicts a category for every row without seeing the planned label. Disagreements are surfaced here and routed to human review; they indicate either a mislabeled row or a judge boundary error.

| Row | Planned category | Judge predicted | Judge score |
|---|---|---|---:|
| `rzp_clarification_001` | `clarification_required` | `genuine_ambiguity` | 58 |
| `rzp_clarification_007` | `clarification_required` | `genuine_ambiguity` | 51 |

## Worst Examples

### 1. `rzp_clarification_007`

- Category: `clarification_required`
- Total score: 51/100
- Failure modes: ['wrong_category', 'missed_ambiguity', 'weak_citation', 'weak_source_selection']
- Source adequacy: 35/100; original sources sufficient: `False`

The answer misclassifies a permission question as simple clarification and relies on definition clauses that cannot establish whether debit may proceed. The likely failure is source retrieval/selection plus category labeling. It was caused by missing clauses on approved/authenticated mandate registration and cancellation status. Add rubric/code checks requiring operative authorization clauses for proceed/can questions.

### 2. `rzp_clarification_001`

- Category: `clarification_required`
- Total score: 58/100
- Failure modes: ['wrong_category', 'missed_ambiguity', 'weak_source_selection', 'low_business_value']
- Source adequacy: 70/100; original sources sufficient: `False`

The answer over-clarifies by making chargeback status the sole decision point and misses the merchant liability/recovery provisions. This likely arose in retrieval/source selection and category labeling, caused by selecting only fraud fork clauses 4.2/4.3/4.5. Add rubric checks requiring direct liability clauses and penalizing unnecessary clarification when sources answer materially.

### 3. `rzp_clarification_014`

- Category: `clarification_required`
- Total score: 76/100
- Failure modes: ['missed_clarification', 'weak_citation']
- Source adequacy: 55/100; original sources sufficient: `False`

The answer correctly asks about paid RTO Protection but wrongly says only one fact is missing, omitting eligibility, coverage exclusions, COD status, documentation, and invoicing conditions. This likely stems from retrieval selecting only parent clauses, not itemized subclauses. Add rubric/code checks requiring all condition subclauses for clarification-required reimbursement answers.

## Human Review

This is a compact audit queue, not a full worksheet. The goal is to tell a human reviewer which generated rows and cited clauses deserve attention, without repeating the full answer text or source text already present in the JSONL. The selection is risk-first with positive controls: lowest judge scores, label disagreements, deterministic/schema failures, source-review insufficiency, judge/lint flags, category-coverage backfill, and a seeded sample of perfect-score rows.

Selection settings: lowest-score rows = 3; judge/lint flagged cap = 4; perfect-score positive controls = 2; seed = 42.

Recommended review criteria: confirm the planned category, verify the cited clauses are the strongest support, check that the answer does not overstate modal language or timelines, and decide whether the row should be accepted, patched, or regenerated.

| Row | Planned -> Judge | Score | Why review | Clauses to inspect |
|---|---|---:|---|---|
| `rzp_clear_001` | `clear_answer` -> `clear_answer` | 100 | perfect_score_positive_control; failure modes: none | `PartB.PartI.3.4`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.4, REFUNDS |
| `rzp_clear_002` | `clear_answer` -> `clear_answer` | 88 | category_coverage_backfill; failure modes: `unsupported_claim` | `PartB.PartI.3.5`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.5, REFUNDS |
| `rzp_clarification_001` | `clarification_required` -> `genuine_ambiguity` | 58 | judge_label_disagreement, judge_or_lint_flag, lowest_judge_score, source_review_insufficient; failure modes: `wrong_category`, `missed_ambiguity`, `weak_source_selection`, `low_business_value` | `PartB.PartI.4.2`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.2, FRAUDULENT TRANSACTIONS<br>`PartB.PartI.4.3`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.3, FRAUDULENT TRANSACTIONS<br>`PartB.PartI.4.5`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.5, FRAUDULENT TRANSACTIONS |
| `rzp_clarification_004` | `clarification_required` -> `clarification_required` | 86 | judge_or_lint_flag; failure modes: `low_business_value` | `PartB.PartI.2.1`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 2.1, CHARGEBACKS<br>`PartB.PartI.2.2`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 2.2, CHARGEBACKS |
| `rzp_clarification_007` | `clarification_required` -> `genuine_ambiguity` | 51 | judge_label_disagreement, judge_or_lint_flag, lowest_judge_score, source_review_insufficient; failure modes: `wrong_category`, `missed_ambiguity`, `weak_citation`, `weak_source_selection` | `PartB.PartII.3`: Part B SPECIFIC TERMS AND CONDITIONS, Part II Specific Terms For E-Mandate Services, Clause 3, Preamble<br>`PartB.PartII.2`: Part B SPECIFIC TERMS AND CONDITIONS, Part II Specific Terms For E-Mandate Services, Clause 2, Preamble |
| `rzp_clarification_014` | `clarification_required` -> `clarification_required` | 76 | judge_or_lint_flag, lowest_judge_score, source_review_insufficient; failure modes: `missed_clarification`, `weak_citation` | `PartB.PartVI.2.1`: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.1, RTO Protection<br>`PartB.PartVI.2.4`: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.4, RTO Protection |
| `rzp_ambiguity_002` | `genuine_ambiguity` -> `genuine_ambiguity` | 100 | perfect_score_positive_control; failure modes: none | `PartB.PartI.4.5`: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.5, FRAUDULENT TRANSACTIONS |
| `rzp_ambiguity_007` | `genuine_ambiguity` -> `genuine_ambiguity` | 87 | category_coverage_backfill; failure modes: `weak_citation`, `weak_source_selection` | `PartA.16.1.item001`: Part A GENERAL TERMS AND CONDITIONS, Clause 16.1.item001, SUSPENSION AND TERMINATION |
## Changes Since V1

V1 generated this dataset and evaluated it with a category-aware judge that shared the generator model and saw the planned label plus deterministic-check results. Feedback identified possible same-model bias/leakage and a lack of gold human checks and adversarial checks. V2 hardens the evaluation mechanism and re-evaluates the same, unchanged dataset:

- Blind judging: the judge no longer sees the planned category, deterministic-check results, or annotation fields, and must predict the category itself.
- Cross-model judging supported via --judge-model / OPENAI_JUDGE_MODEL; this run reused the generator model, so blind prompting is the active mitigation (same-model bias remains a caveat).
- Human review: a compact, pipeline-selected audit queue listing the rows and cited clauses a reviewer should inspect.
- Adversarial pack: intentionally flawed rows with a standalone per-layer catch-rate report.

Because the v2 judge uses a different prompt and model, score deltas against v1 are directional rather than point-comparable. Failure-mode counts and label agreement are the like-for-like evidence for the dataset run; the adversarial catch rate is reported separately in `adversarial_report.md`.

| Metric | V1 | V2 |
|---|---|---|
| Judge model | `gpt-5.5` | `gpt-5.5` |
| Judge prompt | `judge_v1_category_aware` | `judge_v2_blind_neutral_ids` |
| Judge sees planned label / deterministic checks | yes | no |
| Cross-model judging | no | no |
| Mean score | 94.22 | 93.04 |
| Median score | 96 | 96 |
| Lowest score | 74 | 51 |
| Mean score (`clarification_required`) | 88.73 | 85.53 |
| Mean score (`clear_answer`) | 99 | 98.53 |
| Mean score (`genuine_ambiguity`) | 94.93 | 95.07 |
| Failure mode `low_business_value` | 0 | 2 |
| Failure mode `missed_ambiguity` | 1 | 2 |
| Failure mode `missed_clarification` | 3 | 1 |
| Failure mode `synthetic_user_question` | 0 | 1 |
| Failure mode `unsupported_claim` | 3 | 1 |
| Failure mode `weak_citation` | 5 | 4 |
| Failure mode `weak_source_selection` | 4 | 3 |
| Failure mode `wrong_category` | 0 | 2 |
| Label agreement | not measured | 43/45 |

## Interpretation

The dataset is strong overall with review caveats. The evaluation is intentionally stricter than schema validation: it tests category fit, source grounding, citation sufficiency, usefulness, overreach, and realism. Because the judge mechanism changed in v2 (blind judging, optional cross-model), scores are not directly point-comparable to v1; failure modes and label agreement are the like-for-like evidence for the dataset run, while the adversarial catch rate is documented separately in `adversarial_report.md`.

## Future Improvements

- Run source-adequacy retrieval for all rows, not only the lowest-scoring rows.
- Move candidate source retrieval upstream into dataset generation so weak citations are caught before rows are written.
- Add a user-question realism rewrite pass for overly polished or synthetic prompts.
- Regenerate or patch rows that fail human review, then re-run this evaluation.
