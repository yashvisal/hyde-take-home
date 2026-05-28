#!/usr/bin/env python3
"""Generate the v1 Razorpay synthetic Q&A dataset.

The script owns the deterministic control plane: coverage plan, selected source
records, parser-owned metadata, validation, ordering, and output files.
The row wording/reasoning is represented as a model-output-shaped object before
being assembled into the final schema row.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.generation_quality import count_quality_errors, has_visible_citation, validate_generation_quality
from src.schemas import RESPONSE_MODE_BY_CATEGORY, SCHEMA_VERSION, load_clause_index, validate_dataset, validate_dataset_row


CLAUSE_INDEX_PATH = ROOT / "data" / "processed" / "clause_index.json"
OUTPUT_DIR = ROOT / "data" / "output"
DATASET_PATH = OUTPUT_DIR / "razorpay_synthetic_qa.jsonl"
RUN_MANIFEST_PATH = OUTPUT_DIR / "run_manifest.json"
GENERATION_SUMMARY_PATH = OUTPUT_DIR / "generation_summary.md"

RUN_ID = "2026-05-22_generation_v1"
PROMPT_VERSION = "generation_v1"
DEFAULT_GENERATOR_MODEL = "gpt-5.5"
DEFAULT_GENERATOR_TEMPERATURE = 1.0
PYTHON_RANDOM_SEED = 42
MAX_ATTEMPTS_PER_ROW = 3
DEFAULT_WORKERS = 5
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


@dataclass(frozen=True)
class PlanItem:
    id: str
    issue_id: str
    category: str
    section_id: str
    source_ids: tuple[str, ...]
    behavior_goal: str
    question: str
    assistant: str
    known_facts: tuple[str, ...] = ()
    missing_fact: str | None = None
    missing_fact_type: str | None = None
    why_it_matters: str | None = None
    clarifying_question: str | None = None
    outcomes: tuple[tuple[str, str, str], ...] = ()
    ambiguity_type: str | None = None
    ambiguity_explanation: str | None = None


@dataclass(frozen=True)
class RunConfig:
    model: str
    temperature: float
    prompt_version: str
    max_attempts: int
    template_only: bool
    workers: int
    quality_gate: bool


@dataclass
class GenerationStats:
    schema_validation_rejections: int = 0
    source_quality_rejections: int = 0
    category_fit_rejections: int = 0
    missing_visible_citation_rejections: int = 0
    weak_quote_rejections: int = 0
    weak_known_facts_rejections: int = 0
    generic_answer_rejections: int = 0
    accepted_after_retry: int = 0
    final_quality_lint_flags: int = 0
    source_quality_flags: int = 0
    category_fit_flags: int = 0
    missing_visible_citation_flags: int = 0
    weak_quote_flags: int = 0
    weak_known_facts_flags: int = 0
    generic_answer_flags: int = 0
    citation_repairs: int = 0
    source_plan_review_flags: int = 0
    source_plan_replacements: int = 0

    def record_schema_errors(self, errors: list[str]) -> None:
        if errors:
            self.schema_validation_rejections += 1

    def record_quality_errors(self, errors: list[str]) -> None:
        if errors:
            counts = count_quality_errors(errors)
            self.source_quality_rejections += counts.get("source_quality", 0)
            self.category_fit_rejections += counts.get("category_fit", 0)
            self.missing_visible_citation_rejections += counts.get("missing_visible_citation", 0)
            self.weak_quote_rejections += counts.get("weak_quote", 0)
            self.weak_known_facts_rejections += counts.get("weak_known_facts", 0)
            self.generic_answer_rejections += counts.get("generic_answer", 0)

    def record_quality_warnings(self, warnings: list[str]) -> None:
        if warnings:
            counts = count_quality_errors(warnings)
            self.source_quality_flags += counts.get("source_quality", 0)
            self.category_fit_flags += counts.get("category_fit", 0)
            self.missing_visible_citation_flags += counts.get("missing_visible_citation", 0)
            self.weak_quote_flags += counts.get("weak_quote", 0)
            self.weak_known_facts_flags += counts.get("weak_known_facts", 0)
            self.generic_answer_flags += counts.get("generic_answer", 0)

    def merge(self, other: "GenerationStats") -> None:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))

    def to_dict(self) -> dict[str, int]:
        return {field_name: getattr(self, field_name) for field_name in self.__dataclass_fields__}


CLEAR_ITEMS = [
    ("rzp_clear_001", "refund_fees_remain_payable", "PartB.PartI.3.refunds", ("PartB.PartI.3.4",), "If I refund a customer's payment, do we still owe Razorpay PA fees?", "Yes. The ToS says Razorpay PA fees remain applicable and payable for each transaction even if you refund the customer."),
    ("rzp_clear_002", "late_authorized_uncaptured_auto_refund", "PartB.PartI.3.refunds", ("PartB.PartI.3.5",), "What happens if a payment is authorized late but we do not capture it?", "Razorpay PA may initiate an auto-refund to the customer within five days for late-authorized payments that you do not capture."),
    ("rzp_clear_003", "facility_provider_fraud_notice_settlement_suspension", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.1",), "If a Facility Provider reports an unauthorized debit, can Razorpay pause settlements?", "Yes. If Razorpay PA is informed by a Facility Provider that a customer reported an unauthorized debit, Razorpay PA can suspend settlements during the investigation and resolution process."),
    ("rzp_clear_004", "suspension_rights_triggered", "PartA.16.suspension_and_termination", ("PartA.16.1",), "Can Razorpay PA immediately suspend services or settlement in the listed termination events?", "Yes. The ToS gives Razorpay PA the right to immediately suspend services and settlement without liability when the listed events occur."),
    ("rzp_clear_005", "prohibited_products_services_restriction", "PartA.17.prohibited_products_and_services", ("PartA.17.1",), "Can we use Razorpay for products or services that are prohibited under the ToS?", "No. The prohibited-products section restricts use of Razorpay services for listed prohibited products or services."),
    ("rzp_clear_006", "data_protection_obligations", "PartA.12.data_protection", ("PartA.12.1",), "Do we have data protection obligations under the Razorpay terms?", "Yes. The data protection section imposes obligations tied to how protected or personal data is handled under the ToS."),
    ("rzp_clear_007", "card_network_rule_changes_bind_merchant", "PartA.9.card_association_rules", ("PartA.9.1",), "If card network rules change, can those changes become binding on us immediately?", "Yes. The ToS says amendments required by card network rule changes are deemed binding on you with immediate effect."),
    ("rzp_clear_008", "payment_processing_terms_apply", "PartB.PartI.1.payment_processing", ("PartB.PartI.1.1",), "Do the payment aggregation terms apply to our use of Razorpay PA payment processing?", "Yes. The payment processing section sets the specific terms for Razorpay PA payment aggregation services."),
    ("rzp_clear_009", "chargeback_terms_apply", "PartB.PartI.2.chargebacks", ("PartB.PartI.2.1",), "Does the ToS include a specific chargeback framework for payment aggregation?", "Yes. The chargebacks section sets out terms that apply when chargebacks arise under the payment aggregation services."),
    ("rzp_clear_010", "emandate_terms_apply", "PartB.PartII.Preamble", ("PartB.PartII.3",), "Do separate terms apply when we use Razorpay's e-mandate services?", "Yes. Part B, Part II contains specific terms for e-mandate services."),
    ("rzp_clear_011", "offline_payment_device_terms_apply", "PartB.PartIA.Preamble", ("PartB.PartIA.1.2",), "Do offline payment aggregation or device terms apply separately from the general terms?", "Yes. Part IA adds specific offline payment aggregation and device terms."),
    ("rzp_clear_012", "cross_border_outward_terms_apply", "PartB.PartIB.1.payment_processing", ("PartB.PartIB.1.1",), "Do PA-CB outward transactions have their own payment processing terms?", "Yes. Part IB sets specific terms for PA-CB outward transaction payment processing."),
    ("rzp_clear_013", "magic_checkout_rto_terms_apply", "PartB.PartVI.2.rto_protection", ("PartB.PartVI.2.4",), "Do undefined capitalized terms in Magic Checkout RTO Protection use the meaning from the main Terms?", "Yes. The Magic Checkout RTO Protection section says capitalized terms not defined there have the meaning given in the Terms."),
    ("rzp_clear_014", "general_fee_terms_apply", "PartA.3.fees", ("PartA.3.1",), "Are fees governed by the fee provisions in Part A?", "Yes. Part A's fee section contains the general fee provisions that apply under the Terms."),
    ("rzp_clear_015", "user_service_usage_terms_apply", "PartA.2.usage_of_the_services_by_the_user", ("PartA.2.1",), "Do we have to follow the ToS rules when using Razorpay services?", "Yes. The usage section sets obligations for using the Razorpay services under the Terms."),
]

CLARIFICATION_ITEMS = [
    ("rzp_clarification_001", "fraud_post_settlement_chargeback_status", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.2", "PartB.PartI.4.3", "PartB.PartI.4.5"), "A fraudulent transaction happened and Razorpay already settled the money to us. What happens now?", "Did the fraudulent transaction result in a chargeback?", "Whether the fraudulent transaction resulted in a chargeback.", "status", "The ToS routes chargeback outcomes through the chargeback framework, while post-settlement fraud disputes also reference external RBI rules.", (("If the fraud resulted in a chargeback.", "The chargeback provisions may apply.", "PartB.PartI.4.3"), ("If the issue is a post-settlement fraud dispute without a chargeback.", "The dispute may be handled under the RBI-referenced framework.", "PartB.PartI.4.2"))),
    ("rzp_clarification_002", "facility_provider_notice_status", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.1", "PartA.16.1"), "A customer says their payment instrument was used without authorization. Can Razorpay suspend our settlement?", "Has a Facility Provider intimated Razorpay PA that the customer reported the unauthorized debit?", "Whether Razorpay PA received a Facility Provider intimation.", "status", "Clause 4.1 turns on Razorpay PA being intimated by a Facility Provider.", (("If a Facility Provider has intimated Razorpay PA.", "Razorpay PA may suspend settlements during inquiry and resolution.", "PartB.PartI.4.1"), ("If there has been no Facility Provider intimation.", "Clause 4.1 may not be the right basis for suspension on these facts.", "PartB.PartI.4.1"))),
    ("rzp_clarification_003", "authorization_capture_status", "PartB.PartI.3.refunds", ("PartB.PartI.3.4", "PartB.PartI.3.5"), "A payment authorization came in late and the customer wants money back. Do we need to do anything?", "Was the payment captured by you, or was it late-authorized but not captured?", "Whether the payment was captured by the merchant.", "status", "The refund-fee rule and late-authorized auto-refund rule address different transaction states.", (("If the payment was late-authorized and not captured.", "Razorpay PA may initiate auto-refund within five days.", "PartB.PartI.3.5"), ("If you captured and then refunded the transaction.", "Razorpay PA fees remain payable despite the refund.", "PartB.PartI.3.4"))),
    ("rzp_clarification_004", "chargeback_documentation_status", "PartB.PartI.2.chargebacks", ("PartB.PartI.2.1", "PartB.PartI.2.2"), "We received a chargeback notice. What exactly happens next?", "What chargeback stage or documentation request has Razorpay sent you?", "The current chargeback stage or documentation request.", "status", "The chargeback section can require different actions depending on the stage and documentation requested.", (("If Razorpay has requested supporting documents.", "You should respond under the chargeback process described in the ToS.", "PartB.PartI.2.1"), ("If the chargeback has already been resolved by a provider or network.", "The result may follow the applicable chargeback outcome under that process.", "PartB.PartI.2.2"))),
    ("rzp_clarification_005", "cross_border_outward_applicability", "PartB.PartIB.1.payment_processing", ("PartB.PartIB.1.1", "PartB.PartIB.1.2"), "We are processing an outward cross-border payment. Do the PA-CB terms apply?", "Is the transaction a PA-CB outward transaction covered by Part IB?", "Whether the transaction is a PA-CB outward transaction.", "transaction_type", "Part IB applies specifically to PA-CB outward transactions.", (("If it is a PA-CB outward transaction.", "The Part IB payment processing terms apply.", "PartB.PartIB.1.1"), ("If it is not a PA-CB outward transaction.", "Another part of the Terms may govern the payment flow.", "PartB.PartIB.1.2"))),
    ("rzp_clarification_006", "offline_device_context", "PartB.PartIA.Preamble", ("PartB.PartIA.1.1", "PartB.PartIA.1.3"), "We want to use an offline payment device. Which terms control?", "Are you using Razorpay's offline payment aggregation or device offering?", "Whether the offline payment aggregation/device terms apply to the setup.", "transaction_type", "Part IA applies to offline payment aggregation and device terms.", (("If the device/offline payment terms apply.", "Part IA provides the applicable additional terms.", "PartB.PartIA.1.1"), ("If this is not an offline/device use case.", "The general or payment aggregation terms may be more relevant.", "PartB.PartIA.1.3"))),
    ("rzp_clarification_007", "emandate_authorization_status", "PartB.PartII.Preamble", ("PartB.PartII.1", "PartB.PartII.2"), "Can we debit a customer through e-mandate for a recurring payment?", "Has the customer completed the required e-mandate authorization for this debit?", "Whether the customer has completed the required e-mandate authorization.", "status", "The e-mandate terms depend on the mandate and authorization context.", (("If the required e-mandate authorization exists.", "The e-mandate terms may support processing under that mandate.", "PartB.PartII.1"), ("If authorization is missing or invalid.", "The debit may not be supportable under the e-mandate terms.", "PartB.PartII.2"))),
    ("rzp_clarification_008", "refund_funds_availability", "PartB.PartI.3.refunds", ("PartB.PartI.3.1", "PartB.PartI.3.2"), "Can Razorpay process refunds if our balance is not enough?", "Are sufficient funds available for the refund under the refund process?", "Whether sufficient funds are available for the refund.", "status", "Refund handling can depend on the refund process and available funds.", (("If sufficient funds are available.", "The refund can proceed under the refund provisions.", "PartB.PartI.3.1"), ("If sufficient funds are not available.", "The refund may require additional funding or a different handling path.", "PartB.PartI.3.2"))),
    ("rzp_clarification_009", "suspension_event_trigger", "PartA.16.suspension_and_termination", ("PartA.16.1", "PartA.16.1.item001"), "Razorpay suspended our services. Is that allowed?", "Which event or ground did Razorpay rely on for suspension?", "The specific suspension ground Razorpay relied on.", "other", "Clause 16.1 allows immediate suspension when listed events occur, so the applicable event matters.", (("If a listed Clause 16.1 event occurred.", "Immediate suspension may be permitted under the ToS.", "PartA.16.1"), ("If no listed event applies.", "The cited suspension basis may need further review against the ToS.", "PartA.16.1.item001"))),
    ("rzp_clarification_010", "prohibited_service_classification", "PartA.17.prohibited_products_and_services", ("PartA.17.1", "PartA.17.2"), "Can we sell this product through Razorpay?", "What product or service category are you selling?", "The product or service category.", "transaction_type", "The prohibited-products section depends on whether the product or service falls within a listed restricted category.", (("If the product is in a prohibited category.", "The ToS may prohibit using Razorpay for it.", "PartA.17.1"), ("If it is not in a prohibited category.", "This specific prohibited-products basis may not block it.", "PartA.17.2"))),
    ("rzp_clarification_011", "data_processing_role_context", "PartA.12.data_protection", ("PartA.12.1", "PartA.12.2"), "Can we store or process payment-related customer data ourselves?", "What data are you storing and in what role are you processing it?", "The type of data and the merchant's processing role.", "party", "The data protection obligations depend on what data is processed and which party is handling it.", (("If the data is covered by the data protection provisions.", "The Part A data protection obligations apply.", "PartA.12.1"), ("If the data or role is outside that context.", "A different privacy or contractual obligation may be relevant.", "PartA.12.2"))),
    ("rzp_clarification_012", "card_network_rule_trigger", "PartA.9.card_association_rules", ("PartA.9.1", "PartA.9.2"), "Do we need to follow a new card-network operational rule immediately?", "Is the change required by a card network rule or guideline amendment?", "Whether the obligation comes from a card network rule amendment.", "regulatory_context", "The card association provision specifically addresses amendments required by card network rules.", (("If it is required by card network rules.", "The amendment may be immediately binding under the ToS.", "PartA.9.1"), ("If it is not a card network rule change.", "Clause 9.1 may not be the controlling basis.", "PartA.9.2"))),
    ("rzp_clarification_013", "marketplace_submerchant_context", "PartB.PartIB.1.specific_terms_for_payment_aggregators_e_commerce_marketplaces_onboarded_as_merchants", ("PartB.PartIB.1.1", "PartB.PartIB.1.2"), "We are a marketplace with sub-merchants. Which payment terms apply?", "Are you onboarded as a marketplace/payment aggregator merchant under the Part IB context?", "Whether the merchant is a marketplace/payment aggregator in the covered context.", "party", "The marketplace/sub-merchant terms depend on the merchant's role and onboarding context.", (("If you are onboarded in the covered marketplace/payment aggregator context.", "The specific Part IB marketplace terms may apply.", "PartB.PartIB.1.1"), ("If you are not in that role.", "The ordinary payment processing terms may be more relevant.", "PartB.PartIB.1.2"))),
    ("rzp_clarification_014", "magic_checkout_rto_context", "PartB.PartVI.2.rto_protection", ("PartB.PartVI.2.1", "PartB.PartVI.2.4"), "Can we claim RTO Protection for this Magic Checkout order?", "Is this order within the Magic Checkout RTO Protection scope and conditions?", "Whether the order satisfies the RTO Protection scope and conditions.", "status", "The Magic Checkout RTO Protection terms apply only in their defined context.", (("If the order falls within RTO Protection scope.", "The Magic Checkout RTO terms may apply.", "PartB.PartVI.2.1"), ("If it does not fall within that scope.", "The RTO Protection terms may not support the claim.", "PartB.PartVI.2.4"))),
    ("rzp_clarification_015", "fee_invoice_context", "PartA.3.fees", ("PartA.3.1", "PartA.3.2"), "Do we have to pay this Razorpay invoice now?", "What fee, invoice, or pricing provision is the charge based on?", "The specific fee or invoice basis for the charge.", "other", "The fee provisions apply differently depending on the charge or invoice at issue.", (("If the invoice is for fees payable under the Terms.", "The fee provisions support Razorpay's charge.", "PartA.3.1"), ("If the charge is not tied to a fee provision.", "The fee section may not fully answer the question.", "PartA.3.2"))),
]

AMBIGUITY_ITEMS = [
    ("rzp_ambiguity_001", "settlement_hold_duration_undefined", "PartA.16.suspension_and_termination", ("PartA.16.1",), "How long can Razorpay hold our settlement after suspending services?", "undefined_timeline", "The ToS gives Razorpay PA suspension and settlement-suspension rights, but this cited text does not define a fixed maximum hold duration."),
    ("rzp_ambiguity_002", "fraud_threshold_details_external", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.5",), "What exact fraud threshold triggers additional consequences?", "undefined_threshold", "The ToS references fraud-threshold handling, but the cited text does not fully define the operative threshold details."),
    ("rzp_ambiguity_003", "rbi_post_settlement_fraud_rules_external", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.2",), "Under the RBI notifications, who ultimately bears the loss for a post-settlement fraudulent transaction?", "external_regulation", "The ToS points to RBI notifications and future RBI guidance, but does not reproduce the full external rule or determine the final outcome by itself."),
    ("rzp_ambiguity_004", "card_network_operational_rule_content", "PartA.9.card_association_rules", ("PartA.9.1",), "What exactly do the current card-network rules require us to change this week?", "external_regulation", "The ToS says card-network rule amendments can become binding, but it does not state the current external card-network rule content."),
    ("rzp_ambiguity_005", "facility_provider_investigation_outcome", "PartB.PartI.4.fraudulent_transactions", ("PartB.PartI.4.1",), "Will the Facility Provider decide the fraud investigation in our favor?", "tos_silent", "The ToS describes suspension during Facility Provider inquiries, but it does not predict or specify the investigation outcome."),
    ("rzp_ambiguity_006", "chargeback_document_sufficiency", "PartB.PartI.2.chargebacks", ("PartB.PartI.2.1",), "Will the documents we have be enough to win a chargeback?", "tos_silent", "The chargeback provisions can describe process obligations, but the ToS does not guarantee that a particular evidence package will succeed."),
    ("rzp_ambiguity_007", "unacceptable_risk_standard", "PartA.16.suspension_and_termination", ("PartA.16.1.item001",), "What exactly counts as unacceptable risk for suspension?", "vague_term", "The ToS may permit suspension for risk-related grounds, but the cited text does not define every operational threshold for unacceptable risk."),
    ("rzp_ambiguity_008", "real_money_gaming_boundary", "PartA.14.additional_terms", ("PartA.14.10", "PartA.17.14"), "We're building a real-money fantasy gaming feature. Can we use Razorpay to collect payments for it?", "external_regulation", "The ToS identifies real-money online gaming and gaming/gambling restrictions, but the boundary for a specific gaming product may require applying external gaming law and product facts not fully resolved by the cited text alone."),
    ("rzp_ambiguity_009", "cross_border_compliance_checks_timing", "PartB.PartIB.1.payment_processing", ("PartB.PartIB.1.1",), "How many days will Razorpay take to complete cross-border compliance checks?", "undefined_timeline", "The PA-CB terms provide a framework for cross-border outward transactions, but the cited text does not specify this operational timeline."),
    ("rzp_ambiguity_010", "offline_device_operational_approval", "PartB.PartIA.Preamble", ("PartB.PartIA.1.3",), "Will Razorpay approve our exact offline device deployment model?", "razorpay_discretion", "The offline device terms govern the device context, but approval of a specific deployment may depend on Razorpay or provider review not fully specified in the cited text."),
    ("rzp_ambiguity_011", "emandate_bank_rejection_reason", "PartB.PartII.Preamble", ("PartB.PartII.1",), "Why did the customer's bank reject this e-mandate?", "external_regulation", "The e-mandate terms govern the service, but the ToS does not state the bank's actual rejection reason or external mandate-processing decision."),
    ("rzp_ambiguity_012", "token_network_deletion_timing", "PartB.PartIII.Preamble", ("PartB.PartIII.Preamble",), "Exactly how long will network token deletion take after a request?", "undefined_timeline", "The TokenHQ section provides the relevant service context, but the cited text does not define the exact operational completion time."),
    ("rzp_ambiguity_013", "subscription_bank_rule_change", "PartB.PartIV.Preamble", ("PartB.PartIV.Preamble",), "Will a new bank rule make our existing subscription flow non-compliant?", "external_regulation", "The subscription terms provide context, but the ToS does not fully reproduce or apply every future external bank rule."),
    ("rzp_ambiguity_014", "magic_checkout_rto_approval_discretion", "PartB.PartVI.2.rto_protection", ("PartB.PartVI.2.1",), "Will Razorpay approve this particular RTO Protection claim?", "razorpay_discretion", "The Magic Checkout RTO terms provide context, but the ToS does not guarantee the outcome of a particular claim from the cited text alone."),
    ("rzp_ambiguity_015", "privacy_policy_external_detail", "PartA.4.privacy_policy", ("PartA.4.1",), "What does Razorpay's Privacy Policy allow for this exact data-sharing scenario?", "external_regulation", "The ToS references privacy obligations and policy context, but the exact data-sharing answer may require the separate Privacy Policy or external law."),
]

PLAN_SOURCE_REPLACEMENTS = {
    "rzp_clarification_006": {"PartB.PartIA.1.3": "PartB.PartIA.1.2"},
    "rzp_clarification_007": {"PartB.PartII.1": "PartB.PartII.3"},
    "rzp_ambiguity_010": {"PartB.PartIA.1.3": "PartB.PartIA.1.1"},
    "rzp_ambiguity_011": {"PartB.PartII.1": "PartB.PartII.3"},
    "rzp_ambiguity_012": {"PartB.PartIII.Preamble": "PartB.PartIII.1"},
    "rzp_ambiguity_013": {"PartB.PartIV.Preamble": "PartB.PartIV.4"},
}


def build_coverage_plan() -> list[PlanItem]:
    items: list[PlanItem] = []
    for row_id, issue_id, section_id, source_ids, question, answer in CLEAR_ITEMS:
        items.append(
            PlanItem(
                id=row_id,
                issue_id=issue_id,
                category="clear_answer",
                section_id=section_id,
                source_ids=source_ids,
                behavior_goal="Answer directly using the cited ToS clause.",
                question=question,
                assistant=answer,
                known_facts=(question.rstrip("?") + ".",),
            )
        )
    for row in CLARIFICATION_ITEMS:
        row_id, issue_id, section_id, source_ids, question, clarifier, missing_fact, fact_type, why, outcomes = row
        items.append(
            PlanItem(
                id=row_id,
                issue_id=issue_id,
                category="clarification_required",
                section_id=section_id,
                source_ids=source_ids,
                behavior_goal="Ask for the missing fact needed to choose the correct ToS branch.",
                question=question,
                assistant=f"{clarifier} The ToS points to different outcomes depending on that fact, so I would not give a final answer until that is clarified.",
                known_facts=(question.rstrip("?") + ".",),
                missing_fact=missing_fact,
                missing_fact_type=fact_type,
                why_it_matters=why,
                clarifying_question=clarifier,
                outcomes=outcomes,
            )
        )
    for row_id, issue_id, section_id, source_ids, question, ambiguity_type, explanation in AMBIGUITY_ITEMS:
        items.append(
            PlanItem(
                id=row_id,
                issue_id=issue_id,
                category="genuine_ambiguity",
                section_id=section_id,
                source_ids=source_ids,
                behavior_goal="Flag a ToS limitation rather than inventing an unsupported answer.",
                question=question,
                assistant=f"The ToS gives some relevant context, but it does not fully resolve this question. {explanation} You should confirm the point with Razorpay or the applicable external source instead of treating the ToS as a complete answer.",
                known_facts=(question.rstrip("?") + ".",),
                ambiguity_type=ambiguity_type,
                ambiguity_explanation=explanation,
            )
        )
    return items


def remap_outcomes(outcomes: tuple[tuple[str, str, str], ...], replacement_map: dict[str, str]) -> tuple[tuple[str, str, str], ...]:
    return tuple((condition, outcome, replacement_map.get(clause_id, clause_id)) for condition, outcome, clause_id in outcomes)


def source_plan_review_flags(item: PlanItem, records: dict[str, dict[str, Any]]) -> list[str]:
    flags = []
    for clause_id in item.source_ids:
        record = records[clause_id]
        text = record.get("text", "").strip()
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if record.get("record_type") == "section":
            flags.append(f"{item.id}: section-only source {clause_id}")
        if normalized in {"definitions:", "you acknowledge and agree that:"}:
            flags.append(f"{item.id}: definition/intro source {clause_id}")
        if len(normalized) < 40:
            flags.append(f"{item.id}: thin source {clause_id}")
        if normalized in {"razorpay pa may provide:", "1.3 razorpay pa may provide:"}:
            flags.append(f"{item.id}: short parent intro {clause_id}")
        if item.category == "genuine_ambiguity" and record.get("record_type") == "section":
            child_ids = record.get("relationships", {}).get("child_clause_ids", [])
            if any(child_id in records and len(records[child_id].get("text", "")) >= 80 for child_id in child_ids):
                flags.append(f"{item.id}: ambiguity uses generic section while substantive child exists {clause_id}")
    return flags


def review_coverage_plan(plan: list[PlanItem], records: dict[str, dict[str, Any]]) -> tuple[list[PlanItem], list[str], int]:
    reviewed = []
    review_flags: list[str] = []
    replacement_count = 0
    for item in plan:
        flags = source_plan_review_flags(item, records)
        review_flags.extend(flags)
        replacement_map = PLAN_SOURCE_REPLACEMENTS.get(item.id, {})
        if replacement_map:
            new_source_ids = tuple(replacement_map.get(clause_id, clause_id) for clause_id in item.source_ids)
            new_outcomes = remap_outcomes(item.outcomes, replacement_map)
            item = replace(item, source_ids=new_source_ids, outcomes=new_outcomes)
            replacement_count += sum(1 for old, new in replacement_map.items() if old != new)
        reviewed.append(item)
    return reviewed, review_flags, replacement_count


def source_role_for(item: PlanItem, clause_id: str) -> str:
    if item.category == "clear_answer":
        return "primary"
    if item.category == "clarification_required":
        return "context" if clause_id == item.section_id else "conditional"
    return "ambiguity_source"


def deterministic_model_output_for(item: PlanItem) -> dict[str, Any]:
    support = [
        {
            "clause_id": clause_id,
            "support_role": source_role_for(item, clause_id),
        }
        for clause_id in item.source_ids
    ]
    output: dict[str, Any] = {
        "user_message": item.question,
        "assistant_message": item.assistant,
        "known_facts": [{"fact": fact, "source": "user"} for fact in item.known_facts],
        "missing_facts": [],
        "clarifying_questions": [],
        "conditional_outcomes": [],
        "ambiguity_reason": None,
        "source_support": support,
    }
    if item.category == "clarification_required":
        output["missing_facts"] = [
            {
                "id": "mf_001",
                "fact": item.missing_fact,
                "why_it_matters": item.why_it_matters,
                "fact_type": item.missing_fact_type,
                "needed_for_clause_ids": sorted({outcome[2] for outcome in item.outcomes}),
                "priority": 1,
            }
        ]
        output["clarifying_questions"] = [
            {
                "question": item.clarifying_question,
                "targets_missing_fact_ids": ["mf_001"],
                "priority": 1,
            }
        ]
        output["conditional_outcomes"] = [
            {
                "condition_summary": condition,
                "required_missing_fact_ids": ["mf_001"],
                "applies_when": {"mf_001": condition.lower().replace(" ", "_").strip(".")},
                "outcome": outcome,
                "source_clause_ids": [clause_id],
            }
            for condition, outcome, clause_id in item.outcomes
        ]
    elif item.category == "genuine_ambiguity":
        output["ambiguity_reason"] = {
            "type": item.ambiguity_type,
            "explanation": item.ambiguity_explanation,
        }
    return output


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_environment() -> None:
    for name in (".env", ".env.local"):
        load_env_file(ROOT / name)


class OpenAIClient:
    def __init__(self, api_key: str, model: str, temperature: float) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
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
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)


def record_payload(record: dict[str, Any], *, context_role: str) -> dict[str, Any]:
    return {
        "context_role": context_role,
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


def hierarchical_context_ids(item: PlanItem, records: dict[str, dict[str, Any]], limit: int = 14) -> list[str]:
    target_ids = set(item.source_ids)
    context_ids: list[str] = []

    def add(clause_id: str | None) -> None:
        if clause_id and clause_id in records and clause_id not in target_ids and clause_id not in context_ids:
            context_ids.append(clause_id)

    add(item.section_id)
    for clause_id in item.source_ids:
        record = records[clause_id]
        add(record.get("relationships", {}).get("parent_clause_id"))
        add(record.get("hierarchy", {}).get("section_id"))
        for sibling_id in record.get("relationships", {}).get("sibling_clause_ids", [])[:5]:
            add(sibling_id)
        for child_id in record.get("relationships", {}).get("child_clause_ids", [])[:8]:
            add(child_id)
        for ref_id in record.get("references", {}).get("cross_references_resolved", [])[:5]:
            add(ref_id)
    return context_ids[:limit]


def source_packets_for(item: PlanItem, records: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "target_source_records": [record_payload(records[clause_id], context_role="target") for clause_id in item.source_ids],
        "context_source_records": [
            record_payload(records[clause_id], context_role="hierarchical_context")
            for clause_id in hierarchical_context_ids(item, records)
        ],
    }


def system_prompt_for(category: str) -> str:
    base = (
        "You generate one intermediate JSON object for a Razorpay Terms of Use synthetic Q&A dataset. "
        "Use only the supplied source records. Do not invent clause IDs, source spans, source text, "
        "external law, Razorpay internal policy, or facts not implied by the target scenario. "
        "Return JSON only. The assistant_message must include a human-readable citation such as "
        "'Under Part B, Part I, Clause 3.4...'. known_facts must be declarative facts, not questions."
    )
    if category == "clear_answer":
        return base + " The row must answer directly in the first sentence and must not ask for clarification."
    if category == "clarification_required":
        return base + " The row must ask a targeted clarifying question and avoid a final answer that depends on unknown facts."
    return base + " The row must flag genuine ToS ambiguity and explain what the ToS does and does not resolve."


def user_prompt_for(
    item: PlanItem,
    records: dict[str, dict[str, Any]],
    *,
    validation_errors: list[str] | None = None,
) -> str:
    expected = deterministic_model_output_for(item)
    instructions = {
        "target": {
            "id": item.id,
            "issue_id": item.issue_id,
            "category": item.category,
            "response_mode": RESPONSE_MODE_BY_CATEGORY[item.category],
            "section_id": item.section_id,
            "behavior_goal": item.behavior_goal,
        },
        "target_source_clause_ids": list(item.source_ids),
        "context_source_clause_ids": hierarchical_context_ids(item, records),
        **source_packets_for(item, records),
        "source_use_rules": [
            "Use target_source_records for source_support and final source_clause citations.",
            "Use context_source_records only to understand hierarchy, neighboring clauses, child clauses, and cross-references.",
            "Do not cite context_source_records in source_support unless their clause_id is also in target_source_clause_ids.",
        ],
        "quote_selection_rules": [
            "Choose the relevant_quote that directly supports the assistant's main answer.",
            "Do not quote a heading, procedural deadline, or unrelated sentence from the same clause if another sentence better supports the answer.",
            "For chargeback liability or deduction answers, quote liability, deduction, debit, chargeback amount, or settlement language.",
            "For suspension answers, quote suspend, terminate, hold, settlement, risk, breach, or investigation language.",
            "For refund-fee answers, quote fees, refund, transaction, applicable, or payable language.",
        ],
        "output_shape": {
            "user_message": "realistic merchant/CTO question",
            "assistant_message": "assistant response matching the target category",
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
                    "relevant_quote": "exact substring from the supplied source record text",
                }
            ],
        },
        "category_constraints": category_constraints(item.category),
        "seed_example_for_shape_only": expected,
    }
    if validation_errors:
        instructions["previous_validation_errors"] = validation_errors
        instructions["repair_instruction"] = "Fix the JSON so it passes these validation errors without changing source IDs."
    return json.dumps(instructions, indent=2, ensure_ascii=False)


def category_constraints(category: str) -> list[str]:
    if category == "clear_answer":
        return [
            "missing_facts, clarifying_questions, and conditional_outcomes must be empty arrays",
            "ambiguity_reason must be null",
            "source_support must include at least one primary support role",
            "assistant_message must answer directly in the first sentence and cite the controlling clause",
            "known_facts must be declarative facts inferred from user_message",
        ]
    if category == "clarification_required":
        return [
            "missing_facts must contain at least one item",
            "clarifying_questions must target missing fact IDs",
            "conditional_outcomes must cite source clause IDs from target_source_clause_ids",
            "ambiguity_reason must be null",
            "assistant_message must ask a targeted missing-fact question and cite the relevant clause or section",
            "known_facts must state what is known from the user question, not restate the question",
        ]
    return [
        "missing_facts, clarifying_questions, and conditional_outcomes should be empty arrays",
        "ambiguity_reason must be present",
        "source_support should use context or ambiguity_source support roles",
        "assistant_message must cite the source and identify the exact ToS gap",
        "known_facts must be declarative facts inferred from user_message",
    ]


def generate_model_output(
    item: PlanItem,
    records: dict[str, dict[str, Any]],
    client: OpenAIClient | None,
    *,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    if client is None:
        return deterministic_model_output_for(item)
    return client.generate_json(
        system_prompt=system_prompt_for(item.category),
        user_prompt=user_prompt_for(item, records, validation_errors=validation_errors),
    )


def normalize_model_output(output: dict[str, Any], item: PlanItem) -> dict[str, Any]:
    normalized = {
        "user_message": output.get("user_message") or item.question,
        "assistant_message": output.get("assistant_message") or item.assistant,
        "known_facts": output.get("known_facts") or [{"fact": item.question.rstrip("?") + ".", "source": "user"}],
        "missing_facts": output.get("missing_facts") or [],
        "clarifying_questions": output.get("clarifying_questions") or [],
        "conditional_outcomes": output.get("conditional_outcomes") or [],
        "ambiguity_reason": output.get("ambiguity_reason"),
        "source_support": output.get("source_support") or [],
    }
    allowed_ids = set(item.source_ids)
    support_by_id = {
        support.get("clause_id"): support
        for support in normalized["source_support"]
        if support.get("clause_id") in allowed_ids
    }
    for clause_id in item.source_ids:
        support_by_id.setdefault(
            clause_id,
            {
                "clause_id": clause_id,
                "support_role": source_role_for(item, clause_id),
            },
        )
    normalized["source_support"] = [support_by_id[clause_id] for clause_id in item.source_ids]
    if item.category == "clear_answer":
        normalized["source_support"][0]["support_role"] = "primary"
    elif item.category == "clarification_required":
        for support in normalized["source_support"]:
            support["support_role"] = "conditional"
    elif item.category == "genuine_ambiguity":
        for support in normalized["source_support"]:
            support["support_role"] = "ambiguity_source"
    return normalized


def build_source_clause(support: dict[str, str], records: dict[str, dict[str, Any]]) -> dict[str, str]:
    record = records[support["clause_id"]]
    return {
        "source_record_type": record["record_type"],
        "source_span_id": record["source_span_id"],
        "clause_id": record["clause_id"],
        "display_citation": record["display_citation"],
        "support_role": support["support_role"],
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


def generation_metadata(index: dict[str, Any], config: RunConfig) -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "source_hash": index["metadata"]["source_hash"],
        "source_fetched_at": index["metadata"]["source_fetched_at"],
        "generator_model": config.model,
        "generator_temperature": config.temperature,
        "prompt_version": config.prompt_version,
    }


def assemble_row(
    item: PlanItem,
    index: dict[str, Any],
    records: dict[str, dict[str, Any]],
    output: dict[str, Any],
    config: RunConfig,
) -> dict[str, Any]:
    output = normalize_model_output(output, item)
    source_clauses = [build_source_clause(support, records) for support in output["source_support"]]
    return {
        "id": item.id,
        "issue_id": item.issue_id,
        "schema_version": SCHEMA_VERSION,
        "category": item.category,
        "response_mode": RESPONSE_MODE_BY_CATEGORY[item.category],
        "messages": [
            {"role": "user", "content": output["user_message"]},
            {"role": "assistant", "content": output["assistant_message"]},
        ],
        "source_clauses": source_clauses,
        "known_facts": output["known_facts"],
        "missing_facts": output["missing_facts"],
        "clarifying_questions": output["clarifying_questions"],
        "conditional_outcomes": output["conditional_outcomes"],
        "ambiguity_reason": output["ambiguity_reason"],
        "coverage_metadata": coverage_metadata(source_clauses, records),
        "generation_metadata": generation_metadata(index, config),
    }


def validate_coverage_plan(plan: list[PlanItem], records: dict[str, dict[str, Any]]) -> None:
    if len(plan) != 45:
        raise RuntimeError(f"COVERAGE_PLAN must contain 45 items, found {len(plan)}.")
    counts = Counter(item.category for item in plan)
    for category, count in {"clear_answer": 15, "clarification_required": 15, "genuine_ambiguity": 15}.items():
        if counts[category] != count:
            raise RuntimeError(f"COVERAGE_PLAN must contain 15 {category} items, found {counts[category]}.")
    for item in plan:
        missing = [clause_id for clause_id in item.source_ids if clause_id not in records]
        if missing:
            raise RuntimeError(f"{item.id} references missing source ids: {', '.join(missing)}")


def log(message: str) -> None:
    print(message, flush=True)


def repair_missing_visible_citation(row: dict[str, Any]) -> bool:
    if has_visible_citation(row):
        return False
    first_source = row["source_clauses"][0]
    assistant_message = row["messages"][1]["content"].rstrip()
    row["messages"][1]["content"] = f"{assistant_message}\n\nSource: {first_source['display_citation']}."
    return True


def quality_gate_mode(config: RunConfig) -> str:
    return "warn_only" if config.quality_gate else "off"


def generate_one(
    *,
    position: int,
    total: int,
    item: PlanItem,
    index: dict[str, Any],
    records: dict[str, dict[str, Any]],
    config: RunConfig,
    client: OpenAIClient | None,
) -> tuple[int, dict[str, Any], int, GenerationStats]:
    stats = GenerationStats()
    last_errors: list[str] = []
    sources = ", ".join(item.source_ids)
    for attempt in range(1, config.max_attempts + 1):
        log(f"[{position}/{total}] generating {item.category} {item.issue_id} attempt={attempt} sources=[{sources}]")
        output = generate_model_output(item, records, client, validation_errors=last_errors or None)
        row = assemble_row(item, index, records, output, config)
        if repair_missing_visible_citation(row):
            stats.citation_repairs += 1
            log(f"[{position}/{total}] repaired missing visible citation {item.issue_id}")
        schema_errors = validate_dataset_row(row, records)
        stats.record_schema_errors(schema_errors)
        if schema_errors:
            last_errors = schema_errors
            log(f"[{position}/{total}] schema validation failed {item.issue_id}: {'; '.join(schema_errors)}")
            continue

        if config.quality_gate:
            quality = validate_generation_quality(row, records)
            warnings = quality.warnings + quality.errors
            stats.final_quality_lint_flags += len(warnings)
            stats.record_quality_warnings(warnings)
            if warnings:
                log(f"[{position}/{total}] quality warnings {item.issue_id}: {'; '.join(warnings)}")

        if attempt > 1:
            stats.accepted_after_retry += 1
        log(f"[{position}/{total}] accepted {item.category} {item.issue_id} attempt={attempt}")
        return position, row, attempt - 1, stats

    raise RuntimeError(f"{item.id} failed validation after {config.max_attempts} attempts: {last_errors}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def workspace_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def write_manifest(
    path: Path,
    index: dict[str, Any],
    rows: list[dict[str, Any]],
    retry_count: int,
    config: RunConfig,
    stats: GenerationStats,
    elapsed_seconds: float,
    *,
    dataset_path: Path,
    summary_path: Path,
) -> None:
    category_counts = Counter(row["category"] for row in rows)
    manifest = {
        "run_id": RUN_ID,
        "source_hash": index["metadata"]["source_hash"],
        "source_fetched_at": index["metadata"]["source_fetched_at"],
        "generator_model": config.model,
        "generator_temperature": config.temperature,
        "python_random_seed": PYTHON_RANDOM_SEED,
        "max_attempts_per_row": config.max_attempts,
        "prompt_version": config.prompt_version,
        "final_row_count": len(rows),
        "category_counts": dict(sorted(category_counts.items())),
        "retry_count": retry_count,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "avg_seconds_per_row": round(elapsed_seconds / len(rows), 2) if rows else None,
        "quality_review_flags": stats.to_dict(),
        "quality_gate_mode": quality_gate_mode(config),
        "quality_review_note": (
            "Quality checks ran in warning mode. These flags guide downstream eval and V2 improvements; "
            "they do not block V1 dataset creation."
        ),
        "deterministic_outputs": [
            "coverage plan",
            "row IDs",
            "source record selection",
            "parser-owned source metadata",
            "schema validation",
            "final row ordering",
        ],
        "non_deterministic_outputs": []
        if config.template_only
        else [
            "LLM-generated user wording",
            "LLM-generated assistant wording",
            "LLM-generated known facts",
            "LLM-generated missing facts",
            "LLM-generated clarifying questions",
            "LLM-generated conditional outcomes",
            "LLM-generated ambiguity explanations",
            "LLM-selected support roles and quotes",
        ],
        "template_only": config.template_only,
        "quality_gate": config.quality_gate,
        "output_paths": {
            "dataset": workspace_path(dataset_path),
            "run_manifest": workspace_path(path),
            "generation_summary": workspace_path(summary_path),
        },
    }
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_summary(
    path: Path,
    rows: list[dict[str, Any]],
    retry_count: int,
    stats: GenerationStats,
    elapsed_seconds: float,
    config: RunConfig,
) -> None:
    category_counts = Counter(row["category"] for row in rows)
    service_counts = Counter(row["coverage_metadata"]["service_area"] for row in rows)
    tag_counts = Counter(tag for row in rows for tag in row["coverage_metadata"]["topic_tags"])
    section_counts = Counter(section for row in rows for section in row["coverage_metadata"]["source_section_ids"])
    lines = [
        "# Generation Summary",
        "",
        f"- Total rows generated: {len(rows)}",
        f"- Validation retries: {retry_count}",
        f"- Categories: {dict(sorted(category_counts.items()))}",
        f"- Elapsed seconds: {elapsed_seconds:.2f}",
        f"- Average seconds per row: {(elapsed_seconds / len(rows)):.2f}",
        f"- Quality gate mode: `{quality_gate_mode(config)}`",
        "",
        "## Quality Review Flags",
        "",
        "Quality checks ran in warning mode. These flags guide downstream eval and V2 improvements; they do not block V1 dataset creation.",
        "",
    ]
    lines.extend(f"- `{name}`: {count}" for name, count in stats.to_dict().items())
    lines.extend([
        "",
        "## Coverage By Service Area",
        "",
    ])
    lines.extend(f"- `{name}`: {count}" for name, count in sorted(service_counts.items()))
    lines.extend(["", "## Coverage By Topic Tag", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in sorted(tag_counts.items()))
    lines.extend(["", "## Key Source Sections", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in section_counts.most_common())
    lines.extend(
        [
            "",
            "## Validation",
            "",
            "All exported rows passed inline row validation and final full-dataset validation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_rows(
    index: dict[str, Any],
    records: dict[str, dict[str, Any]],
    config: RunConfig,
    client: OpenAIClient | None,
) -> tuple[list[dict[str, Any]], int, GenerationStats, float]:
    started_at = time.perf_counter()
    stats = GenerationStats()
    plan = build_coverage_plan()
    plan, plan_flags, replacement_count = review_coverage_plan(plan, records)
    stats.source_plan_review_flags += len(plan_flags)
    stats.source_plan_replacements += replacement_count
    validate_coverage_plan(plan, records)
    log(f"Coverage plan built with {len(plan)} rows.")
    if plan_flags:
        log(f"Source-plan review flagged {len(plan_flags)} weak planned source(s); applied {replacement_count} deterministic replacement(s).")
    accepted: dict[int, dict[str, Any]] = {}
    retry_count = 0
    total = len(plan)
    worker_count = 1 if config.template_only else max(1, config.workers)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                generate_one,
                position=position,
                total=total,
                item=item,
                index=index,
                records=records,
                config=config,
                client=client,
            )
            for position, item in enumerate(plan, start=1)
        ]
        for future in as_completed(futures):
            position, row, retries, row_stats = future.result()
            accepted[position] = row
            retry_count += retries
            stats.merge(row_stats)
            counts = Counter(row["category"] for row in accepted.values())
            log(f"Accepted {len(accepted)}/{total}; category_counts={dict(sorted(counts.items()))}")

    rows = [accepted[position] for position in sorted(accepted)]

    failures = validate_dataset(rows, records)
    if failures:
        raise RuntimeError("Final dataset validation failed:\n" + json.dumps(failures, indent=2))
    elapsed = time.perf_counter() - started_at
    log(f"Final validation passed for {len(rows)} rows in {elapsed:.2f}s.")
    return rows, retry_count, stats, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clause-index", type=Path, default=CLAUSE_INDEX_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--model", default=None, help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.5.")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS_PER_ROW)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--template-only", action="store_true", help="Use deterministic local templates instead of OpenAI.")
    parser.add_argument("--quality-gate", action="store_true", help="Run generation-quality lints in warn-only mode.")
    args = parser.parse_args()

    load_environment()
    model = args.model or os.environ.get("OPENAI_MODEL") or DEFAULT_GENERATOR_MODEL
    temperature = args.temperature if args.temperature is not None else float(os.environ.get("OPENAI_TEMPERATURE", DEFAULT_GENERATOR_TEMPERATURE))
    config = RunConfig(
        model=model,
        temperature=temperature,
        prompt_version=PROMPT_VERSION,
        max_attempts=args.max_attempts,
        template_only=args.template_only,
        workers=args.workers,
        quality_gate=args.quality_gate,
    )
    api_key = os.environ.get("OPENAI_API_KEY")
    if not config.template_only and not api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --template-only is set.")
    client = None if config.template_only else OpenAIClient(api_key=api_key or "", model=config.model, temperature=config.temperature)

    index, records = load_clause_index(args.clause_index)
    rows, retry_count, stats, elapsed_seconds = generate_rows(index, records, config, client)

    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    dataset_path = output_dir / "razorpay_synthetic_qa.jsonl"
    run_manifest_path = output_dir / "run_manifest.json"
    generation_summary_path = output_dir / "generation_summary.md"
    output_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(dataset_path, rows)
    write_manifest(
        run_manifest_path,
        index,
        rows,
        retry_count,
        config,
        stats,
        elapsed_seconds,
        dataset_path=dataset_path,
        summary_path=generation_summary_path,
    )
    write_summary(generation_summary_path, rows, retry_count, stats, elapsed_seconds, config)
    print(f"Wrote {workspace_path(dataset_path)}")
    print(f"Wrote {workspace_path(run_manifest_path)}")
    print(f"Wrote {workspace_path(generation_summary_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
