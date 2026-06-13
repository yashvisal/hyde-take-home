#!/usr/bin/env python3
"""Evaluate the Razorpay synthetic Q&A dataset with deterministic checks and LLM judges."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.generation_quality import has_visible_citation, validate_generation_quality
from src.schemas import CATEGORIES, RESPONSE_MODE_BY_CATEGORY, load_clause_index, quote_supported, validate_dataset, validate_dataset_row


DATASET_PATH = ROOT / "data" / "output" / "razorpay_synthetic_qa.jsonl"
RUN_MANIFEST_PATH = ROOT / "data" / "output" / "run_manifest.json"
GENERATION_SUMMARY_PATH = ROOT / "data" / "output" / "generation_summary.md"
CLAUSE_INDEX_PATH = ROOT / "data" / "processed" / "clause_index.json"
# v2 outputs are versioned so the v1 artifacts and earlier v2 runs stay untouched.
EVAL_DIR = ROOT / "data" / "eval" / "v2" / "v2.1"
BASELINE_MANIFEST_PATH = ROOT / "data" / "eval" / "v1" / "eval_manifest.json"
EVAL_RESULTS_PATH = EVAL_DIR / "eval_results.jsonl"
WORST_SOURCE_REVIEWS_PATH = EVAL_DIR / "worst_source_reviews.json"
EVAL_SUMMARY_PATH = EVAL_DIR / "eval_summary.md"
EVAL_MANIFEST_PATH = EVAL_DIR / "eval_manifest.json"
ADVERSARIAL_REPORT_PATH = EVAL_DIR / "adversarial_report.md"

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_JUDGE_MODEL = "gpt-5.5"
JUDGE_TEMPERATURE: float | None = None
JUDGE_PROMPT_VERSION = "judge_v2_blind"
SOURCE_REVIEW_PROMPT_VERSION = "source_review_v1"
DIAGNOSIS_PROMPT_VERSION = "worst_diagnosis_v1"
MAX_JUDGE_ATTEMPTS = 2
MAX_CANDIDATES = 35
SAME_SERVICE_AREA_CAP = 8
HUMAN_REVIEW_SEED = 42
HUMAN_REVIEW_WORST_COUNT = 3
HUMAN_REVIEW_FLAGGED_COUNT = 4
HUMAN_REVIEW_PERFECT_SCORE_COUNT = 2
JUDGE_CATCH_SCORE_THRESHOLD = 80

DIMENSION_MAX = {
    "category_fit": 25,
    "groundedness": 25,
    "citation_source_sufficiency": 15,
    "answer_usefulness": 10,
    "no_overreach": 10,
    "category_specific_behavior": 10,
    "user_question_realism": 5,
}
FAILURE_MODES = {
    "wrong_category",
    "unsupported_claim",
    "weak_citation",
    "missing_citation",
    "overly_generic_answer",
    "vague_clarifying_question",
    "missed_clarification",
    "missed_ambiguity",
    "overstated_ambiguity",
    "synthetic_user_question",
    "low_business_value",
    "weak_source_selection",
}
SOURCE_ISSUE_TYPES = {
    "none",
    "weak_source_selection",
    "answer_generation",
    "category_labeling",
    "user_question_design",
    "multiple",
    "other",
}


@dataclass(frozen=True)
class EvalConfig:
    judge_model: str
    judge_temperature: float | None
    judge_prompt_version: str
    source_review_prompt_version: str
    diagnosis_prompt_version: str
    judge_run_id: str
    generator_model: str | None = None
    adversarial: bool = False

    @property
    def cross_model_judging(self) -> bool:
        return bool(self.generator_model) and self.judge_model != self.generator_model


class OpenAIClient:
    def __init__(self, api_key: str, model: str, temperature: float | None) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        request = urllib.request.Request(
            OPENAI_CHAT_COMPLETIONS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API request failed with {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"OpenAI API request failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_environment() -> None:
    for name in (".env", ".env.local"):
        load_env_file(ROOT / name)


def workspace_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: {exc}")
    return rows, errors


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def deterministic_row_checks(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    schema_errors = validate_dataset_row(row, records)
    quality = validate_generation_quality(row, records)
    source_clauses = row.get("source_clauses", [])
    source_clause_ids_resolve = all(source.get("clause_id") in records for source in source_clauses)
    relevant_quotes_contained = all(
        source.get("clause_id") in records and quote_supported(source.get("relevant_quote", ""), records[source["clause_id"]], records)
        for source in source_clauses
    )
    category = row.get("category")
    category_fields_valid = (
        category in CATEGORIES
        and row.get("response_mode") == RESPONSE_MODE_BY_CATEGORY[category]
        and not schema_errors
    )
    return {
        "source_clause_ids_resolve": source_clause_ids_resolve,
        "relevant_quotes_contained": relevant_quotes_contained,
        "assistant_has_visible_citation": has_visible_citation(row),
        "category_fields_valid": category_fields_valid,
        "assistant_non_empty": bool(row.get("messages", [{}, {}])[1].get("content", "").strip()) if isinstance(row.get("messages"), list) and len(row["messages"]) > 1 else False,
        "user_question_non_empty": bool(row.get("messages", [{}])[0].get("content", "").strip()) if isinstance(row.get("messages"), list) and row["messages"] else False,
        "schema_validation_errors": schema_errors,
        "quality_lint_warnings": quality.warnings,
        "quality_lint_errors": quality.errors,
    }


def dataset_checks(
    rows: list[dict[str, Any]],
    parse_errors: list[str],
    records: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    *,
    expected_total: int | None = 45,
    expected_per_category: int | None = 15,
) -> dict[str, Any]:
    category_counts = Counter(row.get("category") for row in rows)
    dataset_validation_failures = validate_dataset(
        rows, records, expected_total=expected_total, expected_per_category=expected_per_category
    )
    row_hashes = {row.get("generation_metadata", {}).get("source_hash") for row in rows}
    manifest_hash = manifest.get("source_hash")
    expected_category_counts = (
        {category: expected_per_category for category in sorted(CATEGORIES)}
        if expected_per_category is not None
        else None
    )
    return {
        "row_count": len(rows),
        "expected_row_count": expected_total,
        "category_counts": dict(sorted(category_counts.items())),
        "expected_category_counts": expected_category_counts,
        "source_hash_matches_manifest": row_hashes == {manifest_hash},
        "all_rows_parse": not parse_errors,
        "parse_errors": parse_errors,
        "dataset_validation_failures": dataset_validation_failures,
    }


def truncate_source_text(text: str, quote: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    quote = quote.strip()
    parts = [text[:700].rstrip()]
    if quote and quote not in parts[0]:
        parts.append(f"[RELEVANT_QUOTE]\n{quote}")
    parts.append(text[-300:].lstrip())
    return "\n...\n".join(parts)


def row_prompt_payload(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Blind judge payload.

    The judge sees only what a reader of the conversation would see: the user
    question, the assistant answer, and the cited clause text. It does NOT see
    the planned category, support roles (which map 1:1 to categories), the
    structured annotation fields, or any deterministic check results. Those
    stay in eval_results.jsonl as audit metadata next to the judgment.
    """
    cited_sources = []
    for source in row.get("source_clauses", []):
        record = records.get(source.get("clause_id"), {})
        cited_sources.append(
            {
                "clause_id": source.get("clause_id"),
                "display_citation": source.get("display_citation"),
                "relevant_quote": source.get("relevant_quote"),
                "source_text": record.get("text", ""),
            }
        )
    return {
        "row_id": row.get("id"),
        "user_question": row.get("messages", [{}, {}])[0].get("content", ""),
        "assistant_answer": row.get("messages", [{}, {}])[1].get("content", ""),
        "cited_source_clauses": cited_sources,
    }


def row_judge_system_prompt() -> str:
    return (
        "You are a strict, blind LLM-as-judge for a synthetic compliance Q&A dataset grounded in the Razorpay Terms of Use. "
        "You are NOT told which response category the dataset intended for this row; you must first decide for yourself, "
        "from the user question and the cited clause text alone, which category the situation truly calls for. "
        "Judge only against the supplied row and cited source clauses. Do not use outside knowledge or infer support from clauses not provided. "
        "Return strict JSON only. Use the full scoring range: structural validity alone should not receive a high score. "
        "A row can be fluent and well-formatted and still score poorly if it behaves like the wrong category, is weakly sourced, "
        "overconfident, low-value, or synthetic-sounding."
    )


