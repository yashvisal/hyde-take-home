# LLM-as-Judge Evaluation Summary (V2)

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
| Mean score | 93.96/100 |
| Median score | 96/100 |
| Lowest score | 74/100 |
| Highest score | 100/100 |
| Judge fallback failures | 0 |
| Label agreement (judge vs planned category) | 45/45 (100.0%) |

## Results By Category

| Category | Mean score | Lowest score | Rows |
|---|---:|---:|---:|
| `clarification_required` | 88.87 | 74 | 15 |
| `clear_answer` | 98.07 | 80 | 15 |
| `genuine_ambiguity` | 94.93 | 82 | 15 |

## Results By Dimension

| Dimension | Mean score |
|---|---:|
| Category Fit | 23.91 |
| Groundedness | 23.91 |
| Citation Source Sufficiency | 14.16 |
| Answer Usefulness | 8.71 |
| No Overreach | 9.53 |
| Category Specific Behavior | 8.87 |
| User Question Realism | 4.87 |

## Common Failure Modes

| Failure mode | Count |
|---|---:|
| `low_business_value` | 1 |
| `missed_clarification` | 1 |
| `unsupported_claim` | 2 |
| `weak_citation` | 4 |
| `weak_source_selection` | 2 |

## Label Agreement

The blind judge predicts a category for every row without seeing the planned label. Disagreements are surfaced here and routed to human review; they indicate either a mislabeled row or a judge boundary error.

The judge's predicted category matched the planned label on every judged row.

## Worst Examples

### 1. `rzp_clarification_001`

- Category: `clarification_required`
- Total score: 74/100
- Failure modes: ['missed_clarification', 'weak_citation']
- Source adequacy: 72/100; original sources sufficient: `False`

The answer under-clarifies the liability question: it asks only whether a chargeback occurred and omits the fraud/chargeback liability analysis. This likely arose in source selection and response planning, caused by relying on branching clauses 4.2/4.3 while missing 4.5 and more specific 2.1/2.3/4.4. A rubric/code check should require liability-specific clauses and all branch-selecting facts.

### 2. `rzp_clarification_014`

- Category: `clarification_required`
- Total score: 74/100
- Failure modes: ['weak_citation', 'weak_source_selection', 'unsupported_claim']
- Source adequacy: 45/100; original sources sufficient: `False`

The answer correctly asks about paid RTO Protection but wrongly says only one fact is missing and cites incomplete sources. This likely stems from retrieval/source selection stopping at the parent clause and missing subconditions. The cause is insufficient clause granularity. Add a rubric/code check requiring all eligibility, exclusion, documentation, and claim-process clauses for clarification cases.

### 3. `rzp_clear_002`

- Category: `clear_answer`
- Total score: 80/100
- Failure modes: ['unsupported_claim']
- Source adequacy: 95/100; original sources sufficient: `True`

The answer overstates a permissive contractual right: it says “Yes” Razorpay will refund, while the clause only says Razorpay PA may initiate auto-refund within five days. This likely arose in answer generation from flattening modal language. Add a rubric/code check penalizing definitive answers to “will” questions when sources use “may.”

## Human Review

This section is the gold human check on the generated dataset. Rows are selected by the pipeline using three criteria: a seeded stratified random sample (2 per category, seed 42, independent of judge output), the 3 lowest-scoring rows, and every row where the blind judge's predicted category disagrees with the planned label. A human fills in the verdicts below; `overall_verdict` rolls up into the dataset-quality line.

Verdict options: `overall_verdict`: accept | minor_fix | major_fix | reject; `category_verdict`: correct | incorrect; `groundedness_verdict`: grounded | partially_grounded | ungrounded; `citation_sufficiency_verdict`: sufficient | insufficient.

Overall rollup (fill in after review): _N_ accept, _N_ minor_fix, _N_ major_fix, _N_ reject out of 9 reviewed.

### Review: `rzp_clear_001`

- Selection reason(s): stratified_random_sample
- Planned category: `clear_answer`; judge predicted: `clear_answer`; judge score: 100/100
- User question: We refunded a customer's transaction through Razorpay's refund flow. Are Razorpay PA fees still payable on that transaction?
- Assistant answer: Yes, under Part B, Part I, Clause 3.4, Razorpay PA fees remain applicable and payable on each transaction even if you refund the customer. The clause applies whether the refund is made through normal refund channels or through Razorpay PA Group Entities' instant refund service, if used.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.4, REFUNDS

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clear_002`

- Selection reason(s): lowest_judge_score
- Planned category: `clear_answer`; judge predicted: `clear_answer`; judge score: 80/100
- User question: A card payment on our Razorpay PA integration was authorized late, and our system never captured it. Will Razorpay automatically refund the customer, and how soon could that happen?
- Assistant answer: Yes—Under Part B, Part I, Clause 3.5, for payments that are late authorized but not captured by you, Razorpay PA may initiate an auto-refund to the customer within five days. The clause says Razorpay PA may initiate the auto-refund, so it is framed as something Razorpay PA may do rather than as a merchant-initiated refund.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 3.5, REFUNDS

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clear_012`

