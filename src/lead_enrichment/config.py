"""Configuration — ICP profile, weights, thresholds, runtime tunables.

Follows 12-factor: env vars > config file > defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class IdealCustomerProfile(BaseModel):
    """Describe your ICP so Jev + scoring can be tuned without code changes."""

    name: str = Field(default="Default B2B SaaS ICP", description="Label for this profile")
    description: str = Field(
        default="B2B SaaS companies, 11-1000 employees, revenue $1M-$100M, buying modern tooling. Decision makers in RevOps, Growth, Sales."
    )
    target_industries: list[str] = Field(default_factory=lambda: ["SaaS", "Software", "Fintech", "Marketing Tech", "B2B Services"])
    target_titles: list[str] = Field(default_factory=lambda: ["Head of Sales", "VP Sales", "RevOps", "Growth Lead", "Founder", "CEO", "CMO"])
    target_company_sizes: list[str] = Field(default_factory=lambda: ["11-50", "51-200", "201-500", "501-1000"])
    target_geos: list[str] = Field(default_factory=lambda: ["US", "CA", "GB", "DE", "FR", "AU"])
    anti_icp_keywords: list[str] = Field(default_factory=lambda: ["student", "intern", "recruiter", "freelancer"])
    deal_notes: str = Field(default="We sell a lead-enrichment & outreach platform. High intent = actively evaluating lead gen, CRM migration, data enrichment.")


class ScoringWeights(BaseModel):
    """Weights for composite scoring. Must sum to 1.0 (auto-normalized if not)."""

    seniority: float = 0.25
    icp_fit: float = 0.30
    intent: float = 0.20
    engagement: float = 0.15
    data_quality: float = 0.10

    def normalized(self) -> dict[str, float]:
        total = self.seniority + self.icp_fit + self.intent + self.engagement + self.data_quality
        if total == 0:
            return {k: 0.2 for k in ["seniority", "icp_fit", "intent", "engagement", "data_quality"]}
        return {
            "seniority": self.seniority / total,
            "icp_fit": self.icp_fit / total,
            "intent": self.intent / total,
            "engagement": self.engagement / total,
            "data_quality": self.data_quality / total,
        }


class TierThresholds(BaseModel):
    """Composite thresholds (0..1). Confidence floor triggers REVIEW queue."""

    platinum: float = 0.82  # S
    gold: float = 0.68      # A
    silver: float = 0.50    # B
    bronze: float = 0.30    # C — below this is D reject
    confidence_floor: float = 0.62  # below → REVIEW regardless of score
    spam_threshold: float = 0.75    # noul is_spam_fake >= this → D reject
    invalid_email_threshold: float = 0.70  # 1 - valid_email >= this → flag
    gdpr_review_threshold: float = 0.75


class RuntimeConfig(BaseModel):
    concurrency: int = 20
    batch_size: int = 50
    rate_limit_rps: Optional[float] = 10.0
    max_retries: int = 3
    timeout_s: float = 12.0
    mock_if_no_key: bool = True  # fall back to deterministic heuristics if no API key
    model: str = "jev-latest"


class EnrichmentConfig(BaseModel):
    icp: IdealCustomerProfile = Field(default_factory=IdealCustomerProfile)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    thresholds: TierThresholds = Field(default_factory=TierThresholds)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    def to_yaml(self, path: Path) -> None:
        path.write_text(yaml.safe_dump(self.model_dump(), sort_keys=False), encoding="utf-8")

    @classmethod
    def from_yaml(cls, path: Path) -> "EnrichmentConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data or {})


class Settings(BaseSettings):
    """Env-driven settings (12-factor)."""

    typesafe_api_key: Optional[str] = Field(default=None, alias="TYPESAFE_API_KEY")
    typesafe_base_url: str = Field(default="https://api.typesafe.ai", alias="TYPESAFE_BASE_URL")
    typesafe_default_model: str = Field(default="jev-latest", alias="TYPESAFE_DEFAULT_MODEL")
    typesafe_log_level: Optional[str] = Field(default=None, alias="TYPESAFE_LOG_LEVEL")

    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}


def load_config(path: Optional[Path] = None) -> EnrichmentConfig:
    """Load config from YAML if given, else defaults. Env vars override runtime tuning."""
    if path and path.exists():
        cfg = EnrichmentConfig.from_yaml(path)
    else:
        cfg = EnrichmentConfig()

    # env overrides for runtime
    if os.getenv("LEAD_ENRICH_CONCURRENCY"):
        cfg.runtime.concurrency = int(os.getenv("LEAD_ENRICH_CONCURRENCY", cfg.runtime.concurrency))
    if os.getenv("LEAD_ENRICH_BATCH_SIZE"):
        cfg.runtime.batch_size = int(os.getenv("LEAD_ENRICH_BATCH_SIZE", cfg.runtime.batch_size))
    if os.getenv("LEAD_ENRICH_RATE_LIMIT_RPS"):
        try:
            cfg.runtime.rate_limit_rps = float(os.getenv("LEAD_ENRICH_RATE_LIMIT_RPS"))
        except ValueError:
            pass
    if os.getenv("TYPESAFE_DEFAULT_MODEL"):
        cfg.runtime.model = os.getenv("TYPESAFE_DEFAULT_MODEL")
    return cfg
