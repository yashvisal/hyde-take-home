# Razorpay ToS Synthetic QA Dataset

This repository builds a synthetic training dataset for a Razorpay Terms of Use Q&A assistant. The goal is to generate examples that teach an assistant when to answer directly, when to ask a clarifying question, and when the Terms are genuinely ambiguous.

The pipeline has three stages:

1. Fetch and parse a snapshot of the Razorpay Terms into a hierarchical clause index.
2. Generate 45 structured synthetic Q&A examples across the required categories.
3. Evaluate the dataset with deterministic validation plus an LLM-as-judge quality review.

## Dataset Categories

The generated dataset contains 45 examples, with 15 examples per category:

- `clear_answer`: The ToS explicitly answers the question. The assistant should answer directly and cite the relevant clause.
- `clarification_required`: The ToS provides a conditional answer, but the user omitted a fact needed to answer safely. The assistant should ask a targeted clarifying question and explain what would change.
- `genuine_ambiguity`: The ToS is silent, vague, discretionary, or dependent on external regulation. The assistant should flag the uncertainty and avoid guessing.

## Key Outputs

Generation outputs:

- `data/processed/clause_index.json`: hierarchical clause index built from the Razorpay Terms snapshot.
- `data/output/razorpay_synthetic_qa.jsonl`: 45 generated Q&A examples.
- `data/output/run_manifest.json`: generation metadata, source hash, seeds, model settings, and determinism notes.
- `data/output/generation_summary.md`: generation summary, coverage, quality lint flags, and validation status.

Evaluation outputs:

- `data/eval/eval_results.jsonl`: one LLM-as-judge result per dataset row.
- `data/eval/worst_source_reviews.json`: deeper source-adequacy reviews for the lowest-scoring rows.
- `data/eval/eval_summary.md`: human-readable evaluation summary.
- `data/eval/eval_manifest.json`: evaluation metadata, judge settings, timestamps, source hash, and aggregate metrics.

## Schema Overview

Each JSONL row includes:

- `id`: stable row identifier.
- `category`: one of `clear_answer`, `clarification_required`, or `genuine_ambiguity`.
- `messages`: user question and assistant answer.
- `source_clauses`: cited clause IDs, display citations, support roles, and relevant quotes.
- `known_facts`: facts supplied by the user or inferred from the question; especially useful for clarification and ambiguity rows.
- `missing_facts`: facts needed for `clarification_required` examples.
- `clarifying_questions`: targeted questions for `clarification_required` examples.
- `conditional_outcomes`: how the answer changes depending on missing facts in `clarification_required` examples.
- `ambiguity_reason`: explanation of the ToS gap for `genuine_ambiguity` examples.
- `coverage_metadata`: service area, topic tags, and source-section coverage metadata.
- `generation_metadata`: model, prompt version, source hash, and generation traceability fields.

The schema is category-aware. All rows share the same top-level structure for consistency, but different fields are populated depending on the intended assistant behavior:

- `clear_answer` rows rely primarily on `messages` and `source_clauses`. They should have enough cited source support for the assistant to answer directly, without requiring `missing_facts`, `clarifying_questions`, or `ambiguity_reason`.
- `clarification_required` rows include `known_facts`, `missing_facts`, `clarifying_questions`, and `conditional_outcomes`. These fields encode the specific missing context, the targeted question the assistant should ask, and how the answer would change depending on the user's response.
- `genuine_ambiguity` rows include an `ambiguity_reason` explaining why the ToS does not fully resolve the question, such as silence, undefined terms, Razorpay discretion, or dependency on external rules.

This structure makes the dataset useful for training and evaluating category-specific behavior, not just factual citation. It preserves source traceability while also encoding when the assistant should answer, ask for context, or acknowledge ambiguity.

## Setup

The pipeline uses Python standard-library code only.

For LLM-backed generation or evaluation, set:

```bash
export OPENAI_API_KEY="..."
```

Optional model overrides:

```bash
export OPENAI_MODEL="gpt-5.5"
export OPENAI_JUDGE_MODEL="gpt-5.5"
```

The scripts also read `.env` and `.env.local` if present.

## Run The Pipeline

Each stage consumes artifacts from the previous stage. If those files already exist, you can rerun later stages independently. For example, dataset generation can use the existing `data/processed/clause_index.json`, and evaluation can use the existing `data/output/razorpay_synthetic_qa.jsonl`.

Fetch and parse the Razorpay Terms:

```bash
uv run python scripts/ingest_razorpay_terms.py
```

This writes:

- `data/raw/razorpay_terms.html`
- `data/raw/razorpay_terms_snapshot.json`
- `data/processed/razorpay_terms_clean.txt`
- `data/processed/clause_index.json`
- `data/processed/parser_validation_report.json`

Generate the dataset with the LLM:

```bash
uv run python scripts/generate_dataset.py --quality-gate
```

This assumes `data/processed/clause_index.json` exists.

This writes:

- `data/output/razorpay_synthetic_qa.jsonl`
- `data/output/run_manifest.json`
- `data/output/generation_summary.md`