- Selection reason(s): stratified_random_sample
- Planned category: `clear_answer`; judge predicted: `clear_answer`; judge score: 100/100
- User question: We are a marketplace based outside India planning to use Razorpay to collect payments from customers in India. Do the PA-CB outward transaction terms apply to us, and do they override the general terms if there is a conflict?
- Assistant answer: Yes—under Part B, Part IB, Clause 1.1, the Part IB terms apply to a merchant based outside India, including a marketplace, using Razorpay Services to collect funds from customers within India for PA-CB Outward Transactions. Clause 1.1 also says Part IB is supplementary to Part A and Part I, but if an express Part IB provision conflicts with or sets a different operational or regulatory requirement, Part IB prevails to the extent necessary for PA-CB Outward Transactions.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part IB Specific Terms Applicable to PA-CB Outward Transactions, Clause 1.1, PAYMENT PROCESSING

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clarification_001`

- Selection reason(s): lowest_judge_score
- Planned category: `clarification_required`; judge predicted: `clarification_required`; judge score: 74/100
- User question: One of our settled UPI payments has now been flagged by the bank as a fraudulent or unauthorised debit. Are we liable under Razorpay's terms, or will this be handled as a chargeback?
- Assistant answer: I need one key fact before choosing the correct ToS branch: has the fraudulent transaction actually resulted in a Chargeback? Under Part B, Part I, Clause 4.2, a post-settlement fraudulent transaction dispute is routed to the RBI-referenced framework, while under Part B, Part I, Clause 4.3, a fraudulent transaction that results in a Chargeback is resolved under the chargeback provisions in the Terms.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.2, FRAUDULENT TRANSACTIONS; Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.3, FRAUDULENT TRANSACTIONS; Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.5, FRAUDULENT TRANSACTIONS

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clarification_002`

- Selection reason(s): stratified_random_sample
- Planned category: `clarification_required`; judge predicted: `clarification_required`; judge score: 89/100
- User question: A cardholder has emailed us saying their payment instrument was debited without authorization, but I do not know whether the bank or payment network has formally notified Razorpay. Can Razorpay PA suspend our settlements while this is looked into?
- Assistant answer: Before answering, I need one key fact: has a Facility Provider intimated Razorpay PA that the customer reported the unauthorized debit? Under Part B, Part I, Clause 4.1, Razorpay PA's settlement-suspension right for a Fraudulent Transaction is triggered where Razorpay PA is intimated by a Facility Provider; Clause 4.1 also says this is in addition to Razorpay PA's rights under Part A, Clause 16.1.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.1, FRAUDULENT TRANSACTIONS; Part A GENERAL TERMS AND CONDITIONS, Clause 16.1, SUSPENSION AND TERMINATION

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clarification_011`

- Selection reason(s): stratified_random_sample
- Planned category: `clarification_required`; judge predicted: `clarification_required`; judge score: 96/100
- User question: For our new checkout flow, customer contact details and transaction metadata will pass through Razorpay. Do we need to obtain customer consents ourselves, or is Razorpay's Privacy Policy enough?
- Assistant answer: I need one fact before choosing the ToS branch: are these customer details or transaction data collected by your business from your customers and then transmitted to Razorpay PA, or are you only asking about Razorpay PA and its Facility Providers collecting, storing, processing, or transferring data as part of your use of the Services? Under Part A, Clause 12.1, use of the Services is governed by the Privacy Policy for Razorpay PA/Facility Provider processing, while under Part A, Clause 12.2, you remain responsible for Customer Data collected by you from your customers.
- Citations: Part A GENERAL TERMS AND CONDITIONS, Clause 12.1, DATA PROTECTION; Part A GENERAL TERMS AND CONDITIONS, Clause 12.2, DATA PROTECTION

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_clarification_014`

