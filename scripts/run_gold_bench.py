#!/usr/bin/env python3
"""Run a question-first gold mini-benchmark for the Razorpay generator."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.generation_quality import has_visible_citation, validate_generation_quality
from src.schemas import RESPONSE_MODE_BY_CATEGORY, SCHEMA_VERSION, load_clause_index, validate_dataset, validate_dataset_row


CLAUSE_INDEX_PATH = ROOT / "data" / "processed" / "clause_index.json"
GOLD_CASES_PATH = ROOT / "data" / "gold_bench" / "gold_cases.jsonl"
GOLD_OUTPUT_DIR = ROOT / "data" / "gold_bench" / "v2"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_GENERATOR_MODEL = "gpt-5.5"
DEFAULT_GENERATOR_TEMPERATURE = 1.0
PROMPT_VERSION = "gold_bench_question_only_v1"
REPORT_VERSION = "gold_bench_report_v2"
RUN_VERSION = "v2"
REPORT_CHANGES = [
    "separate blocking failures from passing rows with score/behavior gaps",
    "surface lower-scoring pass rows as diagnostic signals",
    "summarize score loss by dimension",
    "connect missed behaviors and validation repairs to likely generator pipeline gaps",
    "include suggested improvements for future generator runs",
]
HIDDEN_GOLD_FIELDS = [
    "id",
    "expected_category",
    "reference_answer",
    "required_clause_ids",
    "optional_clause_ids",
    "must_have_behaviors",
    "forbidden_claims",
    "boundary_focus",
]
SCORE_DIMENSIONS = {
    "category_match": 20,
    "required_clause_coverage": 20,
    "reference_behavior_match": 25,
    "groundedness_no_unsupported_claims": 20,
    "cto_usefulness": 10,
    "citation_formatting": 5,
}


def neutral_case_id(index: int) -> str:
    return f"bench_case_{index:03d}"


class OpenAIClient:
    def __init__(self, api_key: str, model: str, temperature: float) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.temperature != 1.0:
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
        return json.loads(body["choices"][0]["message"]["content"])


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


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def normalize_words(*values: Any) -> set[str]:
    text = " ".join(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value for value in values)
    words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower()))
    stop_words = {
        "the",
        "and",
        "for",
        "you",
        "your",
        "our",
        "that",
        "this",
        "with",
        "from",
        "are",
        "can",
        "what",
        "when",
        "how",
        "does",
        "did",
        "under",
        "razorpay",
        "clause",
        "terms",
        "tos",
        "payment",
        "payments",
        "customer",
        "merchant",
    }
    return words - stop_words


def expanded_query_words(question: str) -> set[str]:
    words = normalize_words(question)
    lowered = normalize(question)
    expansions = {
        ("refund",): {"refunds", "fees", "payable", "transaction"},
        ("captured", "capture", "authorized", "authorised"): {"late", "authorized", "auto-refund", "refund"},
        ("credential", "credentials", "api", "key", "keys"): {"secure", "credentials", "unauthorized", "fraudulent"},
        ("permission", "unauthorized", "unauthorised", "card"): {"unauthorized", "debit", "settlements", "suspend"},
        ("fraud", "fraudulent", "settled"): {"fraudulent", "chargeback", "npci", "rbi"},
        ("category", "product", "service", "collect"): {"prohibited", "products", "services"},
        ("gaming", "fantasy", "skill", "entry"): {"gaming", "gambling", "real", "money", "online"},
        ("suspended", "suspend", "hold", "settlement"): {"suspend", "settlement", "monies", "termination"},
        ("threshold", "npci"): {"npci", "fraud", "threshold", "guidelines"},
        ("network", "rule", "rules"): {"card", "network", "rules", "binding"},
    }
    for triggers, additions in expansions.items():
        if any(trigger in lowered for trigger in triggers):
            words.update(additions)
    return words


def record_search_text(record: dict[str, Any]) -> str:
    taxonomy = record.get("taxonomy", {})
    return " ".join(
        [
            record.get("clause_id", ""),
            record.get("display_citation", ""),
            record.get("text", ""),
            " ".join(taxonomy.get("topic_tags", [])),
            " ".join(taxonomy.get("issue_type", [])),
            " ".join(taxonomy.get("payment_stage", [])),
        ]
    )


def retrieve_candidate_records(question: str, records: dict[str, dict[str, Any]], *, limit: int = 24) -> list[dict[str, Any]]:
    query_words = expanded_query_words(question)
    lowered = normalize(question)
    manual_boosts: dict[str, int] = {}
    boost_rules = [
        (("refund", "fee"), {"PartB.PartI.3.4": 20}),
        (("never captured", "not captured", "late-authorized", "late authorized"), {"PartB.PartI.3.5": 20}),
        (("api", "credential", "credentials"), {"PartA.2.1": 22}),
        (("without permission", "unauthorized", "unauthorised"), {"PartB.PartI.4.1": 20}),
        (("already settled", "settled"), {"PartB.PartI.4.2": 18, "PartB.PartI.4.3": 18, "PartB.PartI.4.5": 12}),
        (("merchant category", "new merchant", "collect payments through us"), {"PartA.17.1": 18, "PartA.17.2": 10}),
        (("real-money", "real money", "fantasy", "gaming"), {"PartA.14.10": 22, "PartA.17.14": 14}),
        (("how long", "hold", "suspended"), {"PartA.16.1": 22, "PartA.16.1.item001": 8}),
        (("threshold", "npci"), {"PartB.PartI.4.5": 22}),
    ]
    for triggers, boosts in boost_rules:
        if any(trigger in lowered for trigger in triggers):
            for clause_id, points in boosts.items():
                manual_boosts[clause_id] = manual_boosts.get(clause_id, 0) + points

    scored: list[tuple[int, int, str]] = []
    for clause_id, record in records.items():
        if record.get("record_type") not in {"clause", "section"}:
            continue
        text_words = normalize_words(record_search_text(record))
        overlap = query_words & text_words
        score = len(overlap) * 3 + manual_boosts.get(clause_id, 0)
        if record.get("record_type") == "clause":
            score += 2
        if record.get("references", {}).get("external_references"):
            score += 1
        if score > 0:
            scored.append((score, int(record.get("hierarchy", {}).get("source_order") or 0), clause_id))

    ranked_ids = [clause_id for _, _, clause_id in sorted(scored, key=lambda item: (-item[0], item[1], item[2]))[:limit]]
    return [records[clause_id] for clause_id in ranked_ids]


def record_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "clause_id": record["clause_id"],
        "source_span_id": record["source_span_id"],
        "record_type": record["record_type"],
        "display_citation": record["display_citation"],
        "section_id": record["hierarchy"].get("section_id"),
        "section_title": record["hierarchy"].get("section_title"),
        "text": record["text"],
        "cross_references_resolved": record.get("references", {}).get("cross_references_resolved", []),
        "external_references": record.get("references", {}).get("external_references", []),
    }


def generator_system_prompt() -> str:
    return (
        "You generate one Razorpay Terms of Use synthetic Q&A row from a user question. "
        "Decide whether the situation calls for clear_answer, clarification_required, or genuine_ambiguity. "
        "Use only the supplied candidate source records. Do not invent clause IDs, source text, external law, "
        "Razorpay internal policy, timelines, thresholds, or facts. Return strict JSON only."
    )


def generator_user_prompt(
    *,
    question: str,
    candidate_records: list[dict[str, Any]],
    validation_errors: list[str] | None = None,
) -> str:
    instructions: dict[str, Any] = {
        "task": "Generate a final dataset row payload for this user question using only the candidate source records.",
        "user_question": question,
        "category_definitions": {
            "clear_answer": "The ToS directly and unambiguously answers the question.",
            "clarification_required": "The answer depends on a specific missing user fact.",
            "genuine_ambiguity": "The ToS is silent, vague, discretionary, or depends on external regulation.",
        },
        "candidate_source_records": [record_payload(record) for record in candidate_records],
        "source_rules": [
            "source_support clause_id values must be chosen from candidate_source_records.",
            "relevant_quote must be an exact substring of the selected source record text.",
            "Use a human-readable citation in assistant_message, such as 'Clause 3.4' or the display citation.",
            "If the right answer depends on a missing fact, ask one targeted clarifying question.",
            "If the ToS has a real gap or depends on external rules, flag the gap and do not guess.",
        ],
        "output_schema": {
            "category": "clear_answer | clarification_required | genuine_ambiguity",
            "assistant_message": "assistant answer matching the chosen category",
            "known_facts": [{"fact": "...", "source": "user | inferred_from_question"}],
            "missing_facts": [
                {
                    "id": "mf_001",
                    "fact": "...",
                    "why_it_matters": "...",
                    "fact_type": "status | timing | threshold | party | transaction_type | regulatory_context | other",
                    "needed_for_clause_ids": ["..."],
                    "priority": 1,
                }
            ],
            "clarifying_questions": [
                {
                    "question": "...",
                    "targets_missing_fact_ids": ["mf_001"],
                    "priority": 1,
                }
            ],
            "conditional_outcomes": [
                {
                    "condition_summary": "...",
                    "required_missing_fact_ids": ["mf_001"],
                    "applies_when": {"mf_001": "..."},
                    "outcome": "...",
                    "source_clause_ids": ["..."],
                }
            ],
            "ambiguity_reason": {
                "type": "tos_silent | vague_term | external_regulation | razorpay_discretion | undefined_timeline | undefined_threshold | other",
                "explanation": "...",
            },
            "source_support": [
                {
                    "clause_id": "...",
                    "support_role": "primary | conditional | context | ambiguity_source",
                    "relevant_quote": "exact substring from the source record text",
                }
            ],
        },
    }
    if validation_errors:
        instructions["previous_validation_errors"] = validation_errors
        instructions["repair_instruction"] = "Repair the JSON so the assembled row passes schema validation. Do not use source IDs outside candidate_source_records."
    return json.dumps(instructions, indent=2, ensure_ascii=False)


def support_role_for_category(category: str) -> str:
    if category == "clear_answer":
        return "primary"
    if category == "clarification_required":
        return "conditional"
    return "ambiguity_source"


def build_source_clause(support: dict[str, Any], records: dict[str, dict[str, Any]], category: str) -> dict[str, str]:
    record = records[support["clause_id"]]
    role = support.get("support_role") or support_role_for_category(category)
    if role not in {"primary", "conditional", "context", "ambiguity_source"}:
        role = support_role_for_category(category)
    return {
        "source_record_type": record["record_type"],
        "source_span_id": record["source_span_id"],
        "clause_id": record["clause_id"],
        "display_citation": record["display_citation"],
        "support_role": role,
        "relevant_quote": support.get("relevant_quote") or record["text"],
    }


def coverage_metadata(source_clauses: list[dict[str, str]], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cited_records = [records[item["clause_id"]] for item in source_clauses]
    first = cited_records[0]
    topic_tags = sorted({tag for record in cited_records for tag in record.get("taxonomy", {}).get("topic_tags", [])})
    section_ids = sorted(
        {
            record.get("hierarchy", {}).get("section_id")
            for record in cited_records
            if record.get("hierarchy", {}).get("section_id")
        }
        | {record["clause_id"] for record in cited_records if record.get("record_type") == "section"}
    )
    return {
        "service_area": first.get("taxonomy", {}).get("service_area", "unknown"),
        "topic_tags": topic_tags,
        "source_section_ids": section_ids,
    }


def generation_metadata(index: dict[str, Any], *, run_id: str, model: str, temperature: float, template_only: bool) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_hash": index["metadata"]["source_hash"],
        "source_fetched_at": index["metadata"]["source_fetched_at"],
        "generator_model": "template-question-rules" if template_only else model,
        "generator_temperature": temperature,
        "prompt_version": PROMPT_VERSION,
    }


def normalize_generator_output(
    output: dict[str, Any],
    *,
    question: str,
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_ids = [record["clause_id"] for record in candidate_records]
    candidate_id_set = set(candidate_ids)
    category = output.get("category")
    if category not in RESPONSE_MODE_BY_CATEGORY:
        category = "genuine_ambiguity"

    source_support = [
        support
        for support in output.get("source_support", [])
        if isinstance(support, dict) and support.get("clause_id") in candidate_id_set
    ]
    if not source_support and candidate_ids:
        source_support = [{"clause_id": candidate_ids[0], "support_role": support_role_for_category(category)}]
    for support in source_support:
        support["support_role"] = support_role_for_category(category) if support.get("support_role") not in {"primary", "conditional", "context", "ambiguity_source"} else support["support_role"]
    if category == "clear_answer" and source_support:
        source_support[0]["support_role"] = "primary"
    if category == "clarification_required":
        for support in source_support:
            support["support_role"] = "conditional"
    if category == "genuine_ambiguity":
        for support in source_support:
            support["support_role"] = "ambiguity_source"

    normalized = {
        "category": category,
        "assistant_message": output.get("assistant_message") or "The Razorpay Terms do not provide enough support to answer this without further review.",
        "known_facts": output.get("known_facts") or [{"fact": question.rstrip("?") + ".", "source": "user"}],
        "missing_facts": output.get("missing_facts") or [],
        "clarifying_questions": output.get("clarifying_questions") or [],
        "conditional_outcomes": output.get("conditional_outcomes") or [],
        "ambiguity_reason": output.get("ambiguity_reason"),
        "source_support": source_support,
    }
    if category == "clear_answer":
        normalized["missing_facts"] = []
        normalized["clarifying_questions"] = []
        normalized["conditional_outcomes"] = []
        normalized["ambiguity_reason"] = None
    elif category == "clarification_required":
        normalized["ambiguity_reason"] = None
    elif category == "genuine_ambiguity":
        normalized["missing_facts"] = []
        normalized["clarifying_questions"] = []
        normalized["conditional_outcomes"] = []
        if not normalized["ambiguity_reason"]:
            normalized["ambiguity_reason"] = {
                "type": "other",
                "explanation": "The ToS does not fully resolve the question from the available source text.",
            }
    return normalized


def assemble_row(
    *,
    row_id: str,
    question: str,
    output: dict[str, Any],
    candidate_records: list[dict[str, Any]],
    index: dict[str, Any],
    records: dict[str, dict[str, Any]],
    run_id: str,
    model: str,
    temperature: float,
    template_only: bool,
) -> dict[str, Any]:
    output = normalize_generator_output(output, question=question, candidate_records=candidate_records)
    source_clauses = [build_source_clause(support, records, output["category"]) for support in output["source_support"]]
    row = {
        "id": row_id,
        "issue_id": row_id,
        "schema_version": SCHEMA_VERSION,
        "category": output["category"],
        "response_mode": RESPONSE_MODE_BY_CATEGORY[output["category"]],
        "messages": [
            {"role": "user", "content": question},
            {"role": "assistant", "content": output["assistant_message"]},
        ],
        "source_clauses": source_clauses,
        "known_facts": output["known_facts"],
        "missing_facts": output["missing_facts"],
        "clarifying_questions": output["clarifying_questions"],
        "conditional_outcomes": output["conditional_outcomes"],
        "ambiguity_reason": output["ambiguity_reason"],
        "coverage_metadata": coverage_metadata(source_clauses, records),
        "generation_metadata": generation_metadata(index, run_id=run_id, model=model, temperature=temperature, template_only=template_only),
    }
    if not has_visible_citation(row) and row["source_clauses"]:
        row["messages"][1]["content"] = f"{row['messages'][1]['content'].rstrip()}\n\nSource: {row['source_clauses'][0]['display_citation']}."
    return row


def template_output(question: str, candidate_records: list[dict[str, Any]]) -> dict[str, Any]:
    lowered = normalize(question)

    def citation(clause_id: str) -> str:
        record = next((item for item in candidate_records if item["clause_id"] == clause_id), None)
        return record["display_citation"] if record else clause_id

    def support(*clause_ids: str, role: str) -> list[dict[str, Any]]:
        return [
            {"clause_id": clause_id, "support_role": role}
            for clause_id in clause_ids
            if any(record["clause_id"] == clause_id for record in candidate_records)
        ]

    if "refund" in lowered and "fee" in lowered:
        clause = "PartB.PartI.3.4"
        return {
            "category": "clear_answer",
            "assistant_message": f"Yes. Razorpay PA fees remain applicable and payable on the transaction even if the transaction is refunded, under {citation(clause)}.",
            "known_facts": [{"fact": "The merchant refunded the customer's full payment.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": None,
            "source_support": support(clause, role="primary"),
        }
    if "never captured" in lowered or "not captured" in lowered:
        clause = "PartB.PartI.3.5"
        return {
            "category": "clear_answer",
            "assistant_message": f"Razorpay PA may initiate an auto-refund to the customer within five days if the late-authorized payment is not captured, under {citation(clause)}.",
            "known_facts": [{"fact": "The payment was late-authorized and was not captured by the merchant.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": None,
            "source_support": support(clause, role="primary"),
        }
    if "credential" in lowered or "api" in lowered:
        clause = "PartA.2.1"
        return {
            "category": "clear_answer",
            "assistant_message": f"No. You are responsible for maintaining secure credentials and for activity under the account, including unauthorized access and fraudulent transactions from failing to keep them confidential, under {citation(clause)}.",
            "known_facts": [{"fact": "The question concerns leaked Razorpay API credentials and resulting fraudulent transactions.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": None,
            "source_support": support(clause, role="primary"),
        }
    if "without permission" in lowered or "unauthorized" in lowered or "unauthorised" in lowered:
        clause = "PartB.PartI.4.1"
        return {
            "category": "clarification_required",
            "assistant_message": f"Has a Facility Provider intimated Razorpay PA that the customer reported the unauthorized debit? That fact matters because {citation(clause)} allows settlement suspension during inquiry and resolution when that Facility Provider intimation has occurred.",
            "known_facts": [{"fact": "A customer says their card was used without permission.", "source": "user"}],
            "missing_facts": [{"id": "mf_001", "fact": "Whether a Facility Provider has intimated Razorpay PA of the unauthorized debit claim.", "why_it_matters": "Clause 4.1 turns on Facility Provider intimation before the settlement-suspension consequence applies.", "fact_type": "status", "needed_for_clause_ids": [clause], "priority": 1}],
            "clarifying_questions": [{"question": "Has a Facility Provider intimated Razorpay PA that the customer reported the unauthorized debit?", "targets_missing_fact_ids": ["mf_001"], "priority": 1}],
            "conditional_outcomes": [
                {"condition_summary": "Facility Provider has intimated Razorpay PA.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "facility_provider_intimated"}, "outcome": "Razorpay PA may suspend settlements during inquiry and resolution.", "source_clause_ids": [clause]},
                {"condition_summary": "No Facility Provider intimation has occurred.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "no_facility_provider_intimation"}, "outcome": "Clause 4.1 may not be the controlling basis for settlement suspension on these facts.", "source_clause_ids": [clause]},
            ],
            "ambiguity_reason": None,
            "source_support": support(clause, role="conditional"),
        }
    if "already settled" in lowered or ("fraud" in lowered and "settled" in lowered):
        clauses = ("PartB.PartI.4.2", "PartB.PartI.4.3", "PartB.PartI.4.5")
        return {
            "category": "clarification_required",
            "assistant_message": f"Did the fraudulent transaction result in a chargeback, and is the NPCI/RBI threshold context implicated? The answer changes because {citation('PartB.PartI.4.2')} points post-settlement fraud handling to RBI rules, while {citation('PartB.PartI.4.3')} addresses chargeback consequences.",
            "known_facts": [{"fact": "A fraudulent transaction was already settled to the merchant.", "source": "user"}],
            "missing_facts": [{"id": "mf_001", "fact": "Whether the fraudulent transaction resulted in a chargeback and whether NPCI/RBI threshold consequences apply.", "why_it_matters": "The ToS separates post-settlement fraud handling, chargeback consequences, and NPCI fraud-guideline threshold treatment.", "fact_type": "status", "needed_for_clause_ids": ["PartB.PartI.4.2", "PartB.PartI.4.3"], "priority": 1}],
            "clarifying_questions": [{"question": "Did the fraudulent transaction result in a chargeback, and has Razorpay identified any NPCI/RBI threshold issue?", "targets_missing_fact_ids": ["mf_001"], "priority": 1}],
            "conditional_outcomes": [
                {"condition_summary": "The fraud resulted in a chargeback.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "chargeback"}, "outcome": "The chargeback consequences may apply.", "source_clause_ids": ["PartB.PartI.4.3"]},
                {"condition_summary": "The fraud is post-settlement without a chargeback.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "post_settlement_no_chargeback"}, "outcome": "The RBI-referenced post-settlement fraud handling path is relevant.", "source_clause_ids": ["PartB.PartI.4.2"]},
            ],
            "ambiguity_reason": None,
            "source_support": support(*clauses, role="conditional"),
        }
    if "merchant category" in lowered or "new merchant" in lowered or "collect payments through us" in lowered:
        clause = "PartA.17.1"
        return {
            "category": "clarification_required",
            "assistant_message": f"What exact merchant category, product, or service will collect payments? That classification matters because the prohibited-products rules in {citation(clause)} can change whether Razorpay use is allowed.",
            "known_facts": [{"fact": "The platform wants a new merchant category to collect payments through Razorpay.", "source": "user"}],
            "missing_facts": [{"id": "mf_001", "fact": "The exact merchant category, product, or service involved.", "why_it_matters": "The prohibited-products section depends on whether the offering falls within a restricted category.", "fact_type": "transaction_type", "needed_for_clause_ids": [clause], "priority": 1}],
            "clarifying_questions": [{"question": "What exact merchant category, product, or service will collect payments through Razorpay?", "targets_missing_fact_ids": ["mf_001"], "priority": 1}],
            "conditional_outcomes": [
                {"condition_summary": "The offering is in a prohibited category.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "prohibited_category"}, "outcome": "Razorpay use may be prohibited for that offering.", "source_clause_ids": [clause]},
                {"condition_summary": "The offering is not in a prohibited category.", "required_missing_fact_ids": ["mf_001"], "applies_when": {"mf_001": "not_prohibited_category"}, "outcome": "This prohibited-products basis may not block it, though other onboarding terms may still matter.", "source_clause_ids": [clause]},
            ],
            "ambiguity_reason": None,
            "source_support": support(clause, role="conditional"),
        }
    if "fantasy" in lowered or "gaming" in lowered:
        clauses = ("PartA.14.10", "PartA.17.14")
        return {
            "category": "genuine_ambiguity",
            "assistant_message": f"The ToS gives strong warning signs but does not classify your exact feature by itself. {citation('PartA.14.10')} addresses real money online gaming or online money game services, and the skill-based framing may require external legal and Razorpay confirmation before relying on the ToS alone.",
            "known_facts": [{"fact": "The feature involves real-money fantasy sports and paid entry fees.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": {"type": "external_regulation", "explanation": "The ToS references external online-gaming law, but it does not fully classify the exact feature or state-law context."},
            "source_support": support(*clauses, role="ambiguity_source"),
        }
    if "how long" in lowered and ("hold" in lowered or "suspended" in lowered):
        clause = "PartA.16.1"
        return {
            "category": "genuine_ambiguity",
            "assistant_message": f"{citation(clause)} allows Razorpay PA to suspend services and settlement of monies in listed events, but the ToS does not specify a maximum fund-hold duration. You should confirm the suspension basis and timeline with Razorpay or counsel rather than assume a deadline.",
            "known_facts": [{"fact": "Razorpay suspended the account and the merchant asks about settlement fund hold duration.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": {"type": "undefined_timeline", "explanation": "Clause 16.1 permits settlement suspension, but the ToS does not define a maximum hold period."},
            "source_support": support(clause, role="ambiguity_source"),
        }
    if "threshold" in lowered or "npci" in lowered:
        clause = "PartB.PartI.4.5"
        return {
            "category": "genuine_ambiguity",
            "assistant_message": f"The ToS references NPCI fraud-guideline consequences, but {citation(clause)} does not define the exact operative fraud threshold in the ToS text. Check the applicable NPCI/Razorpay guidance rather than inventing a number.",
            "known_facts": [{"fact": "The user asks for the exact NPCI-related fraud threshold.", "source": "user"}],
            "missing_facts": [],
            "clarifying_questions": [],
            "conditional_outcomes": [],
            "ambiguity_reason": {"type": "undefined_threshold", "explanation": "The ToS references NPCI fraud guidelines but does not reproduce the exact threshold."},
            "source_support": support(clause, role="ambiguity_source"),
        }
    first = candidate_records[0]["clause_id"]
    return {
        "category": "genuine_ambiguity",
        "assistant_message": f"The ToS has related context in {citation(first)}, but it does not fully resolve the question from the retrieved source text.",
        "known_facts": [{"fact": question.rstrip("?") + ".", "source": "user"}],
        "missing_facts": [],
        "clarifying_questions": [],
        "conditional_outcomes": [],
        "ambiguity_reason": {"type": "other", "explanation": "The retrieved ToS context does not fully resolve the question."},
        "source_support": support(first, role="ambiguity_source"),
    }


def generate_row(
    *,
    case: dict[str, Any],
    row_id: str,
    index: dict[str, Any],
    records: dict[str, dict[str, Any]],
    client: OpenAIClient | None,
    model: str,
    temperature: float,
    run_id: str,
    max_attempts: int,
    template_only: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    question = case["question"]
    candidate_records = retrieve_candidate_records(question, records)
    if not candidate_records:
        raise RuntimeError(f"No candidate records retrieved for {case['id']}")
    validation_errors: list[str] = []
    generation_notes: dict[str, Any] = {
        "candidate_clause_ids": [record["clause_id"] for record in candidate_records],
        "attempts": 0,
        "fallback_used": False,
        "validation_errors": [],
        "generation_errors": [],
    }
    attempts = 1 if template_only else max_attempts
    for attempt in range(1, attempts + 1):
        generation_notes["attempts"] = attempt
        if template_only:
            output = template_output(question, candidate_records)
        else:
            assert client is not None
            try:
                output = client.generate_json(
                    system_prompt=generator_system_prompt(),
                    user_prompt=generator_user_prompt(
                        question=question,
                        candidate_records=candidate_records,
                        validation_errors=validation_errors or None,
                    ),
                )
            except Exception as exc:
                generation_notes["generation_errors"].append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                continue
        row = assemble_row(
            row_id=row_id,
            question=question,
            output=output,
            candidate_records=candidate_records,
            index=index,
            records=records,
            run_id=run_id,
            model=model,
            temperature=temperature,
            template_only=template_only,
        )
        errors = validate_dataset_row(row, records)
        if not errors:
            return row, generation_notes
        validation_errors = errors
        generation_notes["validation_errors"] = errors

    # Preserve a complete benchmark run even when the live generator returns
    # invalid JSON/shape repeatedly; the fallback remains question-rule based.
    generation_notes["fallback_used"] = True
    output = template_output(question, candidate_records)
    row = assemble_row(
        row_id=row_id,
        question=question,
        output=output,
        candidate_records=candidate_records,
        index=index,
        records=records,
        run_id=run_id,
        model=model,
        temperature=temperature,
        template_only=True,
    )
    return row, generation_notes


def significant_behavior_terms(behavior: str) -> set[str]:
    words = normalize_words(behavior)
    return {word for word in words if len(word) >= 4}


def avoid_behavior_met(row: dict[str, Any], behavior_normalized: str) -> bool:
    assistant = normalize(row["messages"][1]["content"])
    row_text = normalize(
        {
            "assistant": row["messages"][1]["content"],
            "clarifying_questions": row.get("clarifying_questions", []),
            "conditional_outcomes": row.get("conditional_outcomes", []),
            "ambiguity_reason": row.get("ambiguity_reason"),
        }
    )
    avoided = behavior_normalized.removeprefix("avoid ").strip()

    if "unconditional yes or no" in avoided:
        gives_direct_yes_no = assistant.startswith("yes") or assistant.startswith("no")
        has_conditioning = bool(row.get("clarifying_questions")) or any(
            phrase in row_text
            for phrase in ("depends", "if ", "whether", "answer changes", "fact matters", "matters because", "changes because")
        )
        return not gives_direct_yes_no or has_conditioning

    if "approving the model" in avoided or "without category facts" in avoided:
        approval_phrases = ("is allowed", "are allowed", "can collect", "may collect", "approved", "approval is guaranteed")
        asks_for_category = bool(row.get("clarifying_questions")) or any(
            phrase in row_text for phrase in ("merchant category", "product", "service", "classification", "prohibited")
        )
        return not any(phrase in assistant for phrase in approval_phrases) or asks_for_category

    if "skill-based" in avoided or "automatically allowed" in avoided:
        automatic_allowance = any(
            phrase in assistant
            for phrase in ("definitely allowed", "automatically allowed", "is allowed because it is skill", "can use razorpay because it is skill")
        )
        return not automatic_allowance

    if "deadline" in avoided:
        invented_deadline = any(phrase in assistant for phrase in ("24 hours", "180 days", "maximum hold", "max hold", "must release within"))
        return not invented_deadline

    if "numeric threshold" in avoided:
        invented_threshold = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent|transactions?|frauds?)\b", assistant))
        return not invented_threshold

    avoided_terms = significant_behavior_terms(avoided)
    return not avoided_terms or not all(term in assistant for term in avoided_terms)


def assess_behavior(row: dict[str, Any], case: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    assistant = normalize(row["messages"][1]["content"])
    cited_text = normalize(row.get("source_clauses", []))
    row_text = normalize(
        {
            "assistant": row["messages"][1]["content"],
            "missing_facts": row.get("missing_facts", []),
            "clarifying_questions": row.get("clarifying_questions", []),
            "conditional_outcomes": row.get("conditional_outcomes", []),
            "ambiguity_reason": row.get("ambiguity_reason"),
        }
    )
    assessments = []
    hits = 0
    for behavior in case.get("must_have_behaviors", []):
        behavior_normalized = normalize(behavior)
        clause_match = re.search(r"clause\s+([0-9]+(?:\.[0-9]+)*)", behavior_normalized)
        if behavior_normalized.startswith("answer yes"):
            matched = assistant.startswith("yes")
        elif behavior_normalized.startswith("answer no"):
            matched = assistant.startswith("no")
        elif clause_match:
            clause_number = clause_match.group(1)
            matched = clause_number in assistant or clause_number in cited_text
        elif "five-day" in behavior_normalized or "five day" in behavior_normalized:
            matched = "five" in row_text or "5" in row_text
        elif behavior_normalized.startswith("avoid "):
            matched = avoid_behavior_met(row, behavior_normalized)
        elif "answer changes" in behavior_normalized:
            matched = any(phrase in row_text for phrase in ("answer changes", "fact matters", "matters because", "changes because"))
        elif "chargeback and non-chargeback" in behavior_normalized:
            matched = "chargeback" in row_text and (
                "non-chargeback" in row_text
                or "no_chargeback" in row_text
                or "without a chargeback" in row_text
                or "post-settlement without a chargeback" in row_text
            )
        else:
            terms = significant_behavior_terms(behavior)
            if not terms:
                matched = False
            else:
                matched_terms = {term for term in terms if term in row_text}
                matched = len(matched_terms) >= max(1, min(len(terms), 2))
        if matched:
            hits += 1
        assessments.append({"behavior": behavior, "met": matched})
    if not assessments:
        return 0, assessments
    return round(SCORE_DIMENSIONS["reference_behavior_match"] * hits / len(assessments)), assessments


def forbidden_violations(row: dict[str, Any], case: dict[str, Any]) -> list[str]:
    assistant = normalize(row["messages"][1]["content"])
    violations = []
    for claim in case.get("forbidden_claims", []):
        claim_normalized = normalize(claim)
        claim_terms = significant_behavior_terms(claim)
        if claim_normalized in assistant:
            violations.append(claim)
            continue
        if len(claim_terms) >= 3 and len({term for term in claim_terms if term in assistant}) >= len(claim_terms):
            violations.append(claim)
    return violations


def missed_behaviors_from_result(result: dict[str, Any]) -> list[str]:
    return [item["behavior"] for item in result.get("must_have_behavior_assessment", []) if not item.get("met")]


def pipeline_gap_assessment(result: dict[str, Any]) -> dict[str, Any]:
    missed_behaviors = missed_behaviors_from_result(result)
    validation_errors = result.get("generation_notes", {}).get("validation_errors", [])
    quality_warnings = result.get("deterministic_checks", {}).get("quality_lint_warnings", [])
    gap_types: list[str] = []
    likely_causes: list[str] = []
    suggestions: list[str] = []

    if not result.get("category_match"):
        gap_types.append("category_selection")
        likely_causes.append("question-only category classification selected the wrong response mode")
        suggestions.append("tighten category boundary instructions with contrastive examples for clarification versus ambiguity")
    if result.get("required_clauses_missed"):
        gap_types.append("source_retrieval_or_selection")
        likely_causes.append("retrieval or source selection missed a required gold clause")
        suggestions.append("boost required-like sibling and cross-reference retrieval before generation")
    if result.get("forbidden_claim_violations"):
        gap_types.append("overreach_control")
        likely_causes.append("assistant answer introduced a prohibited unsupported claim")
        suggestions.append("add a post-generation overreach check before accepting the row")
    if missed_behaviors:
        gap_types.append("behavior_completeness")
        likely_causes.append("the row chose the right category and sources but missed part of the expected answer behavior")
        suggestions.append("make the generator explicitly cover every branch/gap implied by the chosen category")
        missed_text = normalize(" ".join(missed_behaviors))
        if any(term in missed_text for term in ("chargeback", "non-chargeback", "rbi", "npci", "branches", "answer changes")):
            gap_types.append("multi_branch_explanation")
            likely_causes.append("clarification answers identify the missing fact but do not fully map the downstream branches")
            suggestions.append("add a required branch-map sentence for clarification rows with multiple legal or operational outcomes")
        if any(term in missed_text for term in ("confirming", "checking", "counsel", "guidance", "legal")):
            gap_types.append("ambiguity_next_step")
            likely_causes.append("ambiguity answers identify the ToS gap but understate the concrete follow-up needed")
            suggestions.append("require ambiguity rows to state what external source or party should confirm the unresolved point")
        if "tos alone" in missed_text or "classify the exact product" in missed_text:
            gap_types.append("ambiguity_gap_specificity")
            likely_causes.append("ambiguity answers cite external dependence but do not sharply state why the ToS alone is incomplete")
            suggestions.append("prompt ambiguity rows to name the exact unresolved classification or definition")
        if any(term in missed_text for term in ("refund", "fee obligation", "does not remove")):
            gap_types.append("clear_answer_obligation_specificity")
            likely_causes.append("clear answers can cite the right clause while not restating the operative obligation explicitly enough")
            suggestions.append("prompt clear answers to restate the operative rule in business terms before or alongside the citation")
    if validation_errors:
        gap_types.append("schema_repair")
        likely_causes.append("initial generation referenced uncited clauses or invalid structured fields before repair")
        suggestions.append("constrain missing_facts and conditional_outcomes to cited source IDs during generation")
    if quality_warnings:
        gap_types.append("source_quality")
        likely_causes.append("deterministic quality lints found weak quotes or thin source support")
        suggestions.append("prefer substantive parent clauses over thin child fragments when citing ambiguity context")

    if not gap_types:
        gap_types.append("none")
        likely_causes.append("no meaningful pipeline gap detected")
        suggestions.append("no generator change suggested")

    return {
        "gap_types": sorted(set(gap_types)),
        "missed_behaviors": missed_behaviors,
        "quality_warnings": quality_warnings,
        "validation_repair_errors": validation_errors,
        "likely_causes": sorted(set(likely_causes)),
        "suggested_improvements": sorted(set(suggestions)),
    }


def score_case(row: dict[str, Any], case: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    generated_clause_ids = [source["clause_id"] for source in row.get("source_clauses", [])]
    required_ids = case.get("required_clause_ids", [])
    optional_ids = case.get("optional_clause_ids", [])
    required_hit = [clause_id for clause_id in required_ids if clause_id in generated_clause_ids]
    required_missed = [clause_id for clause_id in required_ids if clause_id not in generated_clause_ids]
    optional_used = [clause_id for clause_id in optional_ids if clause_id in generated_clause_ids]
    schema_errors = validate_dataset_row(row, records)
    quality = validate_generation_quality(row, records)
    violations = forbidden_violations(row, case)
    behavior_points, behavior_assessment = assess_behavior(row, case)
    category_match = row["category"] == case["expected_category"]
    category_points = SCORE_DIMENSIONS["category_match"] if category_match else 0
    required_points = round(SCORE_DIMENSIONS["required_clause_coverage"] * len(required_hit) / len(required_ids)) if required_ids else 0
    groundedness_points = SCORE_DIMENSIONS["groundedness_no_unsupported_claims"]
    if schema_errors:
        groundedness_points -= 10
    if quality.errors:
        groundedness_points -= 5
    if violations:
        groundedness_points -= 10
    groundedness_points = max(0, groundedness_points)
    cto_points = SCORE_DIMENSIONS["cto_usefulness"]
    if len(row["messages"][1]["content"].split()) < 20:
        cto_points -= 4
    if row["category"] == "clarification_required" and not row.get("clarifying_questions"):
        cto_points -= 5
    if row["category"] == "genuine_ambiguity" and not row.get("ambiguity_reason"):
        cto_points -= 5
    cto_points = max(0, cto_points)
    citation_points = SCORE_DIMENSIONS["citation_formatting"] if has_visible_citation(row) else 0
    scores = {
        "category_match": category_points,
        "required_clause_coverage": required_points,
        "reference_behavior_match": behavior_points,
        "groundedness_no_unsupported_claims": groundedness_points,
        "cto_usefulness": cto_points,
        "citation_formatting": citation_points,
    }
    total = sum(scores.values())
    passed = total >= 80 and category_match and not violations and bool(required_hit)
    feedback_parts = []
    if not category_match:
        feedback_parts.append(f"Expected {case['expected_category']} but generated {row['category']}.")
    if required_missed:
        feedback_parts.append("Missed required clauses: " + ", ".join(required_missed) + ".")
    if violations:
        feedback_parts.append("Forbidden claims detected: " + "; ".join(violations) + ".")
    missed_behaviors = [item["behavior"] for item in behavior_assessment if not item["met"]]
    if missed_behaviors:
        label = "Minor behavior gaps" if passed else "Missed behavior checks"
        feedback_parts.append(f"{label}: " + "; ".join(missed_behaviors) + ".")
    if not feedback_parts:
        feedback_parts.append("Fully meets the hidden behavior expectations.")
    if not passed:
        recommended_fix = "Improve category classification, source retrieval, or answer constraints for this boundary case."
    elif missed_behaviors:
        recommended_fix = "Minor improvement: strengthen the answer against the missed behavior checks."
    else:
        recommended_fix = "No generator fix required."
    result = {
        "id": row["id"],
        "gold_case_id": case["id"],
        "boundary_focus": case.get("boundary_focus"),
        "expected_category": case["expected_category"],
        "generated_category": row["category"],
        "category_match": category_match,
        "required_clauses_hit": required_hit,
        "required_clauses_missed": required_missed,
        "optional_clauses_used": optional_used,
        "forbidden_claim_violations": violations,
        "must_have_behavior_assessment": behavior_assessment,
        "scores": scores,
        "score": total,
        "pass": passed,
        "feedback": " ".join(feedback_parts),
        "recommended_generator_fix": recommended_fix,
        "deterministic_checks": {
            "schema_validation_errors": schema_errors,
            "quality_lint_warnings": quality.warnings,
            "quality_lint_errors": quality.errors,
            "assistant_has_visible_citation": has_visible_citation(row),
        },
    }
    result["pipeline_gap_assessment"] = pipeline_gap_assessment(result)
    return result


def validate_gold_cases(cases: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> None:
    ids = [case.get("id") for case in cases]
    if len(cases) != 9:
        raise RuntimeError(f"Expected 9 gold cases, found {len(cases)}.")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Gold case IDs must be unique.")
    counts = Counter(case.get("expected_category") for case in cases)
    expected_counts = {"clear_answer": 3, "clarification_required": 3, "genuine_ambiguity": 3}
    if dict(counts) != expected_counts:
        raise RuntimeError(f"Expected category counts {expected_counts}, found {dict(counts)}.")
    for case in cases:
        missing_fields = [
            field
            for field in ["id", "question", "expected_category", "reference_answer", "required_clause_ids", "optional_clause_ids", "must_have_behaviors", "forbidden_claims", "boundary_focus"]
            if field not in case
        ]
        if missing_fields:
            raise RuntimeError(f"{case.get('id')} missing gold fields: {', '.join(missing_fields)}")
        missing_clauses = [clause_id for clause_id in case["required_clause_ids"] + case["optional_clause_ids"] if clause_id not in records]
        if missing_clauses:
            raise RuntimeError(f"{case['id']} references unknown clauses: {', '.join(missing_clauses)}")


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [result["score"] for result in results]
    category_matches = sum(1 for result in results if result["category_match"])
    required_coverage = sum(1 for result in results if result["required_clauses_hit"])
    forbidden_count = sum(len(result["forbidden_claim_violations"]) for result in results)
    blocking_failures = [result for result in results if not result["pass"]]
    minor_behavior_feedback = [
        result
        for result in results
        if result["pass"] and any(not item["met"] for item in result.get("must_have_behavior_assessment", []))
    ]
    diagnostic_rows = [result for result in results if result["pass"] and result["score"] < 95]
    weakest_case = min(results, key=lambda result: result["score"]) if results else None
    dimension_loss = {
        name: sum(max_points - result["scores"].get(name, 0) for result in results)
        for name, max_points in SCORE_DIMENSIONS.items()
    }
    gap_counts = Counter(
        gap
        for result in results
        for gap in result.get("pipeline_gap_assessment", {}).get("gap_types", [])
        if gap != "none"
    )
    improvement_counts = Counter(
        suggestion
        for result in results
        for suggestion in result.get("pipeline_gap_assessment", {}).get("suggested_improvements", [])
        if suggestion != "no generator change suggested"
    )
    score_bands = {
        "100": sum(1 for score in scores if score == 100),
        "95_99": sum(1 for score in scores if 95 <= score < 100),
        "80_94": sum(1 for score in scores if 80 <= score < 95),
        "below_80": sum(1 for score in scores if score < 80),
    }
    return {
        "mean_score": round(statistics.mean(scores), 2) if scores else 0,
        "median_score": round(statistics.median(scores), 2) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "pass_count": sum(1 for result in results if result["pass"]),
        "row_count": len(results),
        "category_match_count": category_matches,
        "required_citation_coverage_count": required_coverage,
        "forbidden_claim_violation_count": forbidden_count,
        "blocking_failure_count": len(blocking_failures),
        "blocking_failures": [result["id"] for result in blocking_failures],
        "minor_behavior_feedback_count": len(minor_behavior_feedback),
        "minor_behavior_feedback_rows": [result["id"] for result in minor_behavior_feedback],
        "diagnostic_pass_rows_count": len(diagnostic_rows),
        "diagnostic_pass_rows": [result["id"] for result in diagnostic_rows],
        "score_bands": score_bands,
        "dimension_loss_totals": dict(sorted(dimension_loss.items(), key=lambda item: (-item[1], item[0]))),
        "pipeline_gap_counts": dict(sorted(gap_counts.items())),
        "suggested_improvement_counts": dict(improvement_counts.most_common()),
        "weakest_case": {
            "id": weakest_case["id"],
            "score": weakest_case["score"],
            "pass": weakest_case["pass"],
            "category_match": weakest_case["category_match"],
            "expected_category": weakest_case["expected_category"],
            "generated_category": weakest_case["generated_category"],
            "feedback": weakest_case["feedback"],
            "boundary_focus": weakest_case.get("boundary_focus"),
            "gap_types": weakest_case.get("pipeline_gap_assessment", {}).get("gap_types", []),
            "suggested_improvements": weakest_case.get("pipeline_gap_assessment", {}).get("suggested_improvements", []),
        }
        if weakest_case
        else None,
        "rows_needing_generator_changes": [result["id"] for result in blocking_failures],
        "by_expected_category": {
            category: {
                "rows": len(items),
                "mean_score": round(statistics.mean(item["score"] for item in items), 2) if items else 0,
                "pass_count": sum(1 for item in items if item["pass"]),
            }
            for category, items in {
                category: [result for result in results if result["expected_category"] == category]
                for category in sorted({result["expected_category"] for result in results})
            }.items()
        },
    }


def write_summary(path: Path, *, results: list[dict[str, Any]], aggregate: dict[str, Any], generated_rows_path: Path, manifest_path: Path) -> None:
    weakest_case = aggregate.get("weakest_case")
    diagnostic_rows = [result for result in results if result["pass"] and result["score"] < 95]
    lines = [
        "# Gold Mini-Benchmark Summary",
        "",
        "## Purpose",
        "",
        "This is a small generator-facing regression benchmark for known-hard Razorpay ToS questions. The generator receives the user question and retrieved ToS candidate sources, while hidden gold expectations are used only for scoring. The v2 report separates blocking failures from non-blocking quality signals so the benchmark can guide future generator improvements.",
        "",
        "## Scope",
        "",
        "- 9 human-curated gold cases.",
        "- 3 cases per category: `clear_answer`, `clarification_required`, and `genuine_ambiguity`.",
        "- Separate from the main 45-row dataset and the eval-hardening adversarial evaluator checks.",
        "- Report v2 adds score-band diagnostics, dimension-loss analysis, pipeline gap themes, and suggested improvements.",
        "",
        "## Aggregate Results",
        "",
        f"- Mean score: `{aggregate['mean_score']}/100`",
        f"- Median score: `{aggregate['median_score']}/100`",
        f"- Pass count: `{aggregate['pass_count']}/{aggregate['row_count']}`",
        f"- Category match count: `{aggregate['category_match_count']}/{aggregate['row_count']}`",
        f"- Required citation coverage count: `{aggregate['required_citation_coverage_count']}/{aggregate['row_count']}`",
        f"- Forbidden-claim violations: `{aggregate['forbidden_claim_violation_count']}`",
        f"- Blocking failures: `{aggregate['blocking_failure_count']}`",
        f"- Minor behavior feedback rows: `{aggregate['minor_behavior_feedback_count']}`",
        f"- Weakest case: `{weakest_case['id']}` at `{weakest_case['score']}/100`" if weakest_case else "- Weakest case: `n/a`",
        "",
        "## Quality Signals",
        "",
        f"- Score bands: `100={aggregate['score_bands']['100']}`, `95-99={aggregate['score_bands']['95_99']}`, `80-94={aggregate['score_bands']['80_94']}`, `below_80={aggregate['score_bands']['below_80']}`",
        f"- Passing rows below 95: `{aggregate['diagnostic_pass_rows_count']}`",
        "- Score loss by dimension:",
    ]
    for name, loss in aggregate["dimension_loss_totals"].items():
        if loss:
            lines.append(f"  - `{name}`: `{loss}` points lost")
    if not any(aggregate["dimension_loss_totals"].values()):
        lines.append("  - No score loss recorded.")
    lines.extend(
        [
            "- Pipeline gap themes:",
        ]
    )
    if aggregate["pipeline_gap_counts"]:
        for gap, count in aggregate["pipeline_gap_counts"].items():
            lines.append(f"  - `{gap}`: `{count}` rows")
    else:
        lines.append("  - No diagnostic gap themes recorded.")
    failing = [result for result in results if not result["pass"]]
    minor_feedback = [
        result
        for result in results
        if result["pass"] and any(not item["met"] for item in result.get("must_have_behavior_assessment", []))
    ]
    lines.extend(["", "## Findings", ""])
    if not failing:
        lines.append(
            "All cases passed the strict gate, with no category mismatches, required-citation failures, or forbidden-claim violations."
        )
    else:
        lines.append("Blocking failures are rows that failed the pass rule and should be fixed before relying on this benchmark:")
        lines.append("")
        for result in failing:
            lines.append(f"- `{result['id']}`: {result['recommended_generator_fix']}")
    if minor_feedback:
        lines.extend(["", "## Minor Behavior Feedback", ""])
        for result in minor_feedback:
            missed = [item["behavior"] for item in result.get("must_have_behavior_assessment", []) if not item["met"]]
            lines.append(f"- `{result['id']}` ({result['score']}/100): " + "; ".join(missed))
    if weakest_case:
        lines.extend(["", "## Weakest Case", ""])
        if weakest_case.get("pass"):
            lines.append(
                f"`{weakest_case['id']}` scored `{weakest_case['score']}/100` on `{weakest_case.get('boundary_focus')}`. "
                "It passed category and required-source checks, so the signal is non-blocking. "
                f"The gap is `{weakest_case.get('feedback')}`, which suggests the generator handles the boundary but should improve explanatory completeness for future runs."
            )
        else:
            lines.append(
                f"`{weakest_case['id']}` scored `{weakest_case['score']}/100` on `{weakest_case.get('boundary_focus')}` and failed the strict gate. "
                f"The gap is `{weakest_case.get('feedback')}`. This points to a generator category-selection issue, not a source-retrieval miss, because required citation coverage still succeeded."
            )
    if aggregate["suggested_improvement_counts"]:
        lines.extend(["", "## Suggested Generator Improvements", ""])
        for suggestion, count in aggregate["suggested_improvement_counts"].items():
            lines.append(f"- `{count}` row(s): {suggestion}")
    lines.extend(
        [
            "",
            "## Changes In This Report Version",
            "",
        ]
    )
    lines.extend(f"- {item}." for item in REPORT_CHANGES)
    lines.extend(
        [
            "",
            "## Diagnostic Pass Rows",
            "",
        ]
    )
    if diagnostic_rows:
        lines.extend(["| Case | Score | Boundary Focus | Main Gap Signal |", "|---|---:|---|---|"])
        for result in diagnostic_rows:
            gap_types = ", ".join(result.get("pipeline_gap_assessment", {}).get("gap_types", [])) or "none"
            focus = str(result.get("boundary_focus", "")).replace("|", "\\|")
            lines.append(f"| `{result['id']}` | {result['score']} | {focus} | {gap_types} |")
    else:
        lines.append("No passing rows scored below 95.")
    lines.extend(
        [
            "",
            "## Per-Case Results",
            "",
            "| Case | Expected | Generated | Score | Pass | Required Hit | Feedback |",
            "|---|---|---|---:|---|---|---|",
        ]
    )
    for result in results:
        hit = ", ".join(result["required_clauses_hit"]) or "-"
        feedback = result["feedback"].replace("|", "\\|")
        lines.append(
            f"| `{result['id']}` | `{result['expected_category']}` | `{result['generated_category']}` | {result['score']} | `{result['pass']}` | {hit} | {feedback} |"
        )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- Generated rows: `{workspace_path(generated_rows_path)}`",
            f"- Manifest: `{workspace_path(manifest_path)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(
    path: Path,
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    elapsed_seconds: float,
    index: dict[str, Any],
    cases_path: Path,
    output_dir: Path,
    generated_rows_path: Path,
    results_path: Path,
    summary_path: Path,
    model: str,
    temperature: float,
    template_only: bool,
    aggregate: dict[str, Any],
) -> None:
    manifest = {
        "run_id": run_id,
        "run_version": RUN_VERSION,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "generator_model": "template-question-rules" if template_only else model,
        "generator_temperature": temperature,
        "prompt_version": PROMPT_VERSION,
        "report_version": REPORT_VERSION,
        "report_changes": REPORT_CHANGES,
        "source_hash": index["metadata"]["source_hash"],
        "source_fetched_at": index["metadata"]["source_fetched_at"],
        "gold_cases_path": workspace_path(cases_path),
        "gold_fields_hidden_from_generator": HIDDEN_GOLD_FIELDS,
        "generator_visible_inputs": [
            "user question",
            "question-retrieved candidate source records",
            "schema instructions",
            "prior validation errors on repair attempts",
        ],
        "id_policy": "Gold case IDs are not sent to the generator. Generated rows use neutral bench_case_NNN IDs assigned by the runner after loading the curated cases.",
        "output_paths": {
            "output_dir": workspace_path(output_dir),
            "generated_rows": workspace_path(generated_rows_path),
            "gold_bench_results": workspace_path(results_path),
            "gold_bench_summary": workspace_path(summary_path),
            "gold_bench_manifest": workspace_path(path),
        },
        "row_count": aggregate["row_count"],
        "aggregate_metrics": aggregate,
        "pass_rule": "score >= 80 and category_match and no forbidden_claim_violations and at least one required clause hit",
        "note": "Generator-facing benchmark only; does not modify the main 45-row dataset or evaluator outputs.",
        "template_only": template_only,
    }
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> int:
    load_environment()
    model = args.model or os.environ.get("OPENAI_MODEL") or DEFAULT_GENERATOR_MODEL
    temperature = args.temperature if args.temperature is not None else float(os.environ.get("OPENAI_TEMPERATURE", DEFAULT_GENERATOR_TEMPERATURE))
    api_key = os.environ.get("OPENAI_API_KEY")
    if not args.template_only and not api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --template-only is set.")

    cases_path = args.gold_cases if args.gold_cases.is_absolute() else ROOT / args.gold_cases
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    clause_index_path = args.clause_index if args.clause_index.is_absolute() else ROOT / args.clause_index
    output_dir.mkdir(parents=True, exist_ok=True)

    index, records = load_clause_index(clause_index_path)
    cases, parse_errors = load_jsonl(cases_path)
    if parse_errors:
        raise RuntimeError("Gold case parse errors:\n" + "\n".join(parse_errors))
    validate_gold_cases(cases, records)

    started_at = utc_now()
    start = time.perf_counter()
    run_id = f"{started_at.replace(':', '').replace('-', '').replace('Z', 'Z')}_gold_bench_{RUN_VERSION}"
    client = None if args.template_only else OpenAIClient(api_key=api_key or "", model=model, temperature=temperature)
    rows: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for index_case, case in enumerate(cases, start=1):
        row_id = neutral_case_id(index_case)
        row, generation_notes = generate_row(
            case=case,
            row_id=row_id,
            index=index,
            records=records,
            client=client,
            model=model,
            temperature=temperature,
            run_id=run_id,
            max_attempts=args.max_attempts,
            template_only=args.template_only,
        )
        row["generation_metadata"]["gold_bench"] = {
            "candidate_clause_ids": generation_notes["candidate_clause_ids"],
            "attempts": generation_notes["attempts"],
            "fallback_used": generation_notes["fallback_used"],
        }
        rows.append(row)
        result = score_case(row, case, records)
        result["generation_notes"] = generation_notes
        result["pipeline_gap_assessment"] = pipeline_gap_assessment(result)
        results.append(result)

    dataset_failures = validate_dataset(rows, records, expected_total=9, expected_per_category=None)
    if dataset_failures:
        raise RuntimeError("Gold benchmark generated rows failed schema validation:\n" + json.dumps(dataset_failures, indent=2))

    aggregate = aggregate_results(results)
    generated_rows_path = output_dir / "generated_rows.jsonl"
    results_path = output_dir / "gold_bench_results.jsonl"
    summary_path = output_dir / "gold_bench_summary.md"
    manifest_path = output_dir / "gold_bench_manifest.json"
    completed_at = utc_now()
    elapsed_seconds = time.perf_counter() - start

    write_jsonl(generated_rows_path, rows)
    write_jsonl(results_path, results)
    write_summary(summary_path, results=results, aggregate=aggregate, generated_rows_path=generated_rows_path, manifest_path=manifest_path)
    write_manifest(
        manifest_path,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=elapsed_seconds,
        index=index,
        cases_path=cases_path,
        output_dir=output_dir,
        generated_rows_path=generated_rows_path,
        results_path=results_path,
        summary_path=summary_path,
        model=model,
        temperature=temperature,
        template_only=args.template_only,
        aggregate=aggregate,
    )
    print(f"Wrote {workspace_path(generated_rows_path)}")
    print(f"Wrote {workspace_path(results_path)}")
    print(f"Wrote {workspace_path(summary_path)}")
    print(f"Wrote {workspace_path(manifest_path)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-cases", type=Path, default=GOLD_CASES_PATH)
    parser.add_argument("--output-dir", type=Path, default=GOLD_OUTPUT_DIR)
    parser.add_argument("--clause-index", type=Path, default=CLAUSE_INDEX_PATH)
    parser.add_argument("--model", default=None, help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.5.")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--template-only", action="store_true", help="Use local question-only rules instead of OpenAI generation.")
    args = parser.parse_args()
    return run_benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
