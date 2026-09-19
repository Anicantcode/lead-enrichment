"""Pydantic schemas for leads, Jev answers, and tiering."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Tier(str, Enum):
    S_PLATINUM = "S_platinum"  # top 5-10% — call now
    A_GOLD = "A_gold"          # high quality
    B_SILVER = "B_silver"      # solid / nurture active
    C_BRONZE = "C_bronze"      # low fit — long nurture
    D_REJECT = "D_reject"      # spam / invalid / no fit
    REVIEW = "REVIEW"          # low confidence → human review

    @property
    def label(self) -> str:
        return {
            "S_platinum": "S — Platinum (Priority)",
            "A_gold": "A — Gold (High Quality)",
            "B_silver": "B — Silver (Medium)",
            "C_bronze": "C — Bronze (Low / Nurture)",
            "D_reject": "D — Reject / Disqualified",
            "REVIEW": "⚠ Review Queue (Low Confidence)",
        }[self.value]

    @property
    def priority(self) -> int:
        order = {
            "S_platinum": 0,
            "A_gold": 1,
            "B_silver": 2,
            "C_bronze": 3,
            "REVIEW": 4,
            "D_reject": 5,
        }
        return order[self.value]


class LeadRecord(BaseModel):
    """Normalized lead as seen by the pipeline. All fields optional to tolerate messy inputs."""

    row_id: int = Field(description="Original row number (1-indexed, header excluded)")
    # identity
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    email_domain: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    # company
    company: Optional[str] = None
    company_domain: Optional[str] = None
    company_size: Optional[str] = None  # raw string, e.g. "11-50", "1000+"
    industry: Optional[str] = None
    website: Optional[str] = None
    # role
    title: Optional[str] = None
    seniority_raw: Optional[str] = None
    department: Optional[str] = None
    # geography
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    # enrichment / source
    source: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None
    # raw catch-all — preserves original columns that didn't map
    raw: dict[str, Any] = Field(default_factory=dict)

    def display_name(self) -> str:
        return self.full_name or f"{self.first_name or ''} {self.last_name or ''}".strip() or self.email or f"row {self.row_id}"

    def state_for_jev(self) -> dict[str, Any]:
        """Build the JSON state object sent to Jev.

        Best practice: state is a structured JSON object, not a flat string.
        Each field gets a descriptive key so Jev can reference it.
        """
        # compact, skip None, keep it readable for the model
        d: dict[str, Any] = {
            "lead": {
                "name": self.display_name(),
                "email": self.email,
                "email_domain": self.email_domain,
                "phone": self.phone,
                "linkedin_url": self.linkedin_url,
                "title": self.title,
                "company": self.company,
                "company_domain": self.company_domain or self.website,
                "company_size": self.company_size,
                "industry": self.industry,
                "department": self.department,
                "location": ", ".join(filter(None, [self.city, self.state, self.country])) or None,
                "source": self.source,
                "notes": self.notes or self.description,
                "created_at": self.created_at,
            },
            "context": {
                "row_id": self.row_id,
                "has_email": bool(self.email),
                "has_linkedin": bool(self.linkedin_url),
                "has_company": bool(self.company),
            },
        }
        # prune None for cleaner state
        lead = {k: v for k, v in d["lead"].items() if v not in (None, "", [])}
        d["lead"] = lead
        return d


class JevScores(BaseModel):
    """All atomic Jev answers for one lead, plus derived composites."""

    # Score primitives (0..4 scale)
    seniority: float = Field(ge=0, le=4, description="Seniority / decision authority")
    icp_fit: float = Field(ge=0, le=4, description="ICP / company fit")
    intent: float = Field(ge=0, le=4, description="Buying intent signal")
    engagement: float = Field(ge=0, le=4, description="Readiness to engage")
    data_quality: float = Field(ge=0, le=4, description="Data completeness & trust")

    seniority_conf: float = 0.0
    icp_fit_conf: float = 0.0
    intent_conf: float = 0.0
    engagement_conf: float = 0.0
    data_quality_conf: float = 0.0

    # Choice primitives
    persona: str
    persona_conf: float = 0.0
    persona_probs: dict[str, float] = Field(default_factory=dict)

    industry_fit: str
    industry_fit_conf: float = 0.0
    industry_fit_probs: dict[str, float] = Field(default_factory=dict)

    company_stage: str
    company_stage_conf: float = 0.0

    # Noul primitives (0..1)
    valid_business_email: float
    is_spam_fake: float
    has_buying_intent: float
    gdpr_risk: float


class EnrichedLead(BaseModel):
    """Lead + Jev answers + composite + tier."""

    lead: LeadRecord
    scores: JevScores
    # normalized 0..1 composites
    composite: float = Field(ge=0, le=1)
    confidence_avg: float = Field(ge=0, le=1)
    tier: Tier
    reason: str = Field(description="Human-readable why this tier")
    flags: list[str] = Field(default_factory=list, description="e.g. low_confidence, spam_risk, gdpr_review")
    enriched_at: datetime = Field(default_factory=datetime.utcnow)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten for CSV/Excel export."""
        base = {
            "row_id": self.lead.row_id,
            "tier": self.tier.value,
            "tier_label": self.tier.label,
            "composite_score": round(self.composite, 4),
            "confidence_avg": round(self.confidence_avg, 4),
            "reason": self.reason,
            "flags": "; ".join(self.flags),
            # identity
            "full_name": self.lead.full_name or self.lead.display_name(),
            "first_name": self.lead.first_name,
            "last_name": self.lead.last_name,
            "email": self.lead.email,
            "email_domain": self.lead.email_domain,
            "phone": self.lead.phone,
            "linkedin_url": self.lead.linkedin_url,
            "company": self.lead.company,
            "company_domain": self.lead.company_domain,
            "company_size": self.lead.company_size,
            "industry": self.lead.industry,
            "title": self.lead.title,
            "department": self.lead.department,
            "city": self.lead.city,
            "state": self.lead.state,
            "country": self.lead.country,
            "source": self.lead.source,
            "notes": self.lead.notes or self.lead.description,
            # scores
            "score_seniority": round(self.scores.seniority, 3),
            "score_icp_fit": round(self.scores.icp_fit, 3),
            "score_intent": round(self.scores.intent, 3),
            "score_engagement": round(self.scores.engagement, 3),
            "score_data_quality": round(self.scores.data_quality, 3),
            "persona": self.scores.persona,
            "persona_conf": round(self.scores.persona_conf, 3),
            "industry_fit": self.scores.industry_fit,
            "industry_fit_conf": round(self.scores.industry_fit_conf, 3),
            "company_stage": self.scores.company_stage,
            "company_stage_conf": round(self.scores.company_stage_conf, 3),
            "noul_valid_email": round(self.scores.valid_business_email, 3),
            "noul_is_spam": round(self.scores.is_spam_fake, 3),
            "noul_buying_intent": round(self.scores.has_buying_intent, 3),
            "noul_gdpr_risk": round(self.scores.gdpr_risk, 3),
        }
        # append any unmapped raw columns with prefix raw_
        for k, v in self.lead.raw.items():
            if k not in base:
                base[f"raw_{k}"] = v
        return base


class PipelineStats(BaseModel):
    total: int = 0
    by_tier: dict[str, int] = Field(default_factory=dict)
    by_flag: dict[str, int] = Field(default_factory=dict)
    avg_composite: float = 0.0
    avg_confidence: float = 0.0
    elapsed_seconds: float = 0.0
    mock_mode: bool = False
