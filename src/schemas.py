"""Schema constants and deterministic validation for dataset v1."""

from __future__ import annotations

import json
import re
from pathlib import Path
from collections import Counter
from typing import Any


SCHEMA_VERSION = "v1.0"

CATEGORIES = {"clear_answer", "clarification_required", "genuine_ambiguity"}
RESPONSE_MODE_BY_CATEGORY = {
    "clear_answer": "answer_directly",
    "clarification_required": "ask_clarifying_question",
    "genuine_ambiguity": "flag_ambiguity",
}
SOURCE_RECORD_TYPES = {"clause", "section"}
SUPPORT_ROLES = {"primary", "conditional", "context", "ambiguity_source"}
KNOWN_FACT_SOURCES = {"user", "inferred_from_question"}
FACT_TYPES = {
    "status",
    "timing",
    "threshold",
    "party",
    "transaction_type",
    "regulatory_context",
    "other",
}
AMBIGUITY_TYPES = {
    "tos_silent",
    "vague_term",
    "external_regulation",
    "razorpay_discretion",
    "undefined_timeline",
    "undefined_threshold",
    "other",
}

REQUIRED_TOP_LEVEL_FIELDS = {
    "id",
    "issue_id",
    "schema_version",
    "category",
    "response_mode",
    "messages",
    "source_clauses",
    "known_facts",
    "missing_facts",
    "clarifying_questions",
    "conditional_outcomes",
    "ambiguity_reason",
    "coverage_metadata",
    "generation_metadata",
}


