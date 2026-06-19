# Razorpay ToS Synthetic QA Dataset

This repository builds a synthetic training dataset for a Razorpay Terms of Use Q&A assistant. The goal is to generate examples that teach an assistant when to answer directly, when to ask a clarifying question, and when the Terms are genuinely ambiguous.

The pipeline has three stages:

1. Fetch and parse a snapshot of the Razorpay Terms into a hierarchical clause index.
2. Generate 45 structured synthetic Q&A examples across the required categories.
3. Evaluate the dataset with deterministic validation plus an LLM-as-judge quality review.

The evaluation framework has two major versions. V1 (`data/eval/v1/`) is the baseline run. V2 (`data/eval/v2/`) re-evaluates the same, unchanged dataset with a hardened mechanism: neutral-ID blind judging, optional cross-model judging, a pipeline-selected human-review section, and an adversarial pack that tests whether each evaluation layer catches known-bad rows. The final documented eval run is `data/eval/v2/v2.3/`; future eval runs default to the next unused top-level `data/eval/vN/` directory unless `--output-dir` is provided.

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

Evaluation outputs (V1, baseline run):

- `data/eval/v1/eval_results.jsonl`: one LLM-as-judge result per dataset row.
- `data/eval/v1/worst_source_reviews.json`: deeper source-adequacy reviews for the lowest-scoring rows.
- `data/eval/v1/eval_summary.md`: human-readable evaluation summary.
- `data/eval/v1/eval_manifest.json`: evaluation metadata, judge settings, timestamps, source hash, and aggregate metrics.

Evaluation outputs (V2, hardened mechanism):

- `data/eval/v2/v2.0/`: first blind-judge v2 run, retained as an earlier iteration.
- `data/eval/v2/v2.3/eval_results.jsonl`, `data/eval/v2/v2.3/worst_source_reviews.json`, `data/eval/v2/v2.3/eval_summary.md`, `data/eval/v2/v2.3/eval_manifest.json`: the final documented neutral-ID blind-judge re-evaluation of the unchanged dataset. The summary contains Label Agreement, a compact Human Review audit queue, and an evaluation-hardening comparison against V1.
- `data/eval/v2/v2.3/adversarial_report.md`: standalone adversarial report for the handcrafted stress-test pack. The normal eval command produces this alongside the standard dataset-eval files by default; pass `--skip-adversarial` only when you want to suppress this extra report.

Handcrafted evaluation inputs:

- `data/eval/adversarial_pack.jsonl`: 8 human-authored, intentionally flawed rows with an `adversarial_metadata` field recording the planted flaw and the layer expected to catch it.

Gold mini-benchmark:

- `data/gold_bench/gold_cases.jsonl`: 9 curated hard user questions with hidden gold expectations for generator-facing regression checks.
- `data/gold_bench/v1/`: initial live baseline run.
- `data/gold_bench/v2/generated_rows.jsonl`: rows generated from the gold questions.
- `data/gold_bench/v2/gold_bench_results.jsonl`: per-case scoring against hidden gold expectations.
- `data/gold_bench/v2/gold_bench_summary.md`: human-readable benchmark summary with blocking failures, minor behavior feedback, score-band diagnostics, and suggested generator improvements.
- `data/gold_bench/v2/gold_bench_manifest.json`: benchmark provenance, hidden-field list, output paths, source hash, report version, and aggregate metrics.

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

Run the LLM-as-judge evaluation (V2, blind judge):

```bash
uv run python scripts/evaluate_dataset.py
```

This assumes these files exist:

- `data/output/razorpay_synthetic_qa.jsonl`
- `data/output/run_manifest.json`
- `data/output/generation_summary.md`
- `data/processed/clause_index.json`

This writes to the next unused top-level eval directory. With the current repository state, the default command writes to `data/eval/v3/` because `data/eval/v1/` and `data/eval/v2/` already exist. Existing non-empty output directories are not overwritten unless `--overwrite` is passed.

