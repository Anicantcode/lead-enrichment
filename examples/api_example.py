"""Programmatic usage examples — no CLI, just Python.

1. One-file → tiered outputs (sync)
2. DataFrame → records → Jev → tiering (async, custom)
3. Custom ICP profile on the fly
"""

import asyncio
from pathlib import Path

import pandas as pd

from lead_enrichment import EnrichmentPipeline, load_config
from lead_enrichment.config import IdealCustomerProfile, ScoringWeights, TierThresholds, EnrichmentConfig, RuntimeConfig
from lead_enrichment.ingest import load_file
from lead_enrichment.normalize import normalize_frame
from lead_enrichment.pipeline import df_to_lead_records


def example_1_one_file_sync():
    """Simplest: one file → tiered outputs."""
    cfg = load_config(Path("examples/icp_config.yaml"))
    pipeline = EnrichmentPipeline(config=cfg)
    enriched, stats, out_dir = pipeline.run_sync(
        input_path=Path("examples/sample_leads.csv"),
        output_dir=Path("/tmp/out_api1"),
        output_format="xlsx",
    )
    print(f"Enriched {len(enriched)} leads → {out_dir}")
    print(stats.by_tier)
    for e in enriched[:3]:
        print(e.tier.value, f"{e.composite:.2f}", e.lead.display_name(), "-", e.reason[:80])


async def example_2_dataframe_async():
    """From an existing DataFrame (e.g., DB query, API)."""
    cfg = load_config()
    # tweak thresholds for this use-case
    cfg.thresholds.platinum = 0.80
    cfg.weights.intent = 0.30
    cfg.weights.icp_fit = 0.25

    df_raw = load_file(Path("examples/sample_leads.csv"))
    df_norm = normalize_frame(df_raw)
    records = df_to_lead_records(df_norm)
    print(f"Normalized {len(records)} records, sample: {records[0].state_for_jev()}")

    pipeline = EnrichmentPipeline(config=cfg)
    enriched = await pipeline.enrich_records(records)
    enriched.sort(key=lambda e: -e.composite)
    for e in enriched[:5]:
        print(f"{e.tier.value:12} {e.composite:.3f} {e.confidence_avg:.2f} {e.lead.display_name():20} {e.scores.persona:15} intent={e.scores.intent:.1f}")


def example_3_custom_icp():
    """Define ICP inline without a YAML file."""
    icp = IdealCustomerProfile(
        name="Fintech — Enterprise ICP",
        description="US fintech enterprises 500+ employees buying compliance & enrichment infra.",
        target_industries=["Fintech", "Banking", "Insurance"],
        target_titles=["CTO", "Head of Compliance", "VP Engineering", "CRO"],
        target_company_sizes=["501-1000", "1000+"],
        target_geos=["US", "GB"],
    )
    cfg = EnrichmentConfig(
        icp=icp,
        weights=ScoringWeights(seniority=0.20, icp_fit=0.35, intent=0.25, engagement=0.10, data_quality=0.10),
        thresholds=TierThresholds(platinum=0.85, gold=0.70, silver=0.50, bronze=0.30),
        runtime=RuntimeConfig(concurrency=10, batch_size=20),
    )
    pipeline = EnrichmentPipeline(config=cfg)
    enriched, stats, out_dir = pipeline.run_sync(
        Path("examples/sample_leads.csv"), Path("/tmp/out_api3"), output_format="csv"
    )
    print("Custom ICP results:", stats.by_tier)
    # show which leads matched new ICP
    df = pd.read_csv(out_dir / "enriched_master.csv")
    print(df[["full_name","company","industry","tier","composite_score"]].head(10).to_string(index=False))


if __name__ == "__main__":
    print("=== Example 1: one-file sync ===")
    example_1_one_file_sync()
    print("\n=== Example 2: dataframe async ===")
    asyncio.run(example_2_dataframe_async())
    print("\n=== Example 3: custom ICP ===")
    example_3_custom_icp()