def load_clause_index(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    index = json.loads(path.read_text(encoding="utf-8"))
    records = {record["clause_id"]: record for record in index["records"]}
    return index, records


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def quote_supported(quote: str, record: dict[str, Any], records: dict[str, dict[str, Any]]) -> bool:
    if not quote:
        return False
    needle = normalize_text(quote)
    haystacks = [normalize_text(record.get("text", ""))]
    for child_id in record.get("relationships", {}).get("child_clause_ids", []):
        child = records.get(child_id)
        if child:
            haystacks.append(normalize_text(child.get("text", "")))
    return any(needle in haystack for haystack in haystacks)


def quote_supported_by_record_text(quote: str, record: dict[str, Any]) -> bool:
    return bool(quote) and normalize_text(quote) in normalize_text(record.get("text", ""))


def validate_dataset_row(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    missing_fields = sorted(REQUIRED_TOP_LEVEL_FIELDS - set(row))
    if missing_fields:
        errors.append(f"Missing top-level fields: {', '.join(missing_fields)}")
        return errors

    if row["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}.")

    category = row.get("category")
    if category not in CATEGORIES:
        errors.append(f"Invalid category: {category}")
    elif row.get("response_mode") != RESPONSE_MODE_BY_CATEGORY[category]:
        errors.append("response_mode does not match category.")

    validate_messages(row, errors)
    validate_source_clauses(row, records, errors)
    validate_known_facts(row, errors)
    validate_missing_facts(row, errors)
    validate_clarifying_questions(row, errors)
    validate_conditional_outcomes(row, records, errors)
    validate_ambiguity_reason(row, errors)
    validate_coverage_metadata(row, records, errors)
    validate_generation_metadata(row, errors)
    validate_category_shape(row, errors)
    return errors


def validate_messages(row: dict[str, Any], errors: list[str]) -> None:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        errors.append("messages must contain exactly one user and one assistant message.")
        return
    expected_roles = ["user", "assistant"]
    for message, role in zip(messages, expected_roles):
        if message.get("role") != role or not message.get("content"):
            errors.append(f"messages must include a non-empty {role} message in order.")


def validate_source_clauses(
    row: dict[str, Any],
    records: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    source_clauses = row.get("source_clauses")
    if not isinstance(source_clauses, list) or not source_clauses:
        errors.append("source_clauses must be a non-empty list.")
        return

    for item in source_clauses:
        clause_id = item.get("clause_id")
        record = records.get(clause_id)
        if not record:
            errors.append(f"Unknown clause_id: {clause_id}")
            continue
        if item.get("source_record_type") not in SOURCE_RECORD_TYPES:
            errors.append(f"Invalid source_record_type for {clause_id}.")
        elif item["source_record_type"] != record.get("record_type"):
            errors.append(f"source_record_type mismatch for {clause_id}.")
        if item.get("source_span_id") != record.get("source_span_id"):
            errors.append(f"source_span_id mismatch for {clause_id}.")
        if item.get("display_citation") != record.get("display_citation"):
            errors.append(f"display_citation mismatch for {clause_id}.")
        if item.get("support_role") not in SUPPORT_ROLES:
            errors.append(f"Invalid support_role for {clause_id}.")
        if not quote_supported(item.get("relevant_quote", ""), record, records):
            errors.append(f"relevant_quote is not supported by {clause_id}.")
        if (
            record.get("record_type") == "section"
            and len(source_clauses) == 1
            and record.get("relationships", {}).get("child_clause_ids")
            and not quote_supported_by_record_text(item.get("relevant_quote", ""), record)
        ):
            errors.append(
                f"{clause_id} is a section-only citation, but the quote comes from a child span; cite the child clause separately."
            )


def validate_known_facts(row: dict[str, Any], errors: list[str]) -> None:
    facts = row.get("known_facts")
    if not isinstance(facts, list):
        errors.append("known_facts must be a list.")
        return
    for fact in facts:
        if not fact.get("fact") or fact.get("source") not in KNOWN_FACT_SOURCES:
            errors.append("known_facts items need fact and valid source.")


def validate_missing_facts(row: dict[str, Any], errors: list[str]) -> None:
    facts = row.get("missing_facts")
    if not isinstance(facts, list):
        errors.append("missing_facts must be a list.")
        return
    seen_ids: set[str] = set()
    for fact in facts:
        fact_id = fact.get("id")
        if not fact_id or fact_id in seen_ids:
            errors.append("missing_facts items need unique ids.")
        seen_ids.add(fact_id)
        if not fact.get("fact") or not fact.get("why_it_matters"):
            errors.append(f"missing_facts item {fact_id} needs fact and why_it_matters.")
        if fact.get("fact_type") not in FACT_TYPES:
            errors.append(f"Invalid fact_type for {fact_id}.")
        if not isinstance(fact.get("needed_for_clause_ids"), list) or not fact["needed_for_clause_ids"]:
            errors.append(f"missing_facts item {fact_id} needs needed_for_clause_ids.")
        else:
            for clause_id in fact["needed_for_clause_ids"]:
                if clause_id not in row_source_clause_ids(row):
                    errors.append(f"missing_facts item {fact_id} references uncited clause_id: {clause_id}")
        if not isinstance(fact.get("priority"), int):
            errors.append(f"missing_facts item {fact_id} needs integer priority.")


def validate_clarifying_questions(row: dict[str, Any], errors: list[str]) -> None:
    questions = row.get("clarifying_questions")
    if not isinstance(questions, list):
        errors.append("clarifying_questions must be a list.")
        return
    missing_ids = {fact.get("id") for fact in row.get("missing_facts", [])}
    for question in questions:
        targets = question.get("targets_missing_fact_ids")
        if not question.get("question") or not isinstance(targets, list) or not targets:
            errors.append("clarifying_questions items need question and targets.")
            continue
        if not set(targets).issubset(missing_ids):
            errors.append("clarifying question targets unknown missing fact ids.")
        if not isinstance(question.get("priority"), int):
            errors.append("clarifying_questions items need integer priority.")


def validate_conditional_outcomes(
    row: dict[str, Any],
    records: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    outcomes = row.get("conditional_outcomes")
    if not isinstance(outcomes, list):
        errors.append("conditional_outcomes must be a list.")
        return
    missing_ids = {fact.get("id") for fact in row.get("missing_facts", [])}
    for outcome in outcomes:
        required_ids = outcome.get("required_missing_fact_ids")
        source_ids = outcome.get("source_clause_ids")
        if not outcome.get("condition_summary") or not outcome.get("outcome"):
            errors.append("conditional_outcomes items need condition_summary and outcome.")
        if not isinstance(required_ids, list) or not set(required_ids).issubset(missing_ids):
            errors.append("conditional_outcomes items need valid required_missing_fact_ids.")
        if not isinstance(outcome.get("applies_when"), dict):
            errors.append("conditional_outcomes items need applies_when.")
        if not isinstance(source_ids, list) or not source_ids:
            errors.append("conditional_outcomes items need source_clause_ids.")
        else:
            for clause_id in source_ids:
                if clause_id not in records:
                    errors.append(f"conditional outcome references unknown clause_id: {clause_id}")
                elif clause_id not in row_source_clause_ids(row):
                    errors.append(f"conditional outcome references uncited clause_id: {clause_id}")


def validate_ambiguity_reason(row: dict[str, Any], errors: list[str]) -> None:
    reason = row.get("ambiguity_reason")
    if reason is None:
        return
    if not isinstance(reason, dict):
        errors.append("ambiguity_reason must be null or an object.")
        return
    if reason.get("type") not in AMBIGUITY_TYPES or not reason.get("explanation"):
        errors.append("ambiguity_reason needs valid type and explanation.")


def validate_coverage_metadata(
    row: dict[str, Any],
    records: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    coverage = row.get("coverage_metadata")
    if not isinstance(coverage, dict):
        errors.append("coverage_metadata must be an object.")
        return

    cited_records = [records[item["clause_id"]] for item in row.get("source_clauses", []) if item.get("clause_id") in records]
    service_areas = {record.get("taxonomy", {}).get("service_area") for record in cited_records}
    topic_tags = {tag for record in cited_records for tag in record.get("taxonomy", {}).get("topic_tags", [])}
    section_ids = {record.get("hierarchy", {}).get("section_id") for record in cited_records}
    section_ids.update(record["clause_id"] for record in cited_records if record.get("record_type") == "section")

    if coverage.get("service_area") not in service_areas:
        errors.append("coverage_metadata.service_area is not derived from cited records.")
    if not set(coverage.get("topic_tags", [])).issubset(topic_tags):
        errors.append("coverage_metadata.topic_tags contains values not derived from cited records.")
    if not set(coverage.get("source_section_ids", [])).issubset(section_ids):
        errors.append("coverage_metadata.source_section_ids contains values not derived from cited records.")


def validate_generation_metadata(row: dict[str, Any], errors: list[str]) -> None:
    metadata = row.get("generation_metadata")
    required = {"run_id", "source_hash", "source_fetched_at", "generator_model", "generator_temperature", "prompt_version"}
    if not isinstance(metadata, dict):
        errors.append("generation_metadata must be an object.")
        return
    missing = required - set(metadata)
    if missing:
        errors.append(f"generation_metadata missing fields: {', '.join(sorted(missing))}")


def validate_category_shape(row: dict[str, Any], errors: list[str]) -> None:
    category = row.get("category")
    source_roles = {item.get("support_role") for item in row.get("source_clauses", [])}
    if category == "clear_answer":
        if row.get("missing_facts") or row.get("clarifying_questions") or row.get("conditional_outcomes"):
            errors.append("clear_answer rows cannot have missing facts, clarifying questions, or conditional outcomes.")
        if row.get("ambiguity_reason") is not None:
            errors.append("clear_answer rows must have null ambiguity_reason.")
        if "primary" not in source_roles:
            errors.append("clear_answer rows need a primary source clause.")
    elif category == "clarification_required":
        if not row.get("missing_facts") or not row.get("clarifying_questions") or not row.get("conditional_outcomes"):
            errors.append("clarification_required rows need missing facts, clarifying questions, and conditional outcomes.")
        if row.get("ambiguity_reason") is not None:
            errors.append("clarification_required rows must have null ambiguity_reason.")
    elif category == "genuine_ambiguity":
        if not row.get("ambiguity_reason"):
            errors.append("genuine_ambiguity rows need ambiguity_reason.")
        if row.get("missing_facts") or row.get("clarifying_questions") or row.get("conditional_outcomes"):
            errors.append("genuine_ambiguity rows generally cannot have missing facts, questions, or conditional outcomes.")
        if not ({"context", "ambiguity_source"} & source_roles):
            errors.append("genuine_ambiguity rows need context or ambiguity_source support.")


def validate_dataset(
    rows: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    *,
    expected_total: int | None = 45,
    expected_per_category: int | None = 15,
) -> list[dict[str, Any]]:
    failures = []
    seen_ids: set[str] = set()
    seen_issue_ids: set[str] = set()
    dataset_errors: list[str] = []

    if expected_total is not None and len(rows) != expected_total:
        dataset_errors.append(f"Expected {expected_total} rows, found {len(rows)}.")

    category_counts = Counter(row.get("category") for row in rows)
    if expected_per_category is not None:
        for category in CATEGORIES:
            if category_counts.get(category, 0) != expected_per_category:
                dataset_errors.append(
                    f"Expected {expected_per_category} rows for {category}, found {category_counts.get(category, 0)}."
                )
    invalid_categories = sorted(category for category in category_counts if category not in CATEGORIES)
    if invalid_categories:
        dataset_errors.append(f"Unknown categories present: {', '.join(invalid_categories)}")

    for row_number, row in enumerate(rows, start=1):
        row_errors = validate_dataset_row(row, records)
        row_id = row.get("id", f"row_{row_number}")
        issue_id = row.get("issue_id")
        if row_id in seen_ids:
            row_errors.append(f"Duplicate id: {row_id}")
        seen_ids.add(row_id)
        if issue_id in seen_issue_ids:
            row_errors.append(f"Duplicate issue_id: {issue_id}")
        seen_issue_ids.add(issue_id)
        if row_errors:
            failures.append({"row_number": row_number, "id": row_id, "errors": row_errors})

    if dataset_errors:
        failures.insert(0, {"row_number": None, "id": "__dataset__", "errors": dataset_errors})
    return failures


def row_source_clause_ids(row: dict[str, Any]) -> set[str]:
    return {item.get("clause_id") for item in row.get("source_clauses", [])}