- `data/eval/vN/eval_results.jsonl`
- `data/eval/vN/worst_source_reviews.json`
- `data/eval/vN/eval_summary.md`
- `data/eval/vN/eval_manifest.json`
- `data/eval/vN/adversarial_report.md`

To intentionally write a specific new location, pass `--output-dir`:

```bash
uv run python scripts/evaluate_dataset.py --output-dir data/eval/v2/v2.4
```

To intentionally refresh an existing non-empty output directory, add `--overwrite`.

To judge with a different model than the generator (recommended to reduce same-model bias), pass `--judge-model` or set `OPENAI_JUDGE_MODEL`. The eval manifest records `generator_model`, `judge_model`, and a `cross_model_judging` flag, and the script warns when the judge matches the generator.

The normal eval command also runs `data/eval/adversarial_pack.jsonl` and writes the focused adversarial report into the same eval output directory.

To skip the adversarial pack for a main-dataset-only run:

```bash
uv run python scripts/evaluate_dataset.py --skip-adversarial
```

Run the generator-facing Gold Mini-Benchmark:

```bash
uv run python scripts/run_gold_bench.py
```

This uses `data/gold_bench/gold_cases.jsonl`, generates rows from question-only benchmark inputs plus question-retrieved ToS candidate sources, and writes to the next unused versioned output directory under `data/gold_bench/`.

For example, if `data/gold_bench/v1/` and `data/gold_bench/v2/` already exist, the default command writes to `data/gold_bench/v3/`. Existing non-empty output directories are not overwritten unless `--overwrite` is passed.

To write a specific location:

```bash
uv run python scripts/run_gold_bench.py --output-dir data/gold_bench/v3
```

To intentionally refresh an existing output directory:

```bash
uv run python scripts/run_gold_bench.py --output-dir data/gold_bench/v3 --overwrite
```

V1 is retained as the first live baseline; V2 keeps the same benchmark concept but improves the reporting layer so lower-scoring passing rows become useful diagnostic signals instead of being hidden behind the pass count.

The first live gold run showed that prompt-visible case IDs such as `gold_clear_001`, `gold_clarification_002`, and `gold_ambiguity_003` carried the expected category in the name. The runner now assigns neutral generated-row IDs such as `bench_case_001` and keeps the original curated gold IDs as scoring/audit metadata only. With neutral IDs, category-selection and source-selection misses show up as benchmark failures instead of being masked, which gives clearer targets for future generator iterations.

For a deterministic local smoke run without an API key:

```bash
uv run python scripts/run_gold_bench.py --template-only
```

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
- Judge calls are recorded with `judge_model`, `generator_model`, `cross_model_judging`, `judge_temperature`, `judge_prompt_version`, `judge_run_id`, timestamps, source hash, structured-output validation status, fallback failure count, and aggregate metrics in the run's `eval_manifest.json` (`data/eval/v1/` for v1, `data/eval/v2/v2.3/` for the final documented v2 run).
- The human-review audit queue uses a seeded positive-control sample (`seed 42`), so perfect-score rows selected for review are reproducible across re-runs with the same eval results.
- The selected GPT-5.5 endpoint does not expose an explicit temperature parameter through the provider interface used for this run, so the eval records `judge_temperature: null` and `judge_temperature_note: provider default`.

Gold Mini-Benchmark determinism:

- The curated gold cases, hidden expectations, pass rule, source hash, schema validation, and scoring dimensions are deterministic.
- The live LLM-generated rows are not expected to be byte-identical across runs.
- `--template-only` uses local question-only rules for a deterministic smoke run and records `generator_model: template-question-rules` in the manifest.
- The generator prompt receives no gold case ID, `expected_category`, `reference_answer`, `required_clause_ids`, `optional_clause_ids`, `must_have_behaviors`, `forbidden_claims`, or `boundary_focus`; the manifest records this hidden-field list and the neutral ID policy.

