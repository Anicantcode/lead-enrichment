import asyncio
from pathlib import Path

import pandas as pd

from lead_enrichment.pipeline import EnrichmentPipeline
from lead_enrichment.config import load_config


def test_pipeline_sample_csv(tmp_path: Path):
    cfg = load_config()
    cfg.runtime.concurrency = 5
    cfg.runtime.batch_size = 10
    pipeline = EnrichmentPipeline(config=cfg)
    inp = Path("examples/sample_leads.csv")
    out = tmp_path / "out"
    enriched, stats, out_dir = pipeline.run_sync(inp, out, output_format="csv")
    assert len(enriched) == 20
    assert stats.total == 20
    # should have at least 4 tiers present
    assert len(stats.by_tier) >= 4
    # files exist
    assert (out_dir / "enriched_master.csv").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "summary.md").exists()
    assert (out_dir / "summary.html").exists()
    # master has all rows
    master = pd.read_csv(out_dir / "enriched_master.csv")
    assert len(master) == 20
    assert "tier" in master.columns
    assert "composite_score" in master.columns
    # at least one S or A
    assert any(master["tier"].str.contains("S_platinum|A_gold"))


def test_pipeline_xlsx(tmp_path: Path):
    cfg = load_config()
    pipeline = EnrichmentPipeline(config=cfg)
    inp = Path("examples/sample_leads.csv")
    out = tmp_path / "out_xlsx"
    enriched, stats, out_dir = pipeline.run_sync(inp, out, output_format="xlsx")
    assert (out_dir / "workbook_all_tiers.xlsx").exists()
    # check workbook sheets
    xls = pd.ExcelFile(out_dir / "workbook_all_tiers.xlsx")
    assert "MASTER" in xls.sheet_names