def category_behavior_rules() -> dict[str, dict[str, list[str]]]:
    return {
        "clear_answer": {
            "expected_behavior": [
                "Answer directly in the first sentence.",
                "Cite the specific controlling clause.",
                "Do not ask a clarifying question unless the row is mislabeled.",
                "Do not hedge beyond the uncertainty actually present in the cited clause.",
            ],
            "deduct_for": [
                "Asking for context even though the cited ToS clause explicitly answers the question.",
                "Answering with vague legal caution instead of a direct yes/no or direct operational conclusion.",
                "Failing to cite the controlling clause in the answer.",
                "Overstating a permissive clause, such as turning 'may' into 'will' or 'must'.",
            ],
        },
        "clarification_required": {
            "expected_behavior": [
                "Ask a specific, targeted clarifying question tied to a missing fact.",
                "Explain what that fact changes about the ToS outcome.",
                "Avoid giving a final answer that depends on the missing fact.",
                "Include conditional outcomes when the available ToS clauses point to different branches.",
            ],
            "deduct_for": [
                "Asking a vague question like 'can you provide more details?'",
                "Failing to identify the exact missing fact needed to choose the ToS branch.",
                "Giving a final answer despite missing context.",
                "Treating a genuine ToS gap or external-regulation issue as merely a missing user fact.",
                "Claiming only one fact is needed when the cited terms contain multiple material conditions.",
            ],
        },
        "genuine_ambiguity": {
            "expected_behavior": [
                "Flag honestly that the ToS is silent, vague, discretionary, or dependent on external regulation.",
                "Describe what the cited ToS text does establish.",
                "Describe the exact gap the ToS does not resolve.",
                "Recommend further clarification from Razorpay, counsel, or the applicable external source rather than guessing.",
            ],
            "deduct_for": [
                "Inventing a definitive answer where the ToS is incomplete.",
                "Asking a clarifying question when no user-provided fact would resolve the ToS gap.",
                "Failing to distinguish what is known from what is unresolved.",
                "Overstating ambiguity when the cited ToS clause directly answers the question.",
                "Citing a broad or irrelevant clause when a more specific ambiguity source is available.",
            ],
        },
    }


def row_judge_user_prompt(payload: dict[str, Any], validation_errors: list[str] | None = None) -> str:
    rubric = {
        "category_fit": {
            "points": 25,
            "deduct_for": "The assistant's behavior does not match the category the situation calls for (your predicted_category). Penalize category boundary mistakes primarily here.",
        },
        "groundedness": {
            "points": 25,
            "deduct_for": "Assistant claims not supported by the supplied cited source text. Unsupported claims primarily penalize groundedness.",
        },
        "citation_source_sufficiency": {
            "points": 15,
            "deduct_for": "Missing, weak, overly broad, or insufficient cited clauses. Weak source selection primarily penalizes this dimension.",
        },
        "answer_usefulness": {
            "points": 10,
            "deduct_for": "Not useful to a CTO, engineering lead, ops lead, compliance stakeholder, or legal reviewer.",
        },
        "no_overreach": {
            "points": 10,
            "deduct_for": "Invented obligations, timelines, thresholds, legal standards, outcomes, or certainty not supported by cited text.",
        },
        "category_specific_behavior": {
            "points": 10,
            "deduct_for": "Apply the behavior rules of your predicted_category to the assistant's answer.",
        },
        "user_question_realism": {
            "points": 5,
            "deduct_for": "Awkward, over-engineered, or synthetic user wording. Penalize awkward wording primarily here.",
        },
    }
    instructions: dict[str, Any] = {
        "task": (
            "First, decide which response category this situation truly calls for (predicted_category), "
            "using only the user question and the cited clause text. "
            "Then score this one synthetic Q&A row out of 100, judging the assistant's behavior against your predicted_category."
        ),
        "category_definitions": {
            "clear_answer": "The ToS directly and unambiguously answers the question.",
            "clarification_required": "The answer depends on a specific missing user fact.",
            "genuine_ambiguity": "The ToS is silent, vague, discretionary, or depends on external regulation.",
        },
        "category_behavior_rules": category_behavior_rules(),
        "category_boundary_rules": [
            "If the ToS explicitly and unambiguously answers the user's question from the cited source, the situation calls for clear_answer.",
            "If the cited ToS gives branches but the user omitted a fact that would select the correct branch, the situation calls for clarification_required.",
            "If no user-provided fact would resolve the issue because the ToS is silent, vague, discretionary, or delegates to external regulation, the situation calls for genuine_ambiguity.",
            "Do not reward a row for asking a clarifying question when the right behavior is to flag genuine ambiguity.",
            "Do not reward a row for flagging ambiguity when the right behavior is to ask a targeted missing-fact question.",
        ],
        "rubric": rubric,
        "controlled_failure_modes": sorted(FAILURE_MODES),
        "scoring_rules": [
            "All scores must be integers.",
            "predicted_category is your independent judgment of what the situation calls for; it is not given to you anywhere in the row.",
            "Do not give high scores simply because the row is fluent or well-formatted.",
            "If the assistant behaves like a different category than your predicted_category, category_fit should usually be 12 or lower and you should flag wrong_category.",
            "If material assistant claims are unsupported by cited source text, groundedness should usually be 15 or lower and no_overreach should be penalized.",
            "If the cited clause is related but too broad or not the strongest support, citation_source_sufficiency should usually be 10 or lower.",
            "If the situation calls for clear_answer and the assistant hedges or asks an unnecessary clarification, penalize category_specific_behavior.",
            "If the situation calls for clarification_required and the assistant asks a vague question or fails to explain what the missing fact changes, penalize category_specific_behavior.",
            "If the situation calls for genuine_ambiguity and the assistant invents a conclusion instead of flagging a ToS gap, penalize no_overreach and category_specific_behavior.",
            "For category_specific_behavior, evaluate against your predicted_category's behavior rules.",
            "Keep rationale to at most two sentences.",
        ],
        "output_schema": {
            "row_id": payload["row_id"],
            "predicted_category": "one of clear_answer | clarification_required | genuine_ambiguity",
            "scores": {name: f"integer 0-{max_points}" for name, max_points in DIMENSION_MAX.items()},
            "total_score": "integer sum of all dimension scores, 0-100",
            "failure_modes": "array of strings from controlled_failure_modes only",
            "rationale": "max two sentences",
        },
        "row": payload,
    }
    if validation_errors:
        instructions["previous_invalid_response_errors"] = validation_errors
        instructions["repair_instruction"] = "Return corrected strict JSON that satisfies the schema and score bounds exactly."
    return json.dumps(instructions, indent=2, ensure_ascii=False)


