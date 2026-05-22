#!/usr/bin/env python3
"""Fetch Razorpay terms and parse them into a traceable clause index.

This script intentionally uses only the Python standard library so the first
pipeline step can run before the project has a dependency setup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


SOURCE_URL = "https://razorpay.com/terms/"
DOCUMENT_TITLE = "Razorpay General Terms of Use"
EXPECTED_ASSIGNMENT_CLAUSES = ("3.4", "3.5", "4.1", "4.2", "4.3", "4.5", "16.1")
PARSER_VERSION = "v0.2"

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
RAW_HTML_PATH = RAW_DIR / "razorpay_terms.html"
CLEAN_TEXT_PATH = PROCESSED_DIR / "razorpay_terms_clean.txt"
SNAPSHOT_META_PATH = RAW_DIR / "razorpay_terms_snapshot.json"
CLAUSE_INDEX_PATH = PROCESSED_DIR / "clause_index.json"
VALIDATION_REPORT_PATH = PROCESSED_DIR / "parser_validation_report.json"

SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside"}
NAV_FOOTER_DENYLIST = {
    "ACCEPT PAYMENTS",
    "NEW",
    "PAYROLL",
    "MORE",
    "COMPANY",
    "DEVELOPERS",
    "RESOURCES",
    "FREE TOOLS",
    "HELP & SUPPORT",
    "FIND US ONLINE",
    "REGD. OFFICE ADDRESS",
}

REQUIRED_SECTION_SLUGS = {
    "PartA.Preamble",
    "PartA.definitions",
    "PartA.16.suspension_and_termination",
    "PartA.17.prohibited_products_and_services",
    "PartB.PartI.1.payment_processing",
    "PartB.PartI.2.chargebacks",
    "PartB.PartI.3.refunds",
    "PartB.PartI.4.fraudulent_transactions",
    "PartB.PartIA.Preamble",
    "PartB.PartIB.Preamble",
    "PartB.PartII.Preamble",
    "PartB.PartIII.Preamble",
    "PartB.PartIV.Preamble",
    "PartB.PartV.Preamble",
    "PartB.PartVI.Preamble",
}


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    value = html.unescape(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "untitled"


def normalize_ws(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


class ReadableTextParser(HTMLParser):
    """Extract readable text while keeping heading/list boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts)
        lines = [normalize_ws(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)


@dataclass
class Block:
    kind: str
    level: int | None
    text: str
    source_order: int


@dataclass
class TextLine:
    text: str
    char_start: int
    char_end: int
    source_order: int


class StructuralTextParser(HTMLParser):
    """Extract headings, paragraphs, and list items as ordered text blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.skip_depth = 0
        self.current_tag: str | None = None
        self.current_level: int | None = None
        self.current_text: list[str] = []
        self.order = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.current_tag = "heading"
            self.current_level = int(tag[1])
            self.current_text = []
        elif tag in {"p", "li"}:
            self._flush()
            self.current_tag = "list_item" if tag == "li" else "paragraph"
            self.current_level = None
            self.current_text = []
        elif tag == "br" and self.current_tag:
            self.current_text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag) or tag in {"p", "li"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.current_tag:
            return
        self.current_text.append(data)

    def close(self) -> None:
        self._flush()
        super().close()

    def _flush(self) -> None:
        if not self.current_tag:
            return
        text = normalize_ws(" ".join(self.current_text))
        if text:
            self.order += 1
            self.blocks.append(
                Block(
                    kind=self.current_tag,
                    level=self.current_level,
                    text=text,
                    source_order=self.order,
                )
            )
        self.current_tag = None
        self.current_level = None
        self.current_text = []


@dataclass
class SectionState:
    number: int
    title: str
    slug: str
    heading_level: int
    section_id: str
    clause_count: int = 0
    source_span_id: str = ""
    clause_ids: list[str] = field(default_factory=list)


def fetch_source(url: str) -> tuple[str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; HydeAssessmentDatasetBot/1.0; "
                "+https://example.invalid/local-assessment)"
            )
        },
    )
    with urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return final_url, body


def extract_clean_text(raw_html: str) -> str:
    parser = ReadableTextParser()
    parser.feed(raw_html)
    parser.close()
    return parser.text()


def extract_blocks(raw_html: str, clean_text: str) -> list[Block]:
    parser = StructuralTextParser()
    parser.feed(raw_html)
    parser.close()
    blocks = dedupe_blocks(parser.blocks)
    if blocks:
        return blocks

    # Fallback for unexpectedly sparse HTML.
    fallback_blocks: list[Block] = []
    for order, line in enumerate(clean_text.splitlines(), start=1):
        kind = "heading" if len(line) < 90 and not line.startswith("- ") else "paragraph"
        fallback_blocks.append(Block(kind=kind, level=2 if kind == "heading" else None, text=line, source_order=order))
    return fallback_blocks


def dedupe_blocks(blocks: list[Block]) -> list[Block]:
    deduped: list[Block] = []
    previous_key: tuple[str, str] | None = None
    for block in blocks:
        key = (block.kind, block.text)
        if key == previous_key:
            continue
        deduped.append(block)
        previous_key = key
    return deduped


def detect_cross_references(text: str) -> list[str]:
    patterns = [
        r"\b(?:Clause|clause)s?\s+((?:\d+(?:\.\d+)*)(?:\s*(?:,|and|or)\s*\d+(?:\.\d+)*)*)",
    ]
    refs: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = match.group(1)
            for token in re.split(r",| and | or ", raw):
                token = token.strip()
                if token and re.fullmatch(r"\d+(?:\.\d+)*", token) and token not in refs:
                    refs.append(token)
    return refs


def detect_external_references(text: str) -> list[dict[str, str]]:
    external_refs: list[dict[str, str]] = []
    patterns: list[tuple[str, str, str, str | None, str | None]] = [
        (
            "regulatory_reference",
            "RBI",
            "Reserve Bank of India",
            r"\bRBI's notification\s+.*?dated\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}",
            None,
        ),
        (
            "regulatory_reference",
            "RBI",
            "Reserve Bank of India",
            r"\bRBI circular\s+[^.;\n]+",
            None,
        ),
        (
            "regulatory_reference",
            "NPCI",
            "National Payments Corporation of India",
            r"\bNPCI/[\dA-Z\-/ ]+",
            None,
        ),
        (
            "regulatory_reference",
            "NPCI",
            "National Payments Corporation of India",
            r"\bNPCI guideline on [^.;\n]+",
            None,
        ),
        (
            "legal_reference",
            "IT_ACT",
            "Information Technology Act, 2000",
            r"\bInformation Technology Act,\s*2000\b",
            None,
        ),
        ("policy_reference", "PRIVACY_POLICY", "Privacy Policy", r"\bPrivacy Policy\b", None),
    ]
    for ref_type, entity, name, pattern, _ in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = normalize_ws(match.group(0))
            item = {"type": ref_type, "entity": entity, "name": name, "raw_text": raw}
            if any(
                existing["entity"] == entity
                and (raw in existing["raw_text"] or existing["raw_text"] in raw)
                for existing in external_refs
            ):
                continue
            if item not in external_refs:
                external_refs.append(item)
    return external_refs


def infer_taxonomy(text: str, section_title: str) -> dict[str, Any]:
    haystack = f"{section_title} {text}".lower()
    tag_rules = {
        "refunds": ["refund"],
        "fees": ["fee", "charges", "pricing"],
        "settlement": ["settlement", "settle"],
        "chargebacks": ["chargeback"],
        "fraudulent_transactions": ["fraud", "unauthorised", "unauthorized"],
        "suspension": ["suspend", "restrict", "terminate"],
        "gaming": ["gaming", "game"],
        "account_registration": ["account", "registration", "credentials", "api key"],
        "privacy": ["privacy", "data"],
        "intellectual_property": ["intellectual property", "licence", "license", "content"],
        "liability": ["liability", "liable", "warranties", "damages"],
        "governing_law": ["governing law", "jurisdiction", "courts"],
        "communications": ["email", "sms", "communications"],
        "third_party": ["third-party", "third party"],
        "external_regulation": ["rbi", "npci", "applicable law", "information technology act"],
    }
    actor_rules = {
        "merchant": ["merchant"],
        "customer": ["customer", "user"],
        "razorpay": ["razorpay", "we", "us", "our"],
        "facility_provider": ["facility provider"],
        "group_entity": ["group entity", "group entities"],
    }
    topic_tags = [tag for tag, keywords in tag_rules.items() if any(keyword in haystack for keyword in keywords)]
    actor_scope = [actor for actor, keywords in actor_rules.items() if any(keyword in haystack for keyword in keywords)]
    service_area = "unknown"
    payment_stage = []
    if any(token in haystack for token in ["refund", "chargeback", "settled", "settlement"]):
        payment_stage.append("post_payment")
    if "settlement" in haystack or "settled" in haystack:
        payment_stage.append("settlement")
    issue_type = []
    if "fraud" in haystack or "unauthorised" in haystack or "unauthorized" in haystack:
        issue_type.append("fraud_dispute")
    if "refund" in haystack:
        issue_type.append("refund")
    if "suspend" in haystack or "terminate" in haystack:
        issue_type.append("account_access")
    return {
        "topic_tags": topic_tags,
        "service_area": service_area,
        "actor_scope": actor_scope,
        "payment_stage": payment_stage,
        "issue_type": issue_type,
    }


def part_key(part: str | None) -> str:
    if not part:
        return "Unparted"
    match = re.search(r"Part\s+([A-Z]+|[IVXLCDM]+)", part, flags=re.IGNORECASE)
    return f"Part{match.group(1).upper()}" if match else slugify(part).title().replace("_", "")


def subpart_key(subpart: str | None) -> str | None:
    if not subpart:
        return None
    match = re.search(r"Part\s+([A-Z]+|[IVXLCDM]+)", subpart, flags=re.IGNORECASE)
    return f"Part{match.group(1).upper()}" if match else slugify(subpart).title().replace("_", "")


def is_probable_section_heading(line: str) -> bool:
    if not line or line == "-":
        return False
    if line in {"RTO Protection"}:
        return True
    if re.match(r"^\d+(?:\.\d+)*\s+", line):
        return False
    if re.match(r"^Part\s+", line, flags=re.IGNORECASE):
        return False
    if len(line) > 100:
        return False
    letters = re.sub(r"[^A-Za-z]+", "", line)
    if not letters:
        return False
    return line.upper() == line and len(letters) >= 3


def legal_text_lines(clean_text: str) -> list[str]:
    lines = [normalize_ws(line) for line in clean_text.splitlines()]
    lines = [line for line in lines if line]
    try:
        start = next(i for i, line in enumerate(lines) if line == "PAYMENTS: TERMS AND CONDITIONS")
    except StopIteration:
        start = 0
    return lines[start:]


def build_clause_index(
    *,
    url: str,
    fetched_at: str,
    raw_html: str,
    clean_text: str,
    raw_html_path: Path,
    clean_text_path: Path,
) -> dict[str, Any]:
    source_hash = sha256_text(raw_html)
    lines = legal_text_lines(clean_text)
    clauses: list[dict[str, Any]] = []
    section_records: list[dict[str, Any]] = []
    seen_clause_texts: dict[str, set[str]] = {}
    current_part: str | None = None
    current_subpart: str | None = None
    current_section_id: str | None = None
    current_section_title: str | None = None
    current_section_source_text: str | None = None
    current_section_number: str | None = None
    current_section_span: str | None = None
    current_section_children: list[str] = []
    last_numbered_clause_id: str | None = None
    last_numbered_clause_number: str | None = None
    child_item_counter = 0
    section_counter = 0
    subpart_section_counter = 0
    inferred_clause_counter = 0
    span_counter = 0

    def next_span_id() -> str:
        nonlocal span_counter
        span_counter += 1
        return f"rzp_terms_{dt.datetime.now(dt.UTC).year}_{span_counter:05d}"

    def make_section_id(title: str, explicit_number: str | None = None) -> str:
        base_parts = [part_key(current_part)]
        sub_key = subpart_key(current_subpart)
        if sub_key:
            base_parts.append(sub_key)
        if explicit_number:
            base_parts.append(explicit_number)
        else:
            base_parts.append(f"Section{section_counter:03d}")
        base_parts.append(slugify(title))
        return ".".join(base_parts)

    def flush_section() -> None:
        nonlocal current_section_id, current_section_title, current_section_source_text
        nonlocal current_section_number, current_section_span, current_section_children
        if not current_section_id or not current_section_title or not current_section_source_text or not current_section_span:
            return
        section_records.append(
            {
                "source_span_id": current_section_span,
                "clause_id": current_section_id,
                "display_citation": display_citation(current_section_number, current_section_title),
                "hierarchy": {
                    "document": DOCUMENT_TITLE,
                    "part": current_part,
                    "subpart": current_subpart,
                    "section_id": current_section_id,
                    "section_title": current_section_title,
                    "clause_number": current_section_number,
                    "heading_level": None,
                },
                "text": current_section_source_text,
                "normalized_text": normalize_ws(current_section_source_text).lower(),
                "relationships": {
                    "parent_clause_id": None,
                    "child_clause_ids": current_section_children,
                    "sibling_clause_ids": [],
                },
                "references": {
                    "cross_references": detect_cross_references(current_section_title),
                    "external_references": detect_external_references(current_section_title),
                },
                "taxonomy": infer_taxonomy("", current_section_title),
                "provenance": provenance(url, source_hash, fetched_at, raw_html_path, clean_text_path),
                "record_type": "section",
            }
        )

    def display_citation(number: str | None, section_title: str, clause_number: str | None = None) -> str:
        pieces = []
        if current_part:
            pieces.append(current_part.replace(":", ""))
        if current_subpart:
            pieces.append(current_subpart.replace(" - ", ", ").replace(":", ""))
        if clause_number:
            pieces.append(f"Clause {clause_number}")
        elif number:
            pieces.append(f"Section {number}")
        pieces.append(section_title)
        return ", ".join(pieces)

    def provenance(
        url_value: str,
        hash_value: str,
        fetched_at_value: str,
        raw_path: Path,
        clean_path: Path,
    ) -> dict[str, str]:
        return {
            "source_url": url_value,
            "source_hash": hash_value,
            "source_fetched_at": fetched_at_value,
            "raw_html_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
            "clean_text_path": str(clean_path.relative_to(ROOT)).replace("\\", "/"),
        }

    for source_order, line in enumerate(lines, start=1):
        if line == "-":
            continue

        part_match = re.match(r"^(PART\s+[A-Z]):?\s*(.*)$", line, flags=re.IGNORECASE)
        subpart_match = re.match(r"^(Part\s+[IVXLCDM]+)\s*-\s*(.*)$", line, flags=re.IGNORECASE)
        numeric_match = re.match(r"^(\d+(?:\.\d+)+)\s+(.*)$", line)

        if part_match and not subpart_match:
            flush_section()
            current_part = f"{part_match.group(1).title()}: {part_match.group(2).strip()}".strip()
            current_subpart = None
            current_section_id = None
            current_section_title = None
            current_section_number = None
            current_section_span = None
            current_section_children = []
            last_numbered_clause_id = None
            last_numbered_clause_number = None
            child_item_counter = 0
            subpart_section_counter = 0
            continue

        if subpart_match:
            flush_section()
            current_subpart = f"{subpart_match.group(1).title()} - {subpart_match.group(2).strip()}".strip()
            current_section_id = None
            current_section_title = None
            current_section_number = None
            current_section_span = None
            current_section_children = []
            last_numbered_clause_id = None
            last_numbered_clause_number = None
            child_item_counter = 0
            subpart_section_counter = 0
            continue

        if is_probable_section_heading(line):
            flush_section()
            section_counter += 1
            subpart_section_counter += 1
            current_section_title = line
            current_section_number = str(subpart_section_counter if current_subpart else section_counter)
            current_section_id = make_section_id(line, current_section_number)
            current_section_span = next_span_id()
            current_section_children = []
            last_numbered_clause_id = None
            last_numbered_clause_number = None
            child_item_counter = 0
            inferred_clause_counter = 0
            continue

        if not current_section_id:
            section_counter += 1
            current_section_title = "Preamble"
            current_section_number = str(section_counter)
            current_section_id = make_section_id(current_section_title, current_section_number)
            current_section_span = next_span_id()
            current_section_children = []
            last_numbered_clause_id = None
            last_numbered_clause_number = None
            child_item_counter = 0
            inferred_clause_counter = 0

        if numeric_match:
            clause_number = numeric_match.group(1)
            clause_text = line
            section_number_from_clause = clause_number.split(".", 1)[0]
            if (
                current_part
                and not current_subpart
                and current_section_title
                and current_section_number != section_number_from_clause
                and not current_section_children
            ):
                current_section_number = section_number_from_clause
                current_section_id = make_section_id(current_section_title, current_section_number)
            child_item_counter = 0
        else:
            clause_text = line
            if last_numbered_clause_id and last_numbered_clause_number and not current_subpart:
                child_item_counter += 1
                clause_number = f"{last_numbered_clause_number}.item{child_item_counter:03d}"
            else:
                inferred_clause_counter += 1
                section_number = current_section_number or str(section_counter)
                clause_number = f"{section_number}.{inferred_clause_counter}"

        base_parts = [part_key(current_part)]
        sub_key = subpart_key(current_subpart)
        if sub_key:
            base_parts.append(sub_key)
        base_parts.append(clause_number)
        clause_id = ".".join(base_parts)
        normalized_clause_text = normalize_ws(clause_text).lower()
        existing_texts = seen_clause_texts.setdefault(clause_id, set())
        if normalized_clause_text in existing_texts:
            continue
        if existing_texts:
            variant_number = len(existing_texts) + 1
            existing_texts.add(normalized_clause_text)
            clause_id = f"{clause_id}.Variant{variant_number:03d}"
        else:
            existing_texts.add(normalized_clause_text)
        source_span_id = next_span_id()
        if numeric_match or not last_numbered_clause_id or current_subpart:
            current_section_children.append(clause_id)
            parent_clause_id = current_section_id
        else:
            parent_clause_id = last_numbered_clause_id
        taxonomy = infer_taxonomy(clause_text, current_section_title or "")
        clauses.append(
            {
                "source_span_id": source_span_id,
                "clause_id": clause_id,
                "display_citation": display_citation(current_section_number, current_section_title or "", clause_number),
                "hierarchy": {
                    "document": DOCUMENT_TITLE,
                    "part": current_part,
                    "subpart": current_subpart,
                    "section_id": current_section_id,
                    "section_title": current_section_title,
                    "clause_number": clause_number,
                    "heading_level": None,
                    "source_order": source_order,
                    "block_kind": "paragraph" if numeric_match else "inferred_clause",
                },
                "text": clause_text,
                "normalized_text": normalized_clause_text,
                "relationships": {
                    "parent_clause_id": parent_clause_id,
                    "child_clause_ids": [],
                    "sibling_clause_ids": [],
                },
                "references": {
                    "cross_references": detect_cross_references(clause_text),
                    "external_references": detect_external_references(clause_text),
                },
                "taxonomy": taxonomy,
                "provenance": provenance(url, source_hash, fetched_at, raw_html_path, clean_text_path),
            }
        )
        if numeric_match:
            last_numbered_clause_id = clause_id
            last_numbered_clause_number = clause_number

    flush_section()

    clauses_by_parent: dict[str, list[str]] = {}
    for clause in clauses:
        parent_id = clause["relationships"]["parent_clause_id"]
        clauses_by_parent.setdefault(parent_id, []).append(clause["clause_id"])
    for clause in clauses:
        parent_id = clause["relationships"]["parent_clause_id"]
        siblings = [cid for cid in clauses_by_parent[parent_id] if cid != clause["clause_id"]]
        clause["relationships"]["sibling_clause_ids"] = siblings
        clause["relationships"]["child_clause_ids"] = clauses_by_parent.get(clause["clause_id"], [])
        clause["record_type"] = "clause"

    clause_id_set = {clause["clause_id"] for clause in clauses}
    expected_targets = {
        "3.4": "PartA.3.4",
        "3.5": "PartA.3.5",
        "4.1": "PartB.PartI.4.1",
        "4.2": "PartB.PartI.4.2",
        "4.3": "PartB.PartI.4.3",
        "4.5": "PartB.PartI.4.5",
        "16.1": "PartA.16.1",
    }
    expected_found = {label: target in clause_id_set for label, target in expected_targets.items()}
    warnings = []
    if not all(expected_found.values()):
        missing = [clause for clause, found in expected_found.items() if not found]
        warnings.append(
            "The fetched /terms/ page does not contain expected assignment-style clause numbers: "
            + ", ".join(missing)
            + ". Clause IDs in this index preserve the structure available in the fetched page."
        )

    return {
        "metadata": {
            "document": DOCUMENT_TITLE,
            "source_url": url,
            "source_hash": source_hash,
            "source_fetched_at": fetched_at,
            "raw_html_path": str(raw_html_path.relative_to(ROOT)).replace("\\", "/"),
            "clean_text_path": str(clean_text_path.relative_to(ROOT)).replace("\\", "/"),
            "parser_version": "v0.1",
            "record_count": len(section_records) + len(clauses),
            "section_count": len(section_records),
            "clause_count": len(clauses),
            "expected_assignment_clauses_found": expected_found,
            "warnings": warnings,
        },
        "records": section_records + clauses,
    }


def extract_legal_text(full_clean_text: str) -> str:
    """Return only the legal terms content from the cleaned page text."""
    lines = [normalize_ws(line) for line in full_clean_text.splitlines()]
    lines = [line for line in lines if line]
    start = 0
    for idx, line in enumerate(lines):
        if (
            line == "PAYMENTS: TERMS AND CONDITIONS"
            and idx + 1 < len(lines)
            and lines[idx + 1] == "PART A: GENERAL TERMS AND CONDITIONS"
        ):
            start = idx
            break

    end = len(lines)
    seen_part_vi = False
    for idx in range(start, len(lines)):
        if re.match(r"^Part VI\b", lines[idx], flags=re.IGNORECASE):
            seen_part_vi = True
        if seen_part_vi and lines[idx] == "PRIVACY":
            end = idx
            break
        if seen_part_vi and lines[idx] in NAV_FOOTER_DENYLIST:
            end = idx
            break
    return "\n".join(lines[start:end])


def legal_lines_with_spans(clean_text: str) -> list[TextLine]:
    lines: list[TextLine] = []
    cursor = 0
    for source_order, raw_line in enumerate(clean_text.splitlines(), start=1):
        text = normalize_ws(raw_line)
        line_start = clean_text.find(raw_line, cursor)
        if line_start < 0:
            line_start = cursor
        line_end = line_start + len(raw_line)
        cursor = line_end + 1
        if text:
            lines.append(TextLine(text=text, char_start=line_start, char_end=line_end, source_order=source_order))
    return lines


def parse_part_heading(line: str) -> dict[str, str] | None:
    top_match = re.match(r"^PART\s+([AB])\s*:\s*(.+)$", line, flags=re.IGNORECASE)
    if top_match:
        label = top_match.group(1).upper()
        return {
            "kind": "top",
            "key": f"Part{label}",
            "title": f"Part {label}: {top_match.group(2).strip()}",
        }

    sub_match = re.match(r"^Part\s+(IA|IB|I|II|III|IV|V|VI)\s*(?::|-)\s*(.+)$", line, flags=re.IGNORECASE)
    if sub_match:
        label = sub_match.group(1).upper()
        return {
            "kind": "subpart",
            "key": f"PartB.Part{label}",
            "title": f"Part {label}: {sub_match.group(2).strip()}",
        }
    return None


def record_taxonomy(text: str, section_title: str, part_key_value: str) -> dict[str, Any]:
    taxonomy = infer_taxonomy(text, section_title)
    taxonomy["service_area"] = hierarchy_service_area(part_key_value)
    if part_key_value == "PartA" and "prohibited_products_and_services" in slugify(section_title):
        tags = set(taxonomy.get("topic_tags", []))
        tags.update({"prohibited_use", "prohibited_products_services"})
        taxonomy["topic_tags"] = sorted(tags)
    taxonomy["taxonomy_method"] = "keyword_v0.2"
    return taxonomy


def clean_section_title(title: str) -> str:
    return title.strip().lstrip(". ").strip()


def hierarchy_service_area(part_key_value: str) -> str:
    service_area_by_part = {
        "PartA": "general_terms",
        "PartB": "specific_terms",
        "PartB.PartI": "payment_aggregation",
        "PartB.PartIA": "offline_payment_aggregation",
        "PartB.PartIB": "cross_border_outward",
        "PartB.PartII": "e_mandate",
        "PartB.PartIII": "token_hq",
        "PartB.PartIV": "subscription_services",
        "PartB.PartV": "partner_program",
        "PartB.PartVI": "magic_checkout",
    }
    return service_area_by_part.get(part_key_value, "unknown")


def make_section_id(part_key_value: str, title: str, number: str | None) -> str:
    if title == "Preamble":
        return f"{part_key_value}.Preamble"
    if number:
        return f"{part_key_value}.{number}.{slugify(title)}"
    return f"{part_key_value}.{slugify(title)}"


def resolve_cross_references(
    raw_refs: list[str],
    text: str,
    current_part_key: str,
    clause_id_set: set[str],
    section_id_set: set[str],
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []
    lower_text = text.lower()

    for raw_ref in raw_refs:
        candidates: list[str] = []
        def add_section_candidates(part_prefix: str) -> None:
            prefix = f"{part_prefix}.{raw_ref}."
            candidates.extend(sorted(section_id for section_id in section_id_set if section_id.startswith(prefix)))

        if "this part b, part i" in lower_text:
            candidates.append(f"PartB.PartI.{raw_ref}")
            add_section_candidates("PartB.PartI")
        if "part b part i" in lower_text or "part b, part i" in lower_text:
            candidates.append(f"PartB.PartI.{raw_ref}")
            add_section_candidates("PartB.PartI")
        if "part a" in lower_text and raw_ref.startswith("16"):
            candidates.extend(
                [
                    "PartA.16.suspension_and_termination",
                    "PartA.16.1",
                    f"PartA.{raw_ref}",
                ]
            )
        if "part a" in lower_text and not raw_ref.startswith("16"):
            add_section_candidates("PartA")
            candidates.append(f"PartA.{raw_ref}")
        candidates.append(f"{current_part_key}.{raw_ref}")
        add_section_candidates(current_part_key)

        match = next((candidate for candidate in candidates if candidate in clause_id_set or candidate in section_id_set), None)
        if match and match not in resolved:
            resolved.append(match)
        elif raw_ref not in unresolved:
            unresolved.append(raw_ref)
    return resolved, unresolved


def validate_clause_index(index: dict[str, Any]) -> dict[str, Any]:
    records = index["records"]
    ids = [record["clause_id"] for record in records]
    id_set = set(ids)
    section_titles = [record["hierarchy"]["section_title"] for record in records if record["record_type"] == "section"]
    nav_footer_like = sorted({title for title in section_titles if title in NAV_FOOTER_DENYLIST})
    weird_ids = sorted(
        record_id
        for record_id in ids
        if re.match(r"^(PartI|PartV)\.\d+", record_id) or ".accept_payments" in record_id
    )
    missing_required_sections = sorted(slug for slug in REQUIRED_SECTION_SLUGS if slug not in id_set)
    missing_parents: list[dict[str, str]] = []
    missing_children: list[dict[str, str]] = []
    for record in records:
        parent_id = record["relationships"].get("parent_clause_id")
        if parent_id and parent_id not in id_set:
            missing_parents.append({"clause_id": record["clause_id"], "missing_parent": parent_id})
        for child_id in record["relationships"].get("child_clause_ids", []):
            if child_id not in id_set:
                missing_children.append({"clause_id": record["clause_id"], "missing_child": child_id})

    unresolved_count = sum(len(record["references"].get("unresolved_cross_references", [])) for record in records)
    validation_errors = []
    if len(ids) != len(id_set):
        validation_errors.append("Duplicate clause_id values found.")
    if nav_footer_like:
        validation_errors.append("Navigation/footer-like section titles found.")
    if weird_ids:
        validation_errors.append("Weird part IDs found.")
    if missing_parents:
        validation_errors.append("Some child records point to missing parents.")
    if missing_children:
        validation_errors.append("Some parent records list missing children.")
    if missing_required_sections:
        validation_errors.append("Required legal sections are missing.")

    return {
        "metadata_counts": {
            "record_count": index["metadata"]["record_count"],
            "section_count": index["metadata"]["section_count"],
            "clause_count": index["metadata"]["clause_count"],
        },
        "first_legal_record": records[0] if records else None,
        "last_legal_record": records[-1] if records else None,
        "part_boundaries_found": index["metadata"].get("part_boundaries_found", []),
        "nav_footer_like_record_count": len(nav_footer_like),
        "nav_footer_like_titles": nav_footer_like,
        "weird_part_id_count": len(weird_ids),
        "weird_part_ids": weird_ids[:50],
        "missing_required_sections": missing_required_sections,
        "unresolved_reference_count": unresolved_count,
        "missing_parent_count": len(missing_parents),
        "missing_child_count": len(missing_children),
        "expected_assignment_clauses_found": index["metadata"]["expected_assignment_clauses_found"],
        "validation_errors": validation_errors,
    }


def build_clause_index(
    *,
    url: str,
    fetched_at: str,
    raw_html: str,
    clean_text: str,
    raw_html_path: Path,
    clean_text_path: Path,
) -> dict[str, Any]:
    source_hash = sha256_text(raw_html)
    lines = legal_lines_with_spans(clean_text)
    records: list[dict[str, Any]] = []
    section_records: list[dict[str, Any]] = []
    clause_records: list[dict[str, Any]] = []
    seen_clause_texts: dict[str, set[str]] = {}
    part_boundaries: list[dict[str, Any]] = []

    current_part_key = "Unparted"
    current_part_title: str | None = None
    current_subpart_title: str | None = None
    current_section_id: str | None = None
    current_section_title: str | None = None
    current_section_source_text: str | None = None
    current_section_number: str | None = None
    current_section_span: tuple[int, int] | None = None
    current_section_children: list[str] = []
    last_numbered_clause_id: str | None = None
    last_numbered_clause_number: str | None = None
    child_item_counter = 0
    inferred_section_counter = 0
    inferred_clause_counter_by_section: dict[str, int] = {}
    span_counter = 0

    def next_span_id() -> str:
        nonlocal span_counter
        span_counter += 1
        return f"rzp_terms_{dt.datetime.now(dt.UTC).year}_{span_counter:05d}"

    def provenance() -> dict[str, str]:
        return {
            "source_url": url,
            "source_hash": source_hash,
            "source_fetched_at": fetched_at,
            "raw_html_path": str(raw_html_path.relative_to(ROOT)).replace("\\", "/"),
            "clean_text_path": str(clean_text_path.relative_to(ROOT)).replace("\\", "/"),
        }

    def citation(section_title: str, clause_number: str | None = None) -> str:
        pieces = []
        if current_part_title:
            pieces.append(current_part_title.replace(":", ""))
        if current_subpart_title:
            pieces.append(current_subpart_title.replace(":", ""))
        if clause_number:
            pieces.append(f"Clause {clause_number}")
        elif current_section_number:
            pieces.append(f"Section {current_section_number}")
        pieces.append(section_title)
        return ", ".join(pieces)

    def flush_section() -> None:
        nonlocal current_section_id, current_section_title, current_section_source_text
        nonlocal current_section_number, current_section_span, current_section_children
        if not current_section_id or not current_section_title or not current_section_source_text or not current_section_span:
            return
        section_records.append(
            {
                "source_span_id": next_span_id(),
                "clause_id": current_section_id,
                "display_citation": citation(current_section_title),
                "hierarchy": {
                    "document": DOCUMENT_TITLE,
                    "part": current_part_title,
                    "subpart": current_subpart_title,
                    "part_key": current_part_key,
                    "section_id": current_section_id,
                    "section_title": current_section_title,
                    "clause_number": current_section_number,
                    "heading_level": None,
                },
                "text": current_section_source_text,
                "normalized_text": normalize_ws(current_section_source_text).lower(),
                "span_location": {
                    "char_start": current_section_span[0],
                    "char_end": current_section_span[1],
                },
                "relationships": {
                    "parent_clause_id": None,
                    "child_clause_ids": current_section_children,
                    "sibling_clause_ids": [],
                },
                "references": {
                    "cross_references_raw": detect_cross_references(current_section_title),
                    "cross_references_resolved": [],
                    "unresolved_cross_references": [],
                    "external_references": detect_external_references(current_section_title),
                },
                "taxonomy": record_taxonomy("", current_section_title, current_part_key),
                "provenance": provenance(),
                "record_type": "section",
            }
        )

    def reset_section_state() -> None:
        nonlocal current_section_id, current_section_title, current_section_source_text
        nonlocal current_section_number, current_section_span, current_section_children
        nonlocal last_numbered_clause_id, last_numbered_clause_number, child_item_counter
        current_section_id = None
        current_section_title = None
        current_section_source_text = None
        current_section_number = None
        current_section_span = None
        current_section_children = []
        last_numbered_clause_id = None
        last_numbered_clause_number = None
        child_item_counter = 0

    def set_section(title: str, line: TextLine, number: str | None = None) -> None:
        nonlocal current_section_id, current_section_title, current_section_source_text
        nonlocal current_section_number, current_section_span, current_section_children
        nonlocal last_numbered_clause_id, last_numbered_clause_number, child_item_counter, inferred_section_counter
        flush_section()
        source_text = line.text
        clean_title = clean_section_title(title)
        if current_part_key == "PartA" and clean_title == "SUSPENSION AND TERMINATION" and current_section_number == "16":
            clean_title = "PROHIBITED PRODUCTS AND SERVICES"
            number = "17"
        if clean_title == "Preamble":
            section_id = f"{current_part_key}.Preamble"
        elif current_part_key == "PartA" and clean_title == "DEFINITIONS":
            section_id = "PartA.definitions"
            number = None
        else:
            if not number:
                inferred_section_counter += 1
                number = str(inferred_section_counter)
            section_id = make_section_id(current_part_key, clean_title, number)
        current_section_id = section_id
        current_section_title = clean_title
        current_section_source_text = source_text
        current_section_number = number
        current_section_span = (line.char_start, line.char_end)
        current_section_children = []
        last_numbered_clause_id = None
        last_numbered_clause_number = None
        child_item_counter = 0

    def ensure_preamble(line: TextLine) -> None:
        if not current_section_id:
            set_section("Preamble", line, None)

    def add_clause(line: TextLine, number: str, text: str, is_explicit: bool) -> None:
        nonlocal current_section_id, current_section_number, last_numbered_clause_id, last_numbered_clause_number, child_item_counter
        if not current_section_id or not current_section_title:
            ensure_preamble(line)

        if (
            is_explicit
            and current_section_title != "Preamble"
            and current_section_id
            and "." in number
            and current_section_number != number.split(".", 1)[0]
            and not current_section_children
        ):
            current_section_number = number.split(".", 1)[0]
            current_section_id = make_section_id(current_part_key, current_section_title, current_section_number)

        clause_id = f"{current_part_key}.{number}"
        normalized_clause_text = normalize_ws(text).lower()
        existing_texts = seen_clause_texts.setdefault(clause_id, set())
        if normalized_clause_text in existing_texts:
            return
        if existing_texts:
            variant_number = len(existing_texts) + 1
            existing_texts.add(normalized_clause_text)
            clause_id = f"{clause_id}.Variant{variant_number:03d}"
        else:
            existing_texts.add(normalized_clause_text)

        if is_explicit or not last_numbered_clause_id or current_part_key == "PartB.PartI":
            current_section_children.append(clause_id)
            parent_clause_id = current_section_id
        else:
            parent_clause_id = last_numbered_clause_id

        raw_refs = detect_cross_references(text)
        clause_records.append(
            {
                "source_span_id": next_span_id(),
                "clause_id": clause_id,
                "display_citation": citation(current_section_title or "Preamble", number),
                "hierarchy": {
                    "document": DOCUMENT_TITLE,
                    "part": current_part_title,
                    "subpart": current_subpart_title,
                    "part_key": current_part_key,
                    "section_id": current_section_id,
                    "section_title": current_section_title,
                    "clause_number": number,
                    "heading_level": None,
                    "source_order": line.source_order,
                    "block_kind": "paragraph" if is_explicit else "inferred_clause",
                },
                "text": text,
                "normalized_text": normalized_clause_text,
                "span_location": {
                    "char_start": line.char_start,
                    "char_end": line.char_end,
                },
                "relationships": {
                    "parent_clause_id": parent_clause_id,
                    "child_clause_ids": [],
                    "sibling_clause_ids": [],
                },
                "references": {
                    "cross_references_raw": raw_refs,
                    "cross_references_resolved": [],
                    "unresolved_cross_references": raw_refs,
                    "external_references": detect_external_references(text),
                },
                "taxonomy": record_taxonomy(text, current_section_title or "", current_part_key),
                "provenance": provenance(),
            }
        )
        if is_explicit:
            last_numbered_clause_id = clause_id
            last_numbered_clause_number = number
            child_item_counter = 0

    for line in lines:
        if line.text == "-":
            continue

        part_heading = parse_part_heading(line.text)
        if part_heading:
            flush_section()
            current_part_key = part_heading["key"]
            if part_heading["kind"] == "top":
                current_part_title = part_heading["title"]
                current_subpart_title = None
            else:
                current_part_title = "Part B: SPECIFIC TERMS AND CONDITIONS"
                current_subpart_title = part_heading["title"]
            reset_section_state()
            inferred_section_counter = 0
            inferred_clause_counter_by_section = {}
            part_boundaries.append(
                {
                    "part_key": current_part_key,
                    "title": part_heading["title"],
                    "source_order": line.source_order,
                    "char_start": line.char_start,
                    "char_end": line.char_end,
                }
            )
            set_section("Preamble", line, None)
            continue

        numeric_match = re.match(r"^(\d+(?:\.\d+)+)\s+(.*)$", line.text)
        if is_probable_section_heading(line.text):
            set_section(line.text, line, None)
            continue

        ensure_preamble(line)
        if numeric_match:
            add_clause(line, numeric_match.group(1), line.text, True)
        else:
            if last_numbered_clause_id and last_numbered_clause_number and current_part_key != "PartB.PartI":
                child_item_counter += 1
                number = f"{last_numbered_clause_number}.item{child_item_counter:03d}"
            else:
                key = current_section_id or current_part_key
                inferred_clause_counter_by_section[key] = inferred_clause_counter_by_section.get(key, 0) + 1
                if current_section_number:
                    number = f"{current_section_number}.{inferred_clause_counter_by_section[key]}"
                else:
                    number = str(inferred_clause_counter_by_section[key])
            add_clause(line, number, line.text, False)

    flush_section()

    all_records = section_records + clause_records
    id_set = {record["clause_id"] for record in all_records}
    section_id_set = {record["clause_id"] for record in section_records}
    clause_id_set = {record["clause_id"] for record in clause_records}

    children_by_parent: dict[str, list[str]] = {}
    for record in clause_records:
        parent_id = record["relationships"]["parent_clause_id"]
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(record["clause_id"])
    for record in all_records:
        record_id = record["clause_id"]
        if record.get("record_type") == "section":
            record["relationships"]["child_clause_ids"] = children_by_parent.get(record_id, record["relationships"]["child_clause_ids"])
        else:
            parent_id = record["relationships"]["parent_clause_id"]
            siblings = [cid for cid in children_by_parent.get(parent_id, []) if cid != record_id]
            record["relationships"]["sibling_clause_ids"] = siblings
            record["relationships"]["child_clause_ids"] = children_by_parent.get(record_id, [])
            resolved, unresolved = resolve_cross_references(
                record["references"]["cross_references_raw"],
                record["text"],
                record["hierarchy"]["part_key"],
                clause_id_set,
                section_id_set,
            )
            record["references"]["cross_references_resolved"] = resolved
            record["references"]["unresolved_cross_references"] = unresolved
            record["record_type"] = "clause"

    expected_targets = {
        "3.4": "PartB.PartI.3.4",
        "3.5": "PartB.PartI.3.5",
        "4.1": "PartB.PartI.4.1",
        "4.2": "PartB.PartI.4.2",
        "4.3": "PartB.PartI.4.3",
        "4.5": "PartB.PartI.4.5",
        "16.1": "PartA.16.1",
    }
    expected_found = {label: target in id_set for label, target in expected_targets.items()}
    warnings = []
    if not all(expected_found.values()):
        missing = [clause for clause, found in expected_found.items() if not found]
        warnings.append("Expected assignment clauses missing canonical IDs: " + ", ".join(missing))

    index = {
        "metadata": {
            "document": DOCUMENT_TITLE,
            "source_url": url,
            "source_hash": source_hash,
            "source_fetched_at": fetched_at,
            "raw_html_path": str(raw_html_path.relative_to(ROOT)).replace("\\", "/"),
            "clean_text_path": str(clean_text_path.relative_to(ROOT)).replace("\\", "/"),
            "parser_version": PARSER_VERSION,
            "record_count": len(all_records),
            "section_count": len(section_records),
            "clause_count": len(clause_records),
            "expected_assignment_clauses_found": expected_found,
            "part_boundaries_found": part_boundaries,
            "warnings": warnings,
        },
        "records": all_records,
    }
    validation_report = validate_clause_index(index)
    index["metadata"]["validation_errors"] = validation_report["validation_errors"]
    return index


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL, help="Terms URL to fetch.")
    parser.add_argument("--skip-fetch", action="store_true", help="Parse the existing raw HTML snapshot.")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    fetched_at = utc_now()
    final_url = args.url
    if args.skip_fetch:
        if not RAW_HTML_PATH.exists():
            print(f"Missing existing snapshot: {RAW_HTML_PATH}", file=sys.stderr)
            return 1
        raw_html = RAW_HTML_PATH.read_text(encoding="utf-8")
        snapshot = json.loads(SNAPSHOT_META_PATH.read_text(encoding="utf-8")) if SNAPSHOT_META_PATH.exists() else {}
        fetched_at = snapshot.get("source_fetched_at", fetched_at)
        final_url = snapshot.get("source_url", args.url)
    else:
        final_url, raw_html = fetch_source(args.url)
        RAW_HTML_PATH.write_text(raw_html, encoding="utf-8")

    full_clean_text = extract_clean_text(raw_html)
    clean_text = extract_legal_text(full_clean_text)
    CLEAN_TEXT_PATH.write_text(clean_text + "\n", encoding="utf-8")
    snapshot_meta = {
        "document": DOCUMENT_TITLE,
        "source_url": final_url,
        "requested_url": args.url,
        "source_fetched_at": fetched_at,
        "source_hash": sha256_text(raw_html),
        "raw_html_path": str(RAW_HTML_PATH.relative_to(ROOT)).replace("\\", "/"),
        "clean_text_path": str(CLEAN_TEXT_PATH.relative_to(ROOT)).replace("\\", "/"),
        "raw_html_bytes": len(raw_html.encode("utf-8")),
        "clean_text_chars": len(clean_text),
    }
    write_json(SNAPSHOT_META_PATH, snapshot_meta)

    clause_index = build_clause_index(
        url=final_url,
        fetched_at=fetched_at,
        raw_html=raw_html,
        clean_text=clean_text,
        raw_html_path=RAW_HTML_PATH,
        clean_text_path=CLEAN_TEXT_PATH,
    )
    write_json(CLAUSE_INDEX_PATH, clause_index)
    validation_report = validate_clause_index(clause_index)
    write_json(VALIDATION_REPORT_PATH, validation_report)

    print(f"Wrote {RAW_HTML_PATH.relative_to(ROOT)}")
    print(f"Wrote {CLEAN_TEXT_PATH.relative_to(ROOT)}")
    print(f"Wrote {SNAPSHOT_META_PATH.relative_to(ROOT)}")
    print(f"Wrote {CLAUSE_INDEX_PATH.relative_to(ROOT)}")
    print(f"Wrote {VALIDATION_REPORT_PATH.relative_to(ROOT)}")
    print(
        "Parsed "
        f"{clause_index['metadata']['section_count']} sections and "
        f"{clause_index['metadata']['clause_count']} clause records."
    )
    for warning in clause_index["metadata"]["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in validation_report["validation_errors"]:
        print(f"VALIDATION ERROR: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
