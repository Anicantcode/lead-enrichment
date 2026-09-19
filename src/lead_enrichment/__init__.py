"""Lead Enrichment — Jev-powered tiering pipeline.

Public API:
    from lead_enrichment import EnrichmentPipeline, load_config
    from lead_enrichment.schemas import LeadRecord, Tier
"""

from .config import EnrichmentConfig, TierThresholds, load_config
from .pipeline import EnrichmentPipeline
from .schemas import Tier

__all__ = ["EnrichmentConfig", "TierThresholds", "EnrichmentPipeline", "Tier", "load_config"]
__version__ = "1.0.0"