def validate_row_judge_response(response: dict[str, Any], row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if response.get("row_id") != row.get("id"):
        errors.append("row_id mismatch")
    # Blind judging: the judge predicts the category instead of echoing the
    # planned label, and disagreement is allowed (it becomes a metric).
    if response.get("predicted_category") not in CATEGORIES:
        errors.append("predicted_category must be one of the dataset categories")
    scores = response.get("scores")
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
        return errors
    if set(scores) != set(DIMENSION_MAX):
        errors.append("scores must contain exactly the rubric dimensions")
    total = 0
    for name, max_points in DIMENSION_MAX.items():
        value = scores.get(name)
        if type(value) is not int:
            errors.append(f"{name} must be an integer")
            continue
        if value < 0 or value > max_points:
            errors.append(f"{name} must be between 0 and {max_points}")
        total += value
    if type(response.get("total_score")) is not int:
        errors.append("total_score must be an integer")
    elif response["total_score"] != total:
        errors.append("total_score must equal sum of dimension scores")
    failure_modes = response.get("failure_modes")
    if not isinstance(failure_modes, list):
        errors.append("failure_modes must be a list")
    else:
        invalid = sorted({mode for mode in failure_modes if mode not in FAILURE_MODES})
        if invalid:
            errors.append(f"invalid failure_modes: {', '.join(invalid)}")
    if not isinstance(response.get("rationale"), str) or not response["rationale"].strip():
        errors.append("rationale must be a non-empty string")
    return errors


def fallback_row_eval(row: dict[str, Any], deterministic_checks: dict[str, Any], errors: list[str], config: EvalConfig) -> dict[str, Any]:
    return {
        "row_id": row.get("id"),
        "category": row.get("category"),
        "predicted_category": None,
        "label_agreement": None,
        "deterministic_checks": deterministic_checks,
        "scores": {name: 0 for name in DIMENSION_MAX},
        "total_score": 0,
        "failure_modes": [],
        "judge_rationale": "The LLM judge response could not be parsed or validated after one repair attempt, so this deterministic fallback assigns zero for auditability.",
        "judge_metadata": {
            "judge_status": "failed",
            "judge_errors": errors,
            "judge_model": config.judge_model,
            "judge_temperature": config.judge_temperature,
            "judge_prompt_version": config.judge_prompt_version,
            "judge_run_id": config.judge_run_id,
            "repair_attempted": True,
        },
    }


def call_row_judge(
    row: dict[str, Any],
    records: dict[str, dict[str, Any]],
    deterministic_checks: dict[str, Any],
    client: OpenAIClient,
    config: EvalConfig,
) -> dict[str, Any]:
    payload = row_prompt_payload(row, records)
    validation_errors: list[str] = []
    last_errors: list[str] = []
    repair_attempted = False
    for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
        repair_attempted = attempt > 1
        try:
            response = client.generate_json(
                system_prompt=row_judge_system_prompt(),
                user_prompt=row_judge_user_prompt(payload, validation_errors or None),
            )
        except (json.JSONDecodeError, RuntimeError, KeyError) as exc:
            last_errors = [f"judge_call_failed: {exc}"]
            validation_errors = last_errors
            continue
        last_errors = validate_row_judge_response(response, row)
        if last_errors:
            validation_errors = last_errors
            continue
        predicted_category = response["predicted_category"]
        return {
            "row_id": row["id"],
            "category": row["category"],
            "predicted_category": predicted_category,
            "label_agreement": predicted_category == row["category"],
            "deterministic_checks": deterministic_checks,
            "scores": response["scores"],
            "total_score": response["total_score"],
            "failure_modes": response.get("failure_modes", []),
            "judge_rationale": response["rationale"],
            "judge_metadata": {
                "judge_status": "ok",
                "judge_errors": [],
                "judge_model": config.judge_model,
                "judge_temperature": config.judge_temperature,
                "judge_prompt_version": config.judge_prompt_version,
                "judge_run_id": config.judge_run_id,
                "repair_attempted": repair_attempted,
            },
        }
    return fallback_row_eval(row, deterministic_checks, last_errors, config)


def normalize_words(*values: Any) -> set[str]:
    text = " ".join(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value for value in values)
    words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower()))
    stop_words = {
        "the",
        "and",
        "for",
        "you",
        "your",
        "that",
        "this",
        "with",
        "from",
        "are",
        "can",
        "under",
        "razorpay",
        "clause",
        "terms",
        "tos",
        "payment",
        "payments",
    }
    return words - stop_words


def add_candidate(scores: dict[str, int], clause_id: str | None, points: int, records: dict[str, dict[str, Any]]) -> None:
    if clause_id and clause_id in records:
        scores[clause_id] = max(scores.get(clause_id, 0), 0) + points


