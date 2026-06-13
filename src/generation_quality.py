"""Generation-time quality checks for synthetic dataset rows.

These lints intentionally live outside schemas.py. Schema validation answers
"is this structurally valid and source-traceable?"; this module answers
"is this a useful generated training example?"
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


OPERATIVE_TERMS = {
    "shall",
    "must",
    "may",
    "will",
    "agree",
    "liable",
    "prohibited",
    "required",
    "subject to",
    "reserves the right",
    "entitled to",
    "notwithstanding",
    "in accordance with",
    "at its discretion",
}

VAGUE_CLARIFIERS = {
    "can you provide more details",
    "please provide more details",
    "need more information",
    "more context",
}

GAP_TERMS = {
    "does not define",
    "does not specify",
    "does not state",
    "does not fully resolve",
    "does not reproduce",
    "undefined",
    "external",
    "discretion",
    "silent",
    "not fully specified",
}


@dataclass
class QualityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def is_heading_like(record: dict[str, Any]) -> bool:
    text = record.get("text", "").strip()
    if not text:
        return True
    words = re.findall(r"[A-Za-z]+", text)
    return len(words) <= 6 and text.upper() == text


def is_preamble_like(record: dict[str, Any]) -> bool:
    hierarchy = record.get("hierarchy", {})
    return hierarchy.get("section_title") == "Preamble" or record.get("clause_id", "").endswith(".Preamble")


def is_thin_fragment(record: dict[str, Any]) -> bool:
    text = normalize(record.get("text", ""))
    if len(text) < 40:
        return True
    thin_patterns = [
        r"^part [a-z]+:",
        r"^definitions?:?$",
        r"^payments: terms and conditions$",
        r"^razorpay pa may provide:?$",
    ]
    return any(re.search(pattern, text) for pattern in thin_patterns)


def has_operative_language(record: dict[str, Any]) -> bool:
    text = normalize(record.get("text", ""))
    return any(term in text for term in OPERATIVE_TERMS)


def is_substantive_clause(record: dict[str, Any]) -> bool:
    return (
        record.get("record_type") == "clause"
        and not is_heading_like(record)
        and not is_preamble_like(record)
        and not is_thin_fragment(record)
        and has_operative_language(record)
    )


def has_better_child_clause(record: dict[str, Any], records: dict[str, dict[str, Any]]) -> bool:
    return any(is_substantive_clause(records[child_id]) for child_id in record.get("relationships", {}).get("child_clause_ids", []) if child_id in records)


def is_eligible_support(
    record: dict[str, Any],
    *,
    category: str,
    support_role: str,
    records: dict[str, dict[str, Any]],
) -> bool:
    if support_role in {"primary", "conditional"}:
        return is_substantive_clause(record)
    if support_role == "ambiguity_source":
        if is_substantive_clause(record):
            return True
        if record.get("record_type") == "section" and not is_heading_like(record):
            return not has_better_child_clause(record, records)
        return False
    if support_role == "context":
        return not (is_heading_like(record) and has_better_child_clause(record, records))
    return False


def source_quality_score(record: dict[str, Any]) -> int:
    score = 0
    if record.get("record_type") == "clause":
        score += 3
    if has_operative_language(record):
        score += 3
    if len(normalize(record.get("text", ""))) >= 120:
        score += 1
    if record.get("references", {}).get("external_references"):
        score += 2
    if record.get("references", {}).get("cross_references_resolved"):
        score += 1
    if is_heading_like(record):
        score -= 4
    if is_preamble_like(record):
        score -= 2
    if is_thin_fragment(record):
        score -= 3
    return score


def has_visible_citation(row: dict[str, Any]) -> bool:
    assistant = normalize(row["messages"][1]["content"])
    for source in row.get("source_clauses", []):
        citation = normalize(source.get("display_citation", ""))
        clause_id = normalize(source.get("clause_id", ""))
        clause_number = normalize(str(clause_id.split(".")[-1]))
        if citation and citation in assistant:
            return True
        if "clause " in assistant and clause_number and clause_number in assistant:
            return True
        hierarchy = source.get("display_citation", "").split(",")
        if hierarchy and normalize(hierarchy[0]) in assistant and "clause" in assistant:
            return True
    return False


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] if parts else text


def is_evasive_answer(value: str) -> bool:
    lowered = normalize(value)
    evasive_phrases = (
        "it depends",
        "i cannot determine",
        "cannot determine",
        "need more information",
        "need to know",
        "please clarify",
        "not enough information",
    )
    return value.strip().endswith("?") or any(phrase in lowered for phrase in evasive_phrases)


def is_question_like(value: str) -> bool:
    lowered = normalize(value)
    return value.strip().endswith("?") or lowered.startswith(("can ", "do ", "does ", "did ", "what ", "when ", "where ", "why ", "how ", "is ", "are ", "will "))


def user_question_is_clause_meta(row: dict[str, Any]) -> bool:
    question = normalize(row["messages"][0]["content"])
    return bool(re.search(r"\bwhat does\b.*\b(clause|section|preamble)\b.*\bsay\b", question))


def validate_generation_quality(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> QualityResult:
    result = QualityResult()
    category = row.get("category")
    assistant = row["messages"][1]["content"]
    cited_ids = {source["clause_id"] for source in row.get("source_clauses", [])}

    if not has_visible_citation(row):
        result.warnings.append("missing_visible_citation: assistant response lacks a human-readable citation")

    if user_question_is_clause_meta(row):
        result.warnings.append("generic_answer: user question asks what a clause/section says instead of an operational question")

    for fact in row.get("known_facts", []):
        if is_question_like(fact.get("fact", "")):
            result.warnings.append("weak_known_facts: known fact is phrased as a question")

    for source in row.get("source_clauses", []):
        record = records.get(source.get("clause_id"))
        if record is None:
            # Unknown clause IDs are a hard schema failure; record a lint warning
            # instead of crashing so eval can still process intentionally bad rows.
            result.warnings.append(f"source_quality: unresolved clause_id {source.get('clause_id')}")
            continue
        support_role = source.get("support_role")
        quote = source.get("relevant_quote", "")
        if support_role in {"primary", "conditional", "ambiguity_source"}:
            if len(normalize(quote)) < 40:
                result.warnings.append(f"weak_quote: quote too short for {source['clause_id']}")
            if is_thin_fragment(record):
                result.warnings.append(f"source_quality: thin source {source['clause_id']}")
            if record.get("record_type") == "section" and support_role in {"primary", "conditional"}:
                child_ids = set(record.get("relationships", {}).get("child_clause_ids", []))
                if not child_ids.intersection(cited_ids):
                    result.warnings.append(f"source_quality: section used as {support_role} support without substantive child citation {source['clause_id']}")
            if support_role == "ambiguity_source" and (record.get("record_type") == "section" or is_thin_fragment(record)):
                result.warnings.append(f"source_quality: ambiguity source could be stronger {source['clause_id']}")

    if category == "clear_answer":
        first = normalize(first_sentence(assistant))
        if is_evasive_answer(first_sentence(assistant)):
            result.warnings.append("category_fit: clear answer does not answer directly in the first sentence")
        elif not first.startswith(("yes", "no", "razorpay", "under ", "the tos", "part ", "for ")):
            result.warnings.append("category_fit: clear answer first sentence could be more direct")

    if category == "clarification_required":
        questions = [item.get("question", "") for item in row.get("clarifying_questions", [])]
        if not questions:
            result.warnings.append("category_fit: clarification row has no clarifying question")
        for question in questions:
            lowered = normalize(question)
            if any(vague in lowered for vague in VAGUE_CLARIFIERS):
                result.warnings.append("category_fit: clarifying question is vague")

    if category == "genuine_ambiguity":
        reason = normalize((row.get("ambiguity_reason") or {}).get("explanation", ""))
        assistant_lower = normalize(assistant)
        if not any(term in reason or term in assistant_lower for term in GAP_TERMS):
            result.warnings.append("category_fit: ambiguity row does not identify the exact ToS gap")

    return result


def count_quality_errors(errors: list[str]) -> Counter:
    counter: Counter = Counter()
    for error in errors:
        key = error.split(":", 1)[0]
        counter[key] += 1
    return counter
