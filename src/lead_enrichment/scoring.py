"""Composite scoring — turn atomic Jev answers into a single ranked number.

Pattern: Composite scoring (docs.typesafe.ai/patterns/composite-scoring)
- Normalize each Score (0..4 → 0..1)
- Weight with ICP-aware weights you control in code
- Noul & Choice modifiers adjust the composite without polluting the atomic scores

Weights live in config.yaml so you can retune without touching prompts.
"""

from __future__ import annotations

from .config import ScoringWeights, TierThresholds
from .schemas import JevScores


def composite_from_scores(scores: JevScores, weights: ScoringWeights) -> tuple[float, dict[str, float]]:
    """Return (composite 0..1, breakdown dict)."""
    w = weights.normalized()

    # normalize 0..4 → 0..1
    n_sen = scores.seniority / 4.0
    n_icp = scores.icp_fit / 4.0
    n_int = scores.intent / 4.0
    n_eng = scores.engagement / 4.0
    n_dq = scores.data_quality / 4.0

    breakdown = {
        "seniority": n_sen,
        "icp_fit": n_icp,
        "intent": n_int,
        "engagement": n_eng,
        "data_quality": n_dq,
    }

    base = (
        w["seniority"] * n_sen
        + w["icp_fit"] * n_icp
        + w["intent"] * n_int
        + w["engagement"] * n_eng
        + w["data_quality"] * n_dq
    )

    # ── Modifiers — multiplicative, bounded ─────────────────────────────────
    # valid business email is a big deal: no business email → cap at 0.6
    # spam → heavy penalty
    # industry no_fit → penalty
    # persona unrelated → penalty

    modifier = 1.0

    # business email absent: apply soft cap
    if scores.valid_business_email < 0.3:
        modifier *= 0.72  # -28%
    elif scores.valid_business_email < 0.55:
        modifier *= 0.88  # -12%

    # spam: strong down-weight
    if scores.is_spam_fake >= 0.70:
        modifier *= 0.35
    elif scores.is_spam_fake >= 0.45:
        modifier *= 0.70

    # persona unrelated: down
    if scores.persona == "unrelated":
        modifier *= 0.55
    elif scores.persona == "blocker":
        modifier *= 0.80

    # industry no_fit
    if scores.industry_fit == "no_fit":
        modifier *= 0.60
    elif scores.industry_fit == "low_fit":
        modifier *= 0.85

    # unknown company stage with otherwise high score: slight discount
    if scores.company_stage == "unknown" and base > 0.7:
        modifier *= 0.92

    composite = max(0.0, min(1.0, base * modifier))

    # stash breakdown for debugging
    breakdown["_base"] = round(base, 4)
    breakdown["_modifier"] = round(modifier, 4)
    breakdown["_composite"] = round(composite, 4)

    return composite, breakdown


def avg_confidence(scores: JevScores) -> float:
    """Average confidence across scored dimensions (probabilistic answers only)."""
    vals = [
        scores.seniority_conf,
        scores.icp_fit_conf,
        scores.intent_conf,
        scores.engagement_conf,
        scores.data_quality_conf,
        scores.persona_conf,
        scores.industry_fit_conf,
        scores.company_stage_conf,
    ]
    # filter zeros (missing)
    vals = [v for v in vals if v > 0]
    if not vals:
        return 0.6
    return sum(vals) / len(vals)


def confidence_for_routing(scores: JevScores) -> float:
    """Confidence used for gating — lower if any key dimension is uncertain.

    We take the mean but also penalize if any critical score has low confidence
    or if spam/business-email nouls are near 0.5 (model uncertain).
    """
    avg = avg_confidence(scores)
    # if any core score is <0.55 confident, pull avg down
    low = min(
        scores.seniority_conf or 1,
        scores.icp_fit_conf or 1,
        scores.icp_fit_conf or 1,
        scores.data_quality_conf or 1,
    )
    if low < 0.55:
        avg = (avg + low) / 2
    # noul uncertainty penalty: distance from 0.5 → certainty
    def noul_cert(n: float) -> float:
        return abs(n - 0.5) * 2  # 0 (uncertain) .. 1 (certain)

    cert_spam = noul_cert(scores.is_spam_fake)
    cert_email = noul_cert(scores.valid_business_email)
    if min(cert_spam, cert_email) < 0.3:
        avg *= 0.92
    return max(0.0, min(1.0, avg))
