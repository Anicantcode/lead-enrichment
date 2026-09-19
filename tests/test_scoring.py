from lead_enrichment.config import ScoringWeights, TierThresholds
from lead_enrichment.schemas import JevScores, LeadRecord, Tier
from lead_enrichment.scoring import composite_from_scores, avg_confidence
from lead_enrichment.tiering import decide_tier, enrich_one


def _good_scores(**over):
    base = dict(
        seniority=3.5, icp_fit=3.8, intent=3.2, engagement=3.0, data_quality=3.5,
        seniority_conf=0.9, icp_fit_conf=0.92, intent_conf=0.88, engagement_conf=0.85, data_quality_conf=0.9,
        persona="economic_buyer", persona_conf=0.9, persona_probs={},
        industry_fit="high_fit", industry_fit_conf=0.9, industry_fit_probs={},
        company_stage="mid_market", company_stage_conf=0.85,
        valid_business_email=0.92, is_spam_fake=0.05, has_buying_intent=0.82, gdpr_risk=0.1,
    )
    base.update(over)
    return JevScores(**base)


def test_composite_weights():
    w = ScoringWeights(seniority=0.25, icp_fit=0.30, intent=0.20, engagement=0.15, data_quality=0.10)
    s = _good_scores()
    comp, breakdown = composite_from_scores(s, w)
    assert 0 <= comp <= 1
    assert breakdown["_composite"] == round(comp, 4)


def test_composite_spam_penalty():
    w = ScoringWeights()
    s_clean = _good_scores(is_spam_fake=0.05)
    s_spam = _good_scores(is_spam_fake=0.9)
    c_clean, _ = composite_from_scores(s_clean, w)
    c_spam, _ = composite_from_scores(s_spam, w)
    assert c_spam < c_clean * 0.6


def test_tier_platinum():
    rec = LeadRecord(row_id=1, full_name="Alice", email="a@co.io", company="Co")
    scores = _good_scores()
    w = ScoringWeights()
    thr = TierThresholds(platinum=0.82, gold=0.68, silver=0.5, bronze=0.3, confidence_floor=0.5)
    enriched = enrich_one(rec, scores, w, thr)
    assert enriched.tier in (Tier.S_PLATINUM, Tier.A_GOLD)
    assert enriched.composite > 0.6


def test_tier_reject_on_spam():
    rec = LeadRecord(row_id=1, full_name="Test", email="test@test.com")
    scores = _good_scores(is_spam_fake=0.9)
    w = ScoringWeights()
    thr = TierThresholds()
    enriched = enrich_one(rec, scores, w, thr)
    assert enriched.tier == Tier.D_REJECT
    assert "spam_risk" in enriched.flags


def test_low_confidence_goes_to_review():
    rec = LeadRecord(row_id=1, full_name="Someone")
    scores = _good_scores(seniority_conf=0.3, icp_fit_conf=0.3, intent_conf=0.3, engagement_conf=0.3, data_quality_conf=0.3,
                          persona_conf=0.3, industry_fit_conf=0.3, company_stage_conf=0.3)
    w = ScoringWeights()
    thr = TierThresholds(confidence_floor=0.62)
    enriched = enrich_one(rec, scores, w, thr)
    assert enriched.tier == Tier.REVIEW
    assert "low_confidence" in enriched.flags