- Selection reason(s): lowest_judge_score
- Planned category: `clarification_required`; judge predicted: `clarification_required`; judge score: 74/100
- User question: We use Magic Checkout, and one COD order was returned to origin with return shipping fees charged to us. Can we claim reimbursement from Razorpay under RTO Protection?
- Assistant answer: I need one missing fact before applying the RTO Protection branch: were you availing RTO Protection as a paid service from Razorpay for this order? Under Part B, Part VI, Clause 2.1, the reimbursement entitlement is framed for cases where you are “availing the RTO Protection as a service from Razorpay for a consideration,” and Clause 2.4 says capitalised terms not defined in that section take their meaning from the Terms.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.1, RTO Protection; Part B SPECIFIC TERMS AND CONDITIONS, Part VI Magic Checkout, Clause 2.4, RTO Protection

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_ambiguity_004`

- Selection reason(s): stratified_random_sample
- Planned category: `genuine_ambiguity`; judge predicted: `genuine_ambiguity`; judge score: 95/100
- User question: We’re updating our checkout as a merchant CTO. Based on Razorpay’s Terms of Use, can you tell me the exact current Card Network operational rules we must implement this week, and whether they require any specific API, 3DS, or receipt changes?
- Assistant answer: The ToS does not provide the exact current Card Network operational rule content or the specific implementation changes you must make. Under Part A, General Terms and Conditions, Clause 9.1, Card Association Rules, the ToS says Card Networks may amend their guidelines, rules, and regulations, and Razorpay may need to amend the Terms as a result, with such amendments binding on you immediately. However, Clause 9.1 does not reproduce the external Card Payment Network Rules, identify which rules are currently in force, or specify operational requirements such as API, 3DS, receipt, or checkout changes. So the ToS flags that external rule changes may bind you, but it does not resolve what the current external rules require.
- Citations: Part A GENERAL TERMS AND CONDITIONS, Clause 9.1, CARD ASSOCIATION RULES

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

### Review: `rzp_ambiguity_005`

- Selection reason(s): stratified_random_sample
- Planned category: `genuine_ambiguity`; judge predicted: `genuine_ambiguity`; judge score: 100/100
- User question: A customer has reported an unauthorized debit, and Razorpay told us the Facility Provider is investigating. Since our settlements are currently suspended, do the Terms say the Facility Provider will decide the fraud investigation in our favor and release the settlements?
- Assistant answer: The Terms do not resolve that outcome. Under Part B, Part I, Clause 4.1, Razorpay PA is entitled to suspend settlements during the pendency of Facility Provider inquiries, investigations, and resolution. That clause explains Razorpay PA’s suspension right while the matter is pending, but it does not state how the Facility Provider must decide the investigation, whether the decision will be in the merchant’s favor, or that settlements must be released based on any particular investigation outcome. This is a genuine ToS gap, so the outcome should be confirmed with Razorpay PA or the relevant Facility Provider rather than inferred from the Terms.
- Citations: Part B SPECIFIC TERMS AND CONDITIONS, Part I Specific Terms For Payment Aggregation Services, Clause 4.1, FRAUDULENT TRANSACTIONS

Human verdicts:

- overall_verdict: _
- category_verdict: _
- groundedness_verdict: _
- citation_sufficiency_verdict: _
- notes: _

## Changes Since V1

V1 generated this dataset and evaluated it with a category-aware judge that shared the generator model and saw the planned label plus deterministic-check results. Feedback identified possible same-model bias/leakage and a lack of gold human checks and adversarial checks. V2 hardens the evaluation mechanism and re-evaluates the same, unchanged dataset:

- Blind judging: the judge no longer sees the planned category, deterministic-check results, or annotation fields, and must predict the category itself (addresses: eval robustness/leakage).
- Cross-model judging supported via --judge-model / OPENAI_JUDGE_MODEL; this run reused the generator model, so blind prompting is the active mitigation (same-model bias remains a caveat).
- Human review: a pipeline-selected sample with human verdicts, above (addresses: gold human checks).
- Adversarial pack: intentionally flawed rows with a per-layer catch-rate report (addresses: adversarial checks).

Because the v2 judge uses a different prompt and model, score deltas against v1 are directional rather than point-comparable. Failure-mode counts, label agreement, and the adversarial catch rate are the like-for-like evidence.

| Metric | V1 | V2 |
|---|---|---|
| Judge model | `gpt-5.5` | `gpt-5.5` |
| Judge prompt | `judge_v1_category_aware` | `judge_v2_blind` |
| Judge sees planned label / deterministic checks | yes | no |
| Cross-model judging | no | no |
| Mean score | 94.22 | 93.96 |
| Median score | 96 | 96 |
| Lowest score | 74 | 74 |
| Mean score (`clarification_required`) | 88.73 | 88.87 |
| Mean score (`clear_answer`) | 99 | 98.07 |
| Mean score (`genuine_ambiguity`) | 94.93 | 94.93 |
| Failure mode `low_business_value` | 0 | 1 |
| Failure mode `missed_ambiguity` | 1 | 0 |
| Failure mode `missed_clarification` | 3 | 1 |
| Failure mode `unsupported_claim` | 3 | 2 |
| Failure mode `weak_citation` | 5 | 4 |
| Failure mode `weak_source_selection` | 4 | 2 |
| Label agreement | not measured | 45/45 |

## Interpretation

The dataset is acceptable for submission with caveats. The evaluation is intentionally stricter than schema validation: it tests category fit, source grounding, citation sufficiency, usefulness, overreach, and realism. Because the judge mechanism changed in v2 (blind judging, optional cross-model), scores are not directly point-comparable to v1; failure modes, label agreement, and the adversarial catch rate are the like-for-like evidence.

## Future Improvements

- Run source-adequacy retrieval for all rows, not only the lowest-scoring rows.
- Move candidate source retrieval upstream into dataset generation so weak citations are caught before rows are written.
- Add a user-question realism rewrite pass for overly polished or synthetic prompts.
- Regenerate or patch rows that fail human review, then re-run this evaluation.