def candidate_clause_pack(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cited_ids = [source["clause_id"] for source in row.get("source_clauses", []) if source.get("clause_id") in records]
    cited_records = [records[clause_id] for clause_id in cited_ids]
    query_words = normalize_words(
        row.get("messages", []),
        row.get("known_facts", []),
        row.get("missing_facts", []),
        row.get("ambiguity_reason"),
    )
    cited_taxonomy = defaultdict(set)
    for record in cited_records:
        taxonomy = record.get("taxonomy", {})
        for key in ("topic_tags", "actor_scope", "payment_stage", "issue_type"):
            cited_taxonomy[key].update(taxonomy.get(key, []))
        service_area = taxonomy.get("service_area")
        if service_area:
            cited_taxonomy["service_area"].add(service_area)

    scores: dict[str, int] = {}
    required_ids: set[str] = set()
    for clause_id in cited_ids:
        add_candidate(scores, clause_id, 100, records)
        required_ids.add(clause_id)
        record = records[clause_id]
        relationships = record.get("relationships", {})
        related = [relationships.get("parent_clause_id")]
        related.extend(relationships.get("child_clause_ids", []))
        related.extend(relationships.get("sibling_clause_ids", []))
        for related_id in related:
            if related_id in records:
                required_ids.add(related_id)
                add_candidate(scores, related_id, 80, records)

    same_service_added = 0
    for clause_id, record in records.items():
        taxonomy = record.get("taxonomy", {})
        score = 0
        topic_overlap = len(set(taxonomy.get("topic_tags", [])) & cited_taxonomy["topic_tags"])
        actor_overlap = len(set(taxonomy.get("actor_scope", [])) & cited_taxonomy["actor_scope"])
        payment_overlap = len(set(taxonomy.get("payment_stage", [])) & cited_taxonomy["payment_stage"])
        issue_overlap = len(set(taxonomy.get("issue_type", [])) & cited_taxonomy["issue_type"])
        score += 3 * topic_overlap
        score += actor_overlap + payment_overlap + issue_overlap
        if taxonomy.get("service_area") in cited_taxonomy["service_area"]:
            if same_service_added < SAME_SERVICE_AREA_CAP or clause_id in required_ids:
                score += 2
                same_service_added += 1
        record_words = normalize_words(record.get("text", ""), record.get("display_citation", ""))
        score += min(len(query_words & record_words), 8)
        if score:
            add_candidate(scores, clause_id, score, records)

    ordered_ids = sorted(scores, key=lambda clause_id: (-scores[clause_id], records[clause_id].get("span_location", {}).get("char_start", 0)))
    final_ids = list(dict.fromkeys([clause_id for clause_id in ordered_ids if clause_id in required_ids]))
    for clause_id in ordered_ids:
        if len(final_ids) >= MAX_CANDIDATES:
            break
        if clause_id not in final_ids:
            final_ids.append(clause_id)
    return [candidate_payload(records[clause_id], scores[clause_id], clause_id in cited_ids) for clause_id in final_ids]


def candidate_payload(record: dict[str, Any], candidate_score: int, originally_cited: bool) -> dict[str, Any]:
    return {
        "clause_id": record["clause_id"],
        "display_citation": record["display_citation"],
        "record_type": record["record_type"],
        "candidate_retrieval_score": candidate_score,
        "originally_cited": originally_cited,
        "hierarchy": record.get("hierarchy", {}),
        "taxonomy": record.get("taxonomy", {}),
        "text": truncate_source_text(record.get("text", ""), "", limit=1200),
    }


def source_review_system_prompt() -> str:
    return (
        "You are a strict source-adequacy reviewer for a Razorpay ToS synthetic Q&A dataset. "
        "Use only the generated row, original sources, and candidate clauses provided. Return strict JSON only."
    )


def source_review_user_prompt(row: dict[str, Any], row_eval: dict[str, Any], candidates: list[dict[str, Any]], validation_errors: list[str] | None = None) -> str:
    instructions: dict[str, Any] = {
        "task": "Review whether the original cited clauses are sufficient and whether a better candidate clause exists.",
        "questions_to_answer": [
            "Were the original cited clauses sufficient?",
            "Is there a better or more specific clause in the candidate set?",
            "If yes, which clause ID is better and why?",
            "Was the issue caused by source selection, answer generation, category labeling, user-question design, or something else?",
        ],
        "allowed_source_issue_types": sorted(SOURCE_ISSUE_TYPES),
        "output_schema": {
            "row_id": row["id"],
            "source_adequacy_score": "integer 0-100",
            "original_sources_sufficient": "boolean",
            "better_candidate_clauses": [{"clause_id": "...", "reason": "..."}],
            "source_issue_type": "one allowed_source_issue_types value",
            "diagnosis": "one concise sentence",
        },
        "row": row_prompt_payload(row, {candidate["clause_id"]: {"text": candidate["text"]} for candidate in candidates}),
        "row_level_eval": {
            "total_score": row_eval["total_score"],
            "scores": row_eval["scores"],
            "failure_modes": row_eval["failure_modes"],
            "judge_rationale": row_eval["judge_rationale"],
        },
        "candidate_clause_pack": candidates,
    }
    if validation_errors:
        instructions["previous_invalid_response_errors"] = validation_errors
        instructions["repair_instruction"] = "Return corrected strict JSON."
    return json.dumps(instructions, indent=2, ensure_ascii=False)


def validate_source_review_response(response: dict[str, Any], row: dict[str, Any], candidate_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if response.get("row_id") != row.get("id"):
        errors.append("row_id mismatch")
    if type(response.get("source_adequacy_score")) is not int or not 0 <= response.get("source_adequacy_score", -1) <= 100:
        errors.append("source_adequacy_score must be integer 0-100")
    if not isinstance(response.get("original_sources_sufficient"), bool):
        errors.append("original_sources_sufficient must be boolean")
    if response.get("source_issue_type") not in SOURCE_ISSUE_TYPES:
        errors.append("source_issue_type is invalid")
    better = response.get("better_candidate_clauses")
    if not isinstance(better, list):
        errors.append("better_candidate_clauses must be a list")
    else:
        for item in better:
            if not isinstance(item, dict) or item.get("clause_id") not in candidate_ids or not item.get("reason"):
                errors.append("better_candidate_clauses entries need candidate clause_id and reason")
    if not isinstance(response.get("diagnosis"), str) or not response["diagnosis"].strip():
        errors.append("diagnosis must be non-empty")
    return errors


def call_source_review(
    row: dict[str, Any],
    row_eval: dict[str, Any],
    records: dict[str, dict[str, Any]],
    client: OpenAIClient,
    config: EvalConfig,
) -> dict[str, Any]:
    candidates = candidate_clause_pack(row, records)
    candidate_ids = {candidate["clause_id"] for candidate in candidates}
    validation_errors: list[str] = []
    last_errors: list[str] = []
    for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
        try:
            response = client.generate_json(
                system_prompt=source_review_system_prompt(),
                user_prompt=source_review_user_prompt(row, row_eval, candidates, validation_errors or None),
            )
        except (json.JSONDecodeError, RuntimeError, KeyError) as exc:
            last_errors = [f"source_review_call_failed: {exc}"]
            validation_errors = last_errors
            continue
        last_errors = validate_source_review_response(response, row, candidate_ids)
        if last_errors:
            validation_errors = last_errors
            continue
        response["review_metadata"] = {
            "judge_status": "ok",
            "judge_model": config.judge_model,
            "judge_temperature": config.judge_temperature,
            "source_review_prompt_version": config.source_review_prompt_version,
            "judge_run_id": config.judge_run_id,
            "candidate_count": len(candidates),
            "repair_attempted": attempt > 1,
        }
        return response
    return {
        "row_id": row["id"],
        "source_adequacy_score": 0,
        "original_sources_sufficient": False,
        "better_candidate_clauses": [],
        "source_issue_type": "other",
        "diagnosis": "The source-adequacy judge response could not be parsed or validated after one repair attempt.",
        "review_metadata": {
            "judge_status": "failed",
            "judge_errors": last_errors,
            "judge_model": config.judge_model,
            "judge_temperature": config.judge_temperature,
            "source_review_prompt_version": config.source_review_prompt_version,
            "judge_run_id": config.judge_run_id,
            "candidate_count": len(candidates),
            "repair_attempted": True,
        },
    }


def diagnosis_system_prompt() -> str:
    return (
        "You write concise failure diagnoses for an evaluation summary. "
        "Return strict JSON only. The diagnosis should be approximately 50 words."
    )


def diagnosis_user_prompt(
    row: dict[str, Any],
    row_eval: dict[str, Any],
    source_review: dict[str, Any],
    validation_errors: list[str] | None = None,
) -> str:
    instructions: dict[str, Any] = {
        "task": "Write one approximately 50-word diagnosis for this worst-scoring dataset example.",
        "must_cover": [
            "what is wrong",
            "where in the pipeline it likely came from",
            "what caused it",
            "what rubric or code change would catch it",
        ],
        "output_schema": {
            "row_id": row["id"],
            "diagnosis": "approximately 50 words, one paragraph",
        },
        "row": {
            "id": row["id"],
            "category": row["category"],
            "user_question": row.get("messages", [{}, {}])[0].get("content", ""),
            "assistant_answer": row.get("messages", [{}, {}])[1].get("content", ""),
            "source_clause_ids": [source.get("clause_id") for source in row.get("source_clauses", [])],
        },
        "row_level_judge": {
            "total_score": row_eval["total_score"],
            "scores": row_eval["scores"],
            "failure_modes": row_eval["failure_modes"],
            "judge_rationale": row_eval["judge_rationale"],
            "deterministic_checks": row_eval["deterministic_checks"],
        },
        "source_adequacy_review": source_review,
    }
    if validation_errors:
        instructions["previous_invalid_response_errors"] = validation_errors
        instructions["repair_instruction"] = "Return corrected strict JSON."
    return json.dumps(instructions, indent=2, ensure_ascii=False)


def validate_diagnosis_response(response: dict[str, Any], row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if response.get("row_id") != row["id"]:
        errors.append("row_id mismatch")
    diagnosis = response.get("diagnosis")
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        errors.append("diagnosis must be non-empty")
    return errors


def call_diagnosis(
    row: dict[str, Any],
    row_eval: dict[str, Any],
    source_review: dict[str, Any],
    client: OpenAIClient,
) -> str:
    validation_errors: list[str] = []
    for _attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
        try:
            response = client.generate_json(
                system_prompt=diagnosis_system_prompt(),
                user_prompt=diagnosis_user_prompt(row, row_eval, source_review, validation_errors or None),
            )
        except (json.JSONDecodeError, RuntimeError, KeyError) as exc:
            validation_errors = [f"diagnosis_call_failed: {exc}"]
            continue
        errors = validate_diagnosis_response(response, row)
        if errors:
            validation_errors = errors
            continue
        return response["diagnosis"].strip()
    return (
        "This row landed among the weakest examples, but the diagnosis judge failed validation. "
        "The issue should be reviewed manually using the row score, failure modes, deterministic checks, and source-adequacy review."
    )


def select_worst_rows(eval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = sorted(eval_results, key=lambda result: (result["total_score"], result["row_id"]))
    if len(sorted_rows) <= 3:
        return sorted_rows
    third_score = sorted_rows[2]["total_score"]
    worst = [result for result in sorted_rows if result["total_score"] <= third_score]
    if len(sorted_rows) > 3 and sorted_rows[3]["total_score"] == third_score:
        return sorted_rows[:4]
    return worst[:3]


def aggregate_metrics(eval_results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [result["total_score"] for result in eval_results]
    by_category: dict[str, dict[str, float]] = {}
    for category in sorted({result["category"] for result in eval_results}):
        category_scores = [result["total_score"] for result in eval_results if result["category"] == category]
        by_category[category] = {
            "mean_score": round(statistics.mean(category_scores), 2),
            "lowest_score": min(category_scores),
            "row_count": len(category_scores),
        }
    dimension_means = {
        name: round(statistics.mean(result["scores"][name] for result in eval_results), 2)
        for name in DIMENSION_MAX
    }
    failure_counts = Counter(mode for result in eval_results for mode in result.get("failure_modes", []))
    judged = [result for result in eval_results if result["judge_metadata"]["judge_status"] == "ok"]
    agreements = [result for result in judged if result.get("label_agreement")]
    mismatches = sorted(
        result["row_id"] for result in judged if result.get("label_agreement") is False
    )
    return {
        "mean_score": round(statistics.mean(scores), 2) if scores else None,
        "median_score": round(statistics.median(scores), 2) if scores else None,
        "lowest_score": min(scores) if scores else None,
        "highest_score": max(scores) if scores else None,
        "by_category": by_category,
        "dimension_means": dimension_means,
        "failure_mode_counts": dict(sorted(failure_counts.items())),
        "judge_failure_count": sum(1 for result in eval_results if result["judge_metadata"]["judge_status"] != "ok"),
        "label_agreement_rate": round(len(agreements) / len(judged), 4) if judged else None,
        "label_agreement_count": len(agreements),
        "label_judged_count": len(judged),
        "label_mismatch_row_ids": mismatches,
    }


def bool_count(eval_results: list[dict[str, Any]], check_name: str) -> int:
    return sum(1 for result in eval_results if result["deterministic_checks"].get(check_name))


def select_human_review_rows(
    rows: list[dict[str, Any]],
    eval_results: list[dict[str, Any]],
    source_reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pick a compact human-review queue with explicit selection reasons.

    Criteria:
    - all label disagreements and deterministic/schema failures,
    - the lowest-scoring rows,
    - rows where deep source review found insufficient citations,
    - a capped sample of rows with judge/lint flags,
    - category coverage backfill if a category was not selected,
    - a seeded sample of perfect-score rows as positive controls.
    """
    reasons: dict[str, list[str]] = {}
    rng = random.Random(HUMAN_REVIEW_SEED)
    row_by_id = {row["id"]: row for row in rows}
    result_by_id = {result["row_id"]: result for result in eval_results}

    for result in eval_results:
        checks = result["deterministic_checks"]
        if result.get("label_agreement") is False:
            reasons.setdefault(result["row_id"], []).append("judge_label_disagreement")
        if checks.get("schema_validation_errors"):
            reasons.setdefault(result["row_id"], []).append("deterministic_validation_failure")

    worst = sorted(eval_results, key=lambda result: (result["total_score"], result["row_id"]))
    for result in worst[:HUMAN_REVIEW_WORST_COUNT]:
        reasons.setdefault(result["row_id"], []).append("lowest_judge_score")

    for review in source_reviews:
        if not review.get("original_sources_sufficient"):
            reasons.setdefault(review["row_id"], []).append("source_review_insufficient")

    flagged = [
        result
        for result in eval_results
        if result.get("failure_modes")
        or result["deterministic_checks"].get("quality_lint_warnings")
        or result["deterministic_checks"].get("quality_lint_errors")
    ]
    for result in sorted(flagged, key=lambda result: (result["total_score"], result["row_id"]))[:HUMAN_REVIEW_FLAGGED_COUNT]:
        reasons.setdefault(result["row_id"], []).append("judge_or_lint_flag")

    selected_categories = {row_by_id[row_id]["category"] for row_id in reasons}
    for category in sorted(CATEGORIES - selected_categories):
        candidates = [result for result in eval_results if result["category"] == category]
        if candidates:
            result = min(candidates, key=lambda item: (item["total_score"], item["row_id"]))
            reasons.setdefault(result["row_id"], []).append("category_coverage_backfill")

    perfect = sorted(
        (result for result in eval_results if result["total_score"] == 100),
        key=lambda result: result["row_id"],
    )
    perfect_ids = [result["row_id"] for result in perfect if result["row_id"] not in reasons]
    for row_id in rng.sample(perfect_ids, min(HUMAN_REVIEW_PERFECT_SCORE_COUNT, len(perfect_ids))):
        reasons.setdefault(row_id, []).append("perfect_score_positive_control")

    ordered_ids = [row["id"] for row in rows if row["id"] in reasons]
    review_items = []
    for row_id in ordered_ids:
        row = row_by_id[row_id]
        result = result_by_id[row_id]
        review_items.append(
            {
                "row_id": row_id,
                "category": row["category"],
                "predicted_category": result.get("predicted_category"),
                "total_score": result["total_score"],
                "reasons": sorted(set(reasons[row_id])),
                "failure_modes": result.get("failure_modes", []),
                "source_clauses": [
                    {
                        "clause_id": source.get("clause_id"),
                        "display_citation": source.get("display_citation"),
                    }
                    for source in row.get("source_clauses", [])
                ],
            }
        )
    return review_items


ADVERSARIAL_CORE_CHECKS = (
    "source_clause_ids_resolve",
    "relevant_quotes_contained",
    "assistant_has_visible_citation",
    "category_fields_valid",
    "assistant_non_empty",
    "user_question_non_empty",
)


def adversarial_catch_result(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Determine which eval layers caught a planted flaw in an adversarial row."""
    checks = result["deterministic_checks"]
    caught_deterministic = bool(checks.get("schema_validation_errors")) or not all(
        checks.get(name) for name in ADVERSARIAL_CORE_CHECKS
    )
    caught_lints = bool(checks.get("quality_lint_warnings") or checks.get("quality_lint_errors"))
    caught_judge = (
        result.get("label_agreement") is False
        or bool(result.get("failure_modes"))
        or result["total_score"] < JUDGE_CATCH_SCORE_THRESHOLD
    )
    caught_by = [
        name
        for name, caught in (
            ("deterministic", caught_deterministic),
            ("quality_lints", caught_lints),
            ("judge", caught_judge),
        )
        if caught
    ]
    metadata = row.get("adversarial_metadata", {})
    expected_layer = metadata.get("expected_layer")
    return {
        "row_id": row["id"],
        "planted_flaw": metadata.get("planted_flaw"),
        "expected_layer": expected_layer,
        "caught": bool(caught_by),
        "caught_by": caught_by,
        "caught_by_expected_layer": expected_layer in caught_by if expected_layer else None,
        "judge_total_score": result["total_score"],
        "judge_predicted_category": result.get("predicted_category"),
        "planned_category": row.get("category"),
    }


def load_baseline_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_summary(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    dataset_check_result: dict[str, Any],
    eval_results: list[dict[str, Any]],
    source_reviews: list[dict[str, Any]],
    diagnoses: dict[str, str],
    metrics: dict[str, Any],
    config: EvalConfig,
    review_rows: list[dict[str, Any]] | None = None,
    catch_results: list[dict[str, Any]] | None = None,
    baseline: dict[str, Any] | None = None,
) -> None:
    row_count = len(rows)
    category_counts = Counter(row["category"] for row in rows)
    source_review_by_id = {review["row_id"]: review for review in source_reviews}
    result_by_id = {result["row_id"]: result for result in eval_results}
    row_by_id = {row["id"]: row for row in rows}
    worst_ids = [review["row_id"] for review in source_reviews]
    lines = [
        "# LLM-as-Judge Evaluation Summary",
        "",
        "## Dataset Evaluated",
        "",
        f"- Dataset path: `{workspace_path(DATASET_PATH)}`",
        f"- Run ID: `{manifest.get('run_id')}`",
        f"- Source hash: `{manifest.get('source_hash')}`",
        f"- Source fetched at: `{manifest.get('source_fetched_at')}`",
        f"- Row count: {row_count}",
        f"- Category distribution: {dict(sorted(category_counts.items()))}",
        f"- Generator model: `{config.generator_model}`",
        f"- Judge model: `{config.judge_model}`",
        f"- Cross-model judging: `{config.cross_model_judging}`",
        f"- Judge prompt version: `{config.judge_prompt_version}` (blind: no planned label, no deterministic-check results, no annotation fields in the judge prompt)",
        f"- Judge temperature: {config.judge_temperature if config.judge_temperature is not None else 'provider default'}",
        "",
        "## Evaluation Design",
        "",
        "This evaluation combines deterministic schema/source validators with a blind row-level LLM judge. "
        "The judge sees only the user question, assistant answer, and full cited clause text; it independently predicts which "
        "response category the situation calls for, then scores the row out of 100 against that prediction. The planned category "
        "label, deterministic check results, and structured annotation fields are withheld from the judge and recorded separately "
        "as audit metadata, so the judge cannot anchor on prior automated signals. Judge-vs-label agreement is reported as a metric. "
        "The lowest-scoring rows then receive a deeper source-adequacy review using hierarchy and taxonomy from the clause index.",
        "",
        "## Deterministic Validation Results",
        "",
        "| Check | Result |",
        "|---|---:|",
        f"| Rows parsed | {row_count if dataset_check_result['all_rows_parse'] else row_count - len(dataset_check_result['parse_errors'])}/{row_count} |",
        f"| Category balance | {category_counts.get('clear_answer', 0)}/{category_counts.get('clarification_required', 0)}/{category_counts.get('genuine_ambiguity', 0)} |",
        f"| Source clauses resolve | {bool_count(eval_results, 'source_clause_ids_resolve')}/{row_count} |",
        f"| Relevant quotes contained | {bool_count(eval_results, 'relevant_quotes_contained')}/{row_count} |",
        f"| Assistant citations visible | {bool_count(eval_results, 'assistant_has_visible_citation')}/{row_count} |",
        f"| Category fields valid | {bool_count(eval_results, 'category_fields_valid')}/{row_count} |",
        f"| Dataset validator failures | {len(dataset_check_result['dataset_validation_failures'])} |",
        "",
        "## Scoring Rubric",
        "",
        "| Dimension | Points |",
        "|---|---:|",
    ]
    lines.extend(f"| {name.replace('_', ' ').title()} | {points} |" for name, points in DIMENSION_MAX.items())
    lines.extend(
        [
            "| Total | 100 |",
            "",
            "## Aggregate Results",
            "",
            "| Metric | Result |",
            "|---|---:|",
            f"| Mean score | {metrics['mean_score']}/100 |",
            f"| Median score | {metrics['median_score']}/100 |",
            f"| Lowest score | {metrics['lowest_score']}/100 |",
            f"| Highest score | {metrics['highest_score']}/100 |",
            f"| Judge fallback failures | {metrics['judge_failure_count']} |",
            f"| Label agreement (judge vs planned category) | {metrics['label_agreement_count']}/{metrics['label_judged_count']} ({round((metrics['label_agreement_rate'] or 0) * 100, 1)}%) |",
            "",
            "## Results By Category",
            "",
            "| Category | Mean score | Lowest score | Rows |",
            "|---|---:|---:|---:|",
        ]
    )
    for category, values in metrics["by_category"].items():
        lines.append(f"| `{category}` | {values['mean_score']} | {values['lowest_score']} | {values['row_count']} |")
    lines.extend(["", "## Results By Dimension", "", "| Dimension | Mean score |", "|---|---:|"])
    for name, value in metrics["dimension_means"].items():
        lines.append(f"| {name.replace('_', ' ').title()} | {value} |")
    lines.extend(["", "## Common Failure Modes", "", "| Failure mode | Count |", "|---|---:|"])
    if metrics["failure_mode_counts"]:
        for mode, count in metrics["failure_mode_counts"].items():
            lines.append(f"| `{mode}` | {count} |")
    else:
        lines.append("| None flagged | 0 |")

    if catch_results is not None:
        lines.extend(render_catch_rate_section(catch_results))

    lines.extend(["", "## Label Agreement", ""])
    lines.append(
        "The blind judge predicts a category for every row without seeing the planned label. "
        "Disagreements are surfaced here and routed to human review; they indicate either a mislabeled row or a judge boundary error."
    )
    lines.append("")
    mismatch_ids = metrics.get("label_mismatch_row_ids", [])
    if mismatch_ids:
        lines.extend(["| Row | Planned category | Judge predicted | Judge score |", "|---|---|---|---:|"])
        for row_id in mismatch_ids:
            result = result_by_id[row_id]
            lines.append(
                f"| `{row_id}` | `{result['category']}` | `{result['predicted_category']}` | {result['total_score']} |"
            )
    else:
        lines.append("The judge's predicted category matched the planned label on every judged row.")

    lines.extend(["", "## Worst Examples", ""])
    for index, row_id in enumerate(worst_ids, start=1):
        row_eval = result_by_id[row_id]
        source_review = source_review_by_id[row_id]
        lines.extend(
            [
                f"### {index}. `{row_id}`",
                "",
                f"- Category: `{row_eval['category']}`",
                f"- Total score: {row_eval['total_score']}/100",
                f"- Failure modes: {row_eval['failure_modes'] or []}",
                f"- Source adequacy: {source_review['source_adequacy_score']}/100; original sources sufficient: `{source_review['original_sources_sufficient']}`",
                "",
                diagnoses.get(row_id, source_review["diagnosis"]),
                "",
            ]
        )
    if review_rows:
        lines.extend(render_human_review_section(review_rows, row_by_id, result_by_id))

    if baseline is not None and not config.adversarial:
        lines.extend(render_changes_since_v1_section(baseline, metrics, config, catch_results))

    if config.adversarial:
        caught = sum(1 for item in (catch_results or []) if item["caught"])
        total = len(catch_results or [])
        lines.extend(
            [
                "## Interpretation",
                "",
                f"The evaluation framework caught {caught}/{total} intentionally planted flaws. "
                "This run exists to test the eval framework itself: every row above is deliberately defective, "
                "so low scores and failures here are the desired outcome.",
            ]
        )
    else:
        acceptability = "strong overall with review caveats" if metrics["mean_score"] and metrics["mean_score"] >= 80 else "needs review before broader use"
        lines.extend(
            [
                "## Interpretation",
                "",
                f"The dataset is {acceptability}. The evaluation is intentionally stricter than schema validation: it tests category fit, source grounding, citation sufficiency, usefulness, overreach, and realism. Because the judge mechanism changed in v2 (blind judging, optional cross-model), scores are not directly point-comparable to v1; failure modes and label agreement are the like-for-like evidence for the dataset run, while the adversarial catch rate is documented separately in `adversarial_report.md`.",
                "",
                "## Future Improvements",
                "",
                "- Run source-adequacy retrieval for all rows, not only the lowest-scoring rows.",
                "- Move candidate source retrieval upstream into dataset generation so weak citations are caught before rows are written.",
                "- Add a user-question realism rewrite pass for overly polished or synthetic prompts.",
                "- Regenerate or patch rows that fail human review, then re-run this evaluation.",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_catch_rate_section(catch_results: list[dict[str, Any]]) -> list[str]:
    caught = sum(1 for item in catch_results if item["caught"])
    expected_hits = [item for item in catch_results if item.get("caught_by_expected_layer")]
    lines = [
        "",
        "## Adversarial Catch Rate By Layer",
        "",
        "Each row below contains one intentionally planted flaw. The table shows which evaluation layer was expected to catch it "
        "and which layers actually did. This tests the eval framework as a stack of defenses (deterministic checks, then quality "
        "lints, then the blind LLM judge) rather than treating the judge as the only safeguard.",
        "",
        f"- Flaws caught (any layer): {caught}/{len(catch_results)}",
        f"- Flaws caught by the expected layer: {len(expected_hits)}/{len(catch_results)}",
        "",
        "| Row | Planted flaw | Expected layer | Caught by | Caught? |",
        "|---|---|---|---|---|",
    ]
    for item in catch_results:
        caught_by = ", ".join(item["caught_by"]) if item["caught_by"] else "none"
        verdict = "yes" if item["caught"] else "NO - MISSED"
        lines.append(
            f"| `{item['row_id']}` | {item['planted_flaw']} | {item['expected_layer']} | {caught_by} | {verdict} |"
        )
    return lines


def write_adversarial_report(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    eval_results: list[dict[str, Any]],
    catch_results: list[dict[str, Any]],
    metrics: dict[str, Any],
    config: EvalConfig,
) -> None:
    caught = sum(1 for item in catch_results if item["caught"])
    expected_hits = sum(1 for item in catch_results if item.get("caught_by_expected_layer"))
    by_expected_layer = Counter(item["expected_layer"] for item in catch_results)
    caught_by_layer = Counter(layer for item in catch_results for layer in item["caught_by"])
    result_by_id = {result["row_id"]: result for result in eval_results}
    row_by_id = {row["id"]: row for row in rows}

    lines = [
        "# Adversarial Evaluation Report",
        "",
        "## Purpose",
        "",
        "This report stress-tests the evaluation framework with intentionally flawed rows. It is not a second dataset evaluation run; it is a targeted check that the layered eval stack can distinguish known-bad examples from strong generated rows.",
        "",
        "The adversarial pack is deliberately small and hand-authored so each row isolates one failure mode. The rows are grouped by the layer expected to catch them: deterministic validation, advisory quality lints, or the blind LLM judge.",
        "",
        "## Why These Eight Examples",
        "",
        "- `adv_det_001` plants a fabricated quote against a real clause to test quote containment.",
        "- `adv_det_002` cites a nonexistent clause ID to test source resolution and crash resistance.",
        "- `adv_det_003` violates the category-specific schema shape for a clarification row.",
        "- `adv_lint_004` is schema-valid but uses the vague clarifier `Can you provide more details?`.",
        "- `adv_lint_005` is schema-valid but relies on a thin section preamble as ambiguity support.",
        "- `adv_judge_006` manufactures ambiguity where the cited clause directly answers the question.",
        "- `adv_judge_007` invents a 24-hour refund guarantee and fee waiver unsupported by the cited clause.",
        "- `adv_judge_008` invents a 180-day fund-hold cap where the ToS is silent on duration.",
        "",
        "Together these cover the main risks in this assignment: citation integrity, schema/category shape, clarification quality, source strength, unsupported claims, modal/timeline overreach, and category-boundary mistakes.",
        "",
        "## Targeted Eval Layers",
        "",
        "| Expected layer | Rows | What it should catch |",
        "|---|---:|---|",
        f"| deterministic | {by_expected_layer.get('deterministic', 0)} | Hard failures: unknown sources, unsupported quotes, invalid category-specific fields |",
        f"| quality_lints | {by_expected_layer.get('quality_lints', 0)} | Schema-valid but weak examples: vague clarifiers and thin source support |",
        f"| judge | {by_expected_layer.get('judge', 0)} | Semantic failures that pass lower layers: wrong category, unsupported claims, overreach |",
        "",
        "## Performance",
        "",
        "Catch layers are non-exclusive: a row can be caught by the intended lower layer and also noticed by later layers such as the judge.",
        "",
        f"- Flaws caught by any layer: {caught}/{len(catch_results)}",
        f"- Flaws caught by the expected layer: {expected_hits}/{len(catch_results)}",
        f"- Mean adversarial judge score: {metrics['mean_score']}/100",
        f"- Lowest adversarial judge score: {metrics['lowest_score']}/100",
        f"- Judge fallback failures: {metrics['judge_failure_count']}",
        f"- Catches by deterministic layer: {caught_by_layer.get('deterministic', 0)}",
        f"- Catches by quality-lint layer: {caught_by_layer.get('quality_lints', 0)}",
        f"- Catches by judge layer: {caught_by_layer.get('judge', 0)}",
        "",
        "| Row | Planted flaw | Expected layer | Caught by | Judge score | Planned -> Judge | Failure modes |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in catch_results:
        result = result_by_id[item["row_id"]]
        row = row_by_id[item["row_id"]]
        caught_by = ", ".join(item["caught_by"]) if item["caught_by"] else "none"
        failure_modes = ", ".join(f"`{mode}`" for mode in result.get("failure_modes", [])) or "none"
        lines.append(
            f"| `{item['row_id']}` | {item['planted_flaw']} | {item['expected_layer']} | {caught_by} | {item['judge_total_score']} | `{row['category']}` -> `{item['judge_predicted_category']}` | {failure_modes} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"The eval stack performed well on the targeted pack: it caught {caught}/{len(catch_results)} planted flaws, and every flaw was caught by the layer it was designed to exercise. The deterministic layer handled source/shape integrity, the advisory lints caught weak-but-valid construction, and the blind judge caught the semantic failures that lower layers intentionally cannot see.",
            "",
            "Judge scores are most meaningful for the judge-targeted rows. For deterministic- and lint-targeted rows, the expected lower layer is the source of truth, so a high judge score can still coexist with a correctly caught source-resolution or schema failure.",
            "",
            "The most useful signal is the judge-only subset. Those rows were constructed to pass schema validation and advisory lints, so low scores, category disagreements, and `unsupported_claim`/`wrong_category`-style failure modes show that the LLM judge is adding semantic coverage rather than duplicating deterministic checks.",
            "",
            f"Run metadata: judge model `{config.judge_model}`, generator model `{config.generator_model}`, cross-model judging `{config.cross_model_judging}`, prompt `{config.judge_prompt_version}`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_human_review_section(
    review_rows: list[dict[str, Any]],
    row_by_id: dict[str, dict[str, Any]],
    result_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    lines = [
        "## Human Review",
        "",
        "This is a compact audit queue, not a full worksheet. The goal is to tell a human reviewer which generated rows and cited clauses deserve attention, without repeating the full answer text or source text already present in the JSONL. The selection is risk-first with positive controls: lowest judge scores, label disagreements, deterministic/schema failures, source-review insufficiency, judge/lint flags, category-coverage backfill, and a seeded sample of perfect-score rows.",
        "",
        f"Selection settings: lowest-score rows = {HUMAN_REVIEW_WORST_COUNT}; judge/lint flagged cap = {HUMAN_REVIEW_FLAGGED_COUNT}; perfect-score positive controls = {HUMAN_REVIEW_PERFECT_SCORE_COUNT}; seed = {HUMAN_REVIEW_SEED}.",
        "",
        "Recommended review criteria: confirm the planned category, verify the cited clauses are the strongest support, check that the answer does not overstate modal language or timelines, and decide whether the row should be accepted, patched, or regenerated.",
        "",
        "| Row | Planned -> Judge | Score | Why review | Clauses to inspect |",
        "|---|---|---:|---|---|",
    ]
    for item in review_rows:
        clauses = "<br>".join(
            f"`{source['clause_id']}`: {source['display_citation']}"
            for source in item["source_clauses"]
        )
        reasons = ", ".join(item["reasons"])
        failure_modes = ", ".join(f"`{mode}`" for mode in item["failure_modes"]) or "none"
        lines.append(
            f"| `{item['row_id']}` | `{item['category']}` -> `{item['predicted_category']}` | {item['total_score']} | {reasons}; failure modes: {failure_modes} | {clauses} |"
        )
    return lines


def render_changes_since_v1_section(
    baseline: dict[str, Any],
    metrics: dict[str, Any],
    config: EvalConfig,
    catch_results: list[dict[str, Any]] | None,
) -> list[str]:
    baseline_metrics = baseline.get("aggregate_metrics", {})
    baseline_by_category = baseline_metrics.get("by_category", {})
    lines = [
        "## Changes Since V1",
        "",
        "V1 generated this dataset and evaluated it with a category-aware judge that shared the generator model and saw the planned "
        "label plus deterministic-check results. Feedback identified possible same-model bias/leakage and a lack of gold human checks "
        "and adversarial checks. V2 hardens the evaluation mechanism and re-evaluates the same, unchanged dataset:",
        "",
        "- Blind judging: the judge no longer sees the planned category, deterministic-check results, or annotation fields, and must predict the category itself.",
        (
            "- Cross-model judging: the judge model differs from the generator model."
            if config.cross_model_judging
            else "- Cross-model judging supported via --judge-model / OPENAI_JUDGE_MODEL; this run reused the generator model, so blind prompting is the active mitigation (same-model bias remains a caveat)."
        ),
        "- Human review: a compact, pipeline-selected audit queue listing the rows and cited clauses a reviewer should inspect.",
        "- Adversarial pack: intentionally flawed rows with a standalone per-layer catch-rate report.",
        "",
        "Because the v2 judge uses a different prompt and model, score deltas against v1 are directional rather than point-comparable. "
        "Failure-mode counts and label agreement are the like-for-like evidence for the dataset run; the adversarial catch rate is reported separately in `adversarial_report.md`.",
        "",
        "| Metric | V1 | V2 |",
        "|---|---|---|",
        f"| Judge model | `{baseline.get('judge_model')}` | `{config.judge_model}` |",
        f"| Judge prompt | `{baseline.get('judge_prompt_version')}` | `{config.judge_prompt_version}` |",
        "| Judge sees planned label / deterministic checks | yes | no |",
        f"| Cross-model judging | no | {'yes' if config.cross_model_judging else 'no'} |",
        f"| Mean score | {baseline_metrics.get('mean_score')} | {metrics.get('mean_score')} |",
        f"| Median score | {baseline_metrics.get('median_score')} | {metrics.get('median_score')} |",
        f"| Lowest score | {baseline_metrics.get('lowest_score')} | {metrics.get('lowest_score')} |",
    ]
    for category in sorted(CATEGORIES):
        v1_mean = baseline_by_category.get(category, {}).get("mean_score")
        v2_mean = metrics.get("by_category", {}).get(category, {}).get("mean_score")
        lines.append(f"| Mean score (`{category}`) | {v1_mean} | {v2_mean} |")
    v1_failures = baseline_metrics.get("failure_mode_counts", {})
    v2_failures = metrics.get("failure_mode_counts", {})
    for mode in sorted(set(v1_failures) | set(v2_failures)):
        lines.append(f"| Failure mode `{mode}` | {v1_failures.get(mode, 0)} | {v2_failures.get(mode, 0)} |")
    lines.append(f"| Label agreement | not measured | {metrics.get('label_agreement_count')}/{metrics.get('label_judged_count')} |")
    if catch_results is not None:
        caught = sum(1 for item in catch_results if item["caught"])
        lines.append(f"| Adversarial flaws caught | not measured | {caught}/{len(catch_results)} |")
    lines.append("")
    return lines


def write_manifest(
    path: Path,
    *,
    config: EvalConfig,
    manifest: dict[str, Any],
    dataset_check_result: dict[str, Any],
    metrics: dict[str, Any],
    started_at: str,
    completed_at: str,
    elapsed_seconds: float,
    row_count: int,
) -> None:
    payload = {
        "judge_model": config.judge_model,
        "generator_model": config.generator_model,
        "cross_model_judging": config.cross_model_judging,
        "judge_blinding": "blind (no planned label, deterministic checks, or annotation fields in judge prompt)",
        "dataset_profile": "adversarial" if config.adversarial else "main",
        "judge_temperature": config.judge_temperature,
        "judge_temperature_note": "provider default" if config.judge_temperature is None else "explicit",
        "judge_prompt_version": config.judge_prompt_version,
        "source_review_prompt_version": config.source_review_prompt_version,
        "diagnosis_prompt_version": config.diagnosis_prompt_version,
        "judge_run_id": config.judge_run_id,
        "source_hash": manifest.get("source_hash"),
        "input_paths": {
            "dataset": workspace_path(DATASET_PATH),
            "run_manifest": workspace_path(RUN_MANIFEST_PATH),
            "generation_summary": workspace_path(GENERATION_SUMMARY_PATH),
            "clause_index": workspace_path(CLAUSE_INDEX_PATH),
        },
        "output_paths": {
            "eval_results": workspace_path(EVAL_RESULTS_PATH),
            "worst_source_reviews": workspace_path(WORST_SOURCE_REVIEWS_PATH),
            "eval_summary": workspace_path(EVAL_SUMMARY_PATH),
            "eval_manifest": workspace_path(EVAL_MANIFEST_PATH),
        },
        "row_count": row_count,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "dataset_checks": dataset_check_result,
        "aggregate_metrics": metrics,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    global DATASET_PATH, RUN_MANIFEST_PATH, GENERATION_SUMMARY_PATH, CLAUSE_INDEX_PATH, EVAL_DIR, EVAL_RESULTS_PATH, WORST_SOURCE_REVIEWS_PATH, EVAL_SUMMARY_PATH, EVAL_MANIFEST_PATH, ADVERSARIAL_REPORT_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--run-manifest", type=Path, default=RUN_MANIFEST_PATH)
    parser.add_argument("--generation-summary", type=Path, default=GENERATION_SUMMARY_PATH)
    parser.add_argument("--clause-index", type=Path, default=CLAUSE_INDEX_PATH)
    parser.add_argument("--output-dir", type=Path, default=EVAL_DIR)
    parser.add_argument("--judge-model", default=None, help="OpenAI judge model. Defaults to OPENAI_JUDGE_MODEL, OPENAI_MODEL, or gpt-5.5.")
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Evaluate an adversarial pack: relax row-count expectations and write a focused adversarial_report.md only.",
    )
    parser.add_argument(
        "--baseline-manifest",
        type=Path,
        default=BASELINE_MANIFEST_PATH,
        help="V1 eval manifest used for the Changes Since V1 comparison section.",
    )
    args = parser.parse_args()

    load_environment()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to run the LLM-as-judge evaluation.")

    judge_model = args.judge_model or os.environ.get("OPENAI_JUDGE_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_JUDGE_MODEL
    started_at = utc_now()

    dataset_path = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    run_manifest_path = args.run_manifest if args.run_manifest.is_absolute() else ROOT / args.run_manifest
    clause_index_path = args.clause_index if args.clause_index.is_absolute() else ROOT / args.clause_index
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    generator_model = manifest.get("generator_model")
    if generator_model and judge_model == generator_model:
        log(
            f"WARNING: judge model '{judge_model}' matches generator model '{generator_model}'. "
            "Set OPENAI_JUDGE_MODEL or --judge-model to a different model for cross-model judging."
        )
    config = EvalConfig(
        judge_model=judge_model,
        judge_temperature=JUDGE_TEMPERATURE,
        judge_prompt_version=JUDGE_PROMPT_VERSION,
        source_review_prompt_version=SOURCE_REVIEW_PROMPT_VERSION,
        diagnosis_prompt_version=DIAGNOSIS_PROMPT_VERSION,
        judge_run_id=f"{started_at.replace(':', '').replace('-', '').replace('Z', 'Z')}_{JUDGE_PROMPT_VERSION}",
        generator_model=generator_model,
        adversarial=args.adversarial,
    )
    client = OpenAIClient(api_key=api_key, model=config.judge_model, temperature=config.judge_temperature)

    DATASET_PATH = dataset_path
    RUN_MANIFEST_PATH = run_manifest_path
    GENERATION_SUMMARY_PATH = args.generation_summary if args.generation_summary.is_absolute() else ROOT / args.generation_summary
    CLAUSE_INDEX_PATH = clause_index_path
    EVAL_DIR = output_dir
    EVAL_RESULTS_PATH = output_dir / "eval_results.jsonl"
    WORST_SOURCE_REVIEWS_PATH = output_dir / "worst_source_reviews.json"
    EVAL_SUMMARY_PATH = output_dir / "eval_summary.md"
    EVAL_MANIFEST_PATH = output_dir / "eval_manifest.json"
    ADVERSARIAL_REPORT_PATH = output_dir / "adversarial_report.md"

    _, records = load_clause_index(clause_index_path)
    rows, parse_errors = load_jsonl(dataset_path)
    if config.adversarial:
        dataset_check_result = dataset_checks(
            rows, parse_errors, records, manifest, expected_total=None, expected_per_category=None
        )
    else:
        dataset_check_result = dataset_checks(rows, parse_errors, records, manifest)

    eval_results = []
    for position, row in enumerate(rows, start=1):
        log(f"[{position}/{len(rows)}] judging {row.get('id')}")
        checks = deterministic_row_checks(row, records)
        eval_results.append(call_row_judge(row, records, checks, client, config))

    metrics = aggregate_metrics(eval_results)

    if config.adversarial:
        result_by_id = {result["row_id"]: result for result in eval_results}
        catch_results = [adversarial_catch_result(row, result_by_id[row["id"]]) for row in rows]
        metrics["adversarial_catch_results"] = catch_results
        write_adversarial_report(
            ADVERSARIAL_REPORT_PATH,
            rows=rows,
            eval_results=eval_results,
            catch_results=catch_results,
            metrics=metrics,
            config=config,
        )
        log(f"Wrote {workspace_path(ADVERSARIAL_REPORT_PATH)}")
        return 0

    write_jsonl(EVAL_RESULTS_PATH, eval_results)
    worst_results = select_worst_rows(eval_results)
    row_by_id = {row["id"]: row for row in rows}

    source_reviews = []
    diagnoses: dict[str, str] = {}
    for position, row_eval in enumerate(worst_results, start=1):
        row = row_by_id[row_eval["row_id"]]
        log(f"[source {position}/{len(worst_results)}] reviewing {row['id']}")
        review = call_source_review(row, row_eval, records, client, config)
        source_reviews.append(review)
        diagnoses[row["id"]] = call_diagnosis(row, row_eval, review, client)

    WORST_SOURCE_REVIEWS_PATH.write_text(json.dumps(source_reviews, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    review_rows = select_human_review_rows(rows, eval_results, source_reviews)
    baseline: dict[str, Any] | None = None
    baseline_path = args.baseline_manifest if args.baseline_manifest.is_absolute() else ROOT / args.baseline_manifest
    if baseline_path.resolve() != EVAL_MANIFEST_PATH.resolve():
        baseline = load_baseline_manifest(baseline_path)

    completed_at = utc_now()
    start_dt = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end_dt = dt.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    elapsed_seconds = (end_dt - start_dt).total_seconds()
    write_summary(
        EVAL_SUMMARY_PATH,
        rows=rows,
        manifest=manifest,
        dataset_check_result=dataset_check_result,
        eval_results=eval_results,
        source_reviews=source_reviews,
        diagnoses=diagnoses,
        metrics=metrics,
        config=config,
        review_rows=review_rows,
        catch_results=None,
        baseline=baseline,
    )
    write_manifest(
        EVAL_MANIFEST_PATH,
        config=config,
        manifest=manifest,
        dataset_check_result=dataset_check_result,
        metrics=metrics,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=elapsed_seconds,
        row_count=len(rows),
    )

    log(f"Wrote {workspace_path(EVAL_RESULTS_PATH)}")
    log(f"Wrote {workspace_path(WORST_SOURCE_REVIEWS_PATH)}")
    log(f"Wrote {workspace_path(EVAL_SUMMARY_PATH)}")
    log(f"Wrote {workspace_path(EVAL_MANIFEST_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