## Evaluation Design

The evaluation has two layers.

First, deterministic validation checks every row for:

- schema validity
- source clause resolution
- quote containment
- visible assistant citations
- category-specific field shape
- generation-quality lint warnings

Second, an LLM judge scores every row out of 100. In V2 the judge is blind: it sees only the user question, the assistant answer, the full cited clause text, and a neutral prompt row ID such as `judge_row_001`. It does not see the planned category label, category-bearing dataset/adversarial row ID, support roles, deterministic-check results, or the structured annotation fields (those are recorded next to the judgment in `eval_results.jsonl` as audit metadata). The judge first predicts which category the situation truly calls for, then scores the row against that prediction using one shared rubric with per-category behavior rules. Judge-vs-label agreement is reported as a metric, and every disagreement is routed into the human-review section.

V2 adds two checks on the evaluation framework itself:

- A Human Review section in `eval_summary.md`: the pipeline now produces a compact audit queue rather than a blank worksheet. It selects rows using a risk-first strategy: lowest judge scores, label disagreements, deterministic/schema failures, source-review insufficiency, capped judge/lint flags, category-coverage backfill, and a seeded sample of perfect-score rows as positive controls. The section lists only the row ID, planned-vs-predicted category, score, reason for review, and cited clauses to inspect.
- An adversarial pack (`data/eval/adversarial_pack.jsonl`): 8 hand-authored rows, each with exactly one planted flaw and an expected catching layer. Three rows violate hard constraints (fabricated quote, unknown clause ID, category-shape violation) and should be caught deterministically; two are schema-valid but weak (vague clarifying question, thin section-heading source) and should be caught by quality lints; three are clean at both lower layers (manufactured ambiguity over a clause that clearly answers, an invented 24-hour refund guarantee, an invented 180-day fund-hold maximum) and can only be caught by the blind judge. The adversarial result is documented in a focused standalone report produced alongside the normal dataset-eval outputs by default.

The Gold Mini-Benchmark is a separate generator-facing hard-case benchmark, not an eval-hardening check. This first version has 9 intentionally difficult Razorpay questions and is meant to seed future phases that add the toughest questions and previous pipeline failure modes. The `v2` run passed `6/9` cases; that is useful signal because the goal is to learn from misses, replicate failure modes, and use them to strengthen the pipeline around category selection, multi-branch clarification behavior, ambiguity next steps, and source retrieval. Each case passes only if the score is at least 80, the generated category matches the hidden expected category, no forbidden claims appear, and at least one required clause is cited.

The gold summary separates blocking failures from non-blocking improvement signals. Rows that pass the strict gate can still appear under Minor Behavior Feedback when they miss part of a hidden expected behavior, and the summary calls out the weakest case (`bench_case_005` at `67/100`) as a concrete generator improvement target.

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

## How To Interpret The Evaluation Artifacts

- Main eval (`eval_results.jsonl`, `eval_summary.md`, `eval_manifest.json`): scores the frozen 45-row dataset with deterministic checks plus the neutral-ID blind judge.
- Human-review queue (`eval_summary.md`): selects the rows a reviewer should inspect first, including label disagreements, low scores, source-review insufficiency, judge/lint flags, and seeded positive controls.
- Adversarial pack (`adversarial_report.md`): tests the evaluation stack itself, not the quality of the generated dataset; it validates that deterministic checks, quality lints, and the judge each catch the kinds of failures they are meant to catch.
- Gold mini-benchmark (`data/gold_bench/v2/`): pressure-tests the generator on known-hard questions and turns misses into a seed set for future hardening cases and pipeline failure-mode replication.

## Evaluation Results

The V1 eval run (category-aware judge) produced a mean of `94.22/100`, median `96/100`, lowest `74/100`, with zero judge fallback failures. The final documented V2.3 neutral-ID blind re-evaluation of the same frozen dataset produced:

