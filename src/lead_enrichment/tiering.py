"""Tiering & confidence-gated routing.

Implements the two TypeSafe patterns together:
- Composite scoring → ranked composite (0..1)
- Confidence-gated routing → decide when to auto-tier vs. human review

Tiers are deliberately code-driven so you can change thresholds in config.yaml
without touching prompts.
"""

from __future__ import annotations

from typing import List

from .config import ScoringWeights, TierThresholds
from .schemas import Tier
from .scoring import avg_confidence, composite_from_scores, confidence_for_routing
from .schemas import EnrichedLead, JevScores, LeadRecord
from datetime import datetime


def decide_tier(
    lead: LeadRecord,
    scores: JevScores,
    composite: float,
    conf_routing: float,
    thresholds: TierThresholds,
) -> tuple[Tier, str, List[str]]:
    """Return (tier, reason, flags). Pure function — easy to test."""

    flags: List[str] = []
    reasons: List[str] = []

    # hard disqualifiers first (before confidence)
    if scores.is_spam_fake >= thresholds.spam_threshold:
        flags.append("spam_risk")
        return Tier.D_REJECT, f"Flagged as spam/fake (noul={scores.is_spam_fake:.2f} ≥ {thresholds.spam_threshold})", flags

    if scores.persona == "unrelated":
        flags.append("persona_unrelated")
        # not always reject — but cap to bronze/reject unless strong counter-signals
        if composite < 0.45:
            return Tier.D_REJECT, "Persona is unrelated (student/intern/recruiter) and composite is low", flags

    # GDPR sensitive → flag but don't auto-reject; route to review if high risk
    if scores.gdpr_risk >= thresholds.gdpr_review_threshold:
        flags.append("gdpr_review")

    # invalid email → flag
    if scores.valid_business_email < (1 - thresholds.invalid_email_threshold):
        # e.g., threshold 0.70 means valid <0.30 triggers flag
        if scores.valid_business_email < 0.30:
            flags.append("invalid_email")

    # confidence gating — must come after hard D checks, before tier buckets
    if conf_routing < thresholds.confidence_floor:
        flags.append("low_confidence")
        # unless it's clearly D already, send to review
        # keep original composite signal in reason
        return (
            Tier.REVIEW,
            f"Low routing confidence ({conf_routing:.2f} < {thresholds.confidence_floor}) — needs human review (composite {composite:.2f})",
            flags,
        )

    # if gdpr flag present and composite would be S/A, downgrade to review
    if "gdpr_review" in flags and composite >= thresholds.gold:
        flags.append("low_confidence")  # treat as review-like
        return Tier.REVIEW, f"GDPR-sensitive lead with high score ({composite:.2f}) — routed to review for legal check", flags

    # tier buckets by composite
    if composite >= thresholds.platinum:
        tier = Tier.S_PLATINUM
        reasons.append(f"Composite {composite:.2f} ≥ platinum {thresholds.platinum}")
    elif composite >= thresholds.gold:
        tier = Tier.A_GOLD
        reasons.append(f"Composite {composite:.2f} ≥ gold {thresholds.gold}")
    elif composite >= thresholds.silver:
        tier = Tier.B_SILVER
        reasons.append(f"Composite {composite:.2f} ≥ silver {thresholds.silver}")
    elif composite >= thresholds.bronze:
        tier = Tier.C_BRONZE
        reasons.append(f"Composite {composite:.2f} ≥ bronze {thresholds.bronze}")
    else:
        tier = Tier.D_REJECT
        reasons.append(f"Composite {composite:.2f} < bronze {thresholds.bronze} — disqualified on fit/intent")

    # enrich reason with persona & industry signal
    reasons.append(f"persona={scores.persona} ({scores.persona_conf:.2f}), industry_fit={scores.industry_fit}")
    if "invalid_email" in flags:
        reasons.append("no valid business email")

    reason = " · ".join(reasons)
    return tier, reason, flags


def enrich_one(
    lead: LeadRecord,
    scores: JevScores,
    weights: ScoringWeights,
    thresholds: TierThresholds,
) -> EnrichedLead:
    composite, _breakdown = composite_from_scores(scores, weights)
    conf_avg = avg_confidence(scores)
    conf_route = confidence_for_routing(scores)
    tier, reason, flags = decide_tier(lead, scores, composite, conf_route, thresholds)

    # if low data quality but otherwise high, add flag
    if scores.data_quality < 1.2:
        if "low_confidence" not in flags:
            flags.append("sparse_data")

    return EnrichedLead(
        lead=lead,
        scores=scores,
        composite=round(composite, 4),
        confidence_avg=round(conf_avg, 4),
        tier=tier,
        reason=reason,
        flags=flags,
        enriched_at=datetime.utcnow(),
    )