Run the LLM-as-judge evaluation:

```bash
uv run python scripts/evaluate_dataset.py
```

This assumes these files exist:

- `data/output/razorpay_synthetic_qa.jsonl`
- `data/output/run_manifest.json`
- `data/output/generation_summary.md`
- `data/processed/clause_index.json`

This writes:

- `data/eval/eval_results.jsonl`
- `data/eval/worst_source_reviews.json`
- `data/eval/eval_summary.md`
- `data/eval/eval_manifest.json`

## Determinism And Reproducibility

Deterministic generation components:

- Coverage plan.
- Row IDs.
- Source record selection.
- Parser-owned metadata and source traceability fields.
- Schema validation.
- Final row ordering.

Non-deterministic generation components:

- LLM-generated user wording.
- LLM-generated assistant wording.
- LLM-generated known facts.
- LLM-generated missing facts.
- LLM-generated clarifying questions.
- LLM-generated conditional outcomes.
- LLM-generated ambiguity explanations.
- LLM-selected support roles and quotes.
- Live Razorpay Terms content if `scripts/ingest_razorpay_terms.py` fetches a fresh copy.

Further reproducibility runs reproduce the structural guarantees: source hash, clause index, row count, 15/15/15 category balance, validation success, and similar quality-lint behavior. LLM-authored wording is not expected to be identical across runs.

Evaluation determinism:

- Deterministic checks use `src/schemas.py` and `src/generation_quality.py`.
- Judge calls are recorded with `judge_model`, `judge_temperature`, `judge_prompt_version`, `judge_run_id`, timestamps, source hash, structured-output validation status, fallback failure count, and aggregate metrics in `data/eval/eval_manifest.json`.
- The selected GPT-5.5 endpoint does not expose an explicit temperature parameter through the provider interface used for this run, so the eval records `judge_temperature: null` and `judge_temperature_note: provider default`.

## Evaluation Design

The evaluation has two layers.

First, deterministic validation checks every row for:

- schema validity
- source clause resolution
- quote containment
- visible assistant citations
- category-specific field shape
- generation-quality lint warnings

Second, an LLM judge scores every row out of 100 using only the generated row and its cited clauses. The score uses one shared rubric for comparability, but the judge prompt is category-aware: `clear_answer`, `clarification_required`, and `genuine_ambiguity` rows each have their own expected behavior and deduction rules.

The scoring rubric is:


| Dimension                   | Points |
| --------------------------- | ------ |
| Category fit                | 25     |
| Groundedness                | 25     |
| Citation/source sufficiency | 15     |
| Answer usefulness           | 10     |
| No overreach                | 10     |
| Category-specific behavior  | 10     |
| User-question realism       | 5      |
| Total                       | 100    |


For the lowest-scoring rows, the eval runs a deeper source-adequacy review using the hierarchical clause index. This review considers originally cited clauses, parent/child/sibling clauses, taxonomy-matched clauses, and keyword-matched candidate clauses. It is targeted rather than exhaustive, which keeps evaluation cost controlled while still testing whether a stronger source clause should have been used.

## Evaluation Results

The submitted eval run produced:

- Mean score: `94.22/100`
- Median score: `96/100`
- Lowest score: `74/100`
- Highest score: `100/100`
- Judge fallback failures: `0`

Most rows scored highly because the generated dataset had already passed deterministic schema, citation, quote-containment, and category-field validation before semantic judging. The LLM judge was most useful for surfacing subtler source-selection and category-boundary issues.

By category:


| Category                 | Mean score | Lowest score | Rows |
| ------------------------ | ---------- | ------------ | ---- |
| `clear_answer`           | 99         | 92           | 15   |
| `clarification_required` | 88.73      | 74           | 15   |
| `genuine_ambiguity`      | 94.93      | 79           | 15   |


The most common failure modes were `weak_citation`, `weak_source_selection`, `unsupported_claim`, `missed_clarification`, and `missed_ambiguity`.

The lowest-scoring rows are documented in `data/eval/eval_summary.md`, with approximately 50-word diagnoses explaining what failed, where in the pipeline the issue likely came from, and what rubric or code change would catch it.

## Notes On Quality Gates

Generation-time quality checks run in `warn_only` mode. They are intentionally advisory: they surface source quality, category fit, citation, quote, and known-fact issues for review, but they do not block V1 dataset creation. Final quality assessment happens in the LLM-as-judge evaluation.

## Future Improvements

The main future improvement would be to move source-adequacy retrieval upstream into dataset generation for every row. In this version, broader source review is only run on the lowest-scoring examples. Parent/child/sibling and taxonomy-matched candidate clauses could be retrieved before finalizing each generated example, reducing weak citation and source-selection failures earlier in the pipeline.

Other future improvements:

- Add a user-question realism rewrite pass.
- Add an adversarial category-fit check for close calls between clarification-required and genuine-ambiguity examples.
- Add a close-call ambiguity filter so ambiguity examples are less obvious and more representative of real fintech questions.
- Add a small human calibration set to compare judge scores against expert review.