- Mean score: `93.04/100`
- Median score: `96/100`
- Lowest score: `51/100`
- Highest score: `100/100`
- Judge fallback failures: `0`
- Label agreement (blind judge prediction vs planned category): `43/45 (95.6%)`

What the final run taught us: the stricter neutral-ID blind judge surfaced two clarification-vs-ambiguity/source-selection issues (`rzp_clarification_001` and `rzp_clarification_007`). That is a positive signal: the final iteration did not improve scores by regenerating examples; it kept the 45-row dataset frozen and made the evaluation more trustworthy around it, exposing real failure modes instead of preserving a higher headline score.

By category (V2):


| Category                 | Mean score | Lowest score | Rows |
| ------------------------ | ---------- | ------------ | ---- |
| `clear_answer`           | 98.53      | 88           | 15   |
| `clarification_required` | 85.53      | 51           | 15   |
| `genuine_ambiguity`      | 95.07      | 87           | 15   |


The most common failure modes were `weak_citation`, `weak_source_selection`, `wrong_category`, `missed_ambiguity`, and `low_business_value`, consistent with the final run's emphasis on source-selection and category-boundary review.

Adversarial catch rate: the eval framework caught `8/8` planted flaws, and every flaw was caught by the layer designed to catch it. The three judge-only rows (clean schema, clean lints) scored 17-36/100 with correct failure-mode flags and category disagreements. Full table in `data/eval/v2/v2.3/adversarial_report.md`.

The lowest-scoring rows are documented in `data/eval/v2/v2.3/eval_summary.md`, with approximately 50-word diagnoses explaining what failed, where in the pipeline the issue likely came from, and what rubric or code change would catch it.

## Evaluation Hardening Summary


| Evaluation Hardening Area        | Final State                                                                                                                                                                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Prompt-visible judge signals     | Neutral-ID blind judging withholds planned label, original row ID, support roles, deterministic checks, and annotation fields; judge predicts the category itself (`judge_v2_blind_neutral_ids`)             |
| Same-model evaluation caveat     | Cross-model judging supported via `--judge-model` / `OPENAI_JUDGE_MODEL`; provenance (`generator_model`, `judge_model`, `cross_model_judging`) recorded in the eval manifest; same-model runs emit a warning |
| Human review                     | Human Review audit queue in `eval_summary.md` with pipeline-selected rows, review reasons, and cited clauses to inspect                                                                                      |
| Adversarial stack check          | `data/eval/adversarial_pack.jsonl` runs by default with the normal eval command and writes a focused `adversarial_report.md` with per-layer catch-rate evidence                                              |
| Output reproducibility           | Eval outputs default to the next unused top-level `data/eval/vN/`; older artifacts such as `data/eval/v1/`, `data/eval/v2/v2.0/`, and `data/eval/v2/v2.3/` remain inspectable                                |


The generated dataset (`data/output/razorpay_synthetic_qa.jsonl`) is unchanged; V2 only hardens the evaluation mechanism around it.

## Notes On Quality Gates

Generation-time quality checks run in `warn_only` mode. They are intentionally advisory: they surface source quality, category fit, citation, quote, and known-fact issues for review, but they do not block V1 dataset creation. Final quality assessment happens in the LLM-as-judge evaluation.

## Future Improvements

The main future improvement would be to move source-adequacy retrieval upstream into dataset generation for every row. In this version, broader source review is only run on the lowest-scoring examples. Parent/child/sibling and taxonomy-matched candidate clauses could be retrieved before finalizing each generated example, reducing weak citation and source-selection failures earlier in the pipeline.

Other future improvements:

- Add a user-question realism rewrite pass.
- Add a close-call ambiguity filter so ambiguity examples are less obvious and more representative of real fintech questions.
- Expand the adversarial pack with near-miss cases (subtly wrong clause of the right section, partially supported claims) to probe judge sensitivity further.
- Feed completed human-review verdicts back into the pipeline to regenerate or patch rejected rows.

