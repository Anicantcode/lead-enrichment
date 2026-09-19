"""Orchestrator — ingest → normalize → Jev fan-out → score → tier → export."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import List, Optional

import pandas as pd
from tqdm.asyncio import tqdm as atqdm
from tqdm import tqdm

from .config import EnrichmentConfig, Settings, load_config
from .ingest import load_file
from .normalize import deduplicate, normalize_frame
from .schemas import EnrichedLead, LeadRecord, PipelineStats, Tier
from .jev_client import JevClient
from .scoring import avg_confidence, composite_from_scores
from .tiering import enrich_one
from .export import export_all

log = logging.getLogger(__name__)


def df_to_lead_records(df: pd.DataFrame) -> List[LeadRecord]:
    """Convert normalized DataFrame → list[LeadRecord]."""
    canonical_fields = {
        "row_id": "_row_id",
        "first_name": "first_name",
        "last_name": "last_name",
        "full_name": "full_name",
        "email": "email",
        "email_domain": "email_domain",
        "phone": "phone",
        "linkedin_url": "linkedin_url",
        "company": "company",
        "company_domain": "company_domain",
        "company_size": "company_size",
        "industry": "industry",
        "website": "website",
        "title": "title",
        "seniority_raw": "seniority_raw",
        "department": "department",
        "city": "city",
        "state": "state",
        "country": "country",
        "source": "source",
        "notes": "notes",
        "description": "description",
        "created_at": "created_at",
    }
    records: List[LeadRecord] = []
    for _, row in df.iterrows():
        # build kwargs for known fields
        kw = {}
        for field, col in canonical_fields.items():
            if col in df.columns:
                val = row.get(col)
                if pd.isna(val) or val == "None":
                    val = None
                # for row_id, map from _row_id
                if field == "row_id":
                    try:
                        kw[field] = int(val) if val is not None else 0
                    except Exception:
                        kw[field] = 0
                else:
                    kw[field] = str(val).strip() if val is not None and str(val).strip() else None
            else:
                if field == "row_id":
                    kw[field] = 0
                else:
                    kw[field] = None

        # raw: everything not canonical
        known_cols = set(canonical_fields.values())
        raw = {}
        for c in df.columns:
            if c not in known_cols and c != "_dedupe_reason":
                v = row.get(c)
                if pd.notna(v) and str(v).strip() not in ("", "None"):
                    raw[str(c)] = v
        kw["raw"] = raw
        # ensure row_id exists
        if kw.get("row_id", 0) == 0 and "_row_id" in row:
            try:
                kw["row_id"] = int(row["_row_id"])
            except Exception:
                kw["row_id"] = len(records) + 1
        records.append(LeadRecord(**kw))
    return records


class EnrichmentPipeline:
    """High-level pipeline — use as context manager or call run()."""

    def __init__(self, config: Optional[EnrichmentConfig] = None, settings: Optional[Settings] = None):
        self.config = config or load_config()
        self.settings = settings or Settings()
        self.stats = PipelineStats()

    async def enrich_records(self, records: List[LeadRecord]) -> List[EnrichedLead]:
        """Jev fan-out for a list of LeadRecords (async, batched, with progress)."""
        if not records:
            return []

        states = [r.state_for_jev() for r in records]

        enriched: List[EnrichedLead] = []
        batch_size = self.config.runtime.batch_size

        async with JevClient(self.config, self.settings) as jev:
            self.stats.mock_mode = jev.is_mock
            # process in batches to bound memory + show progress
            batches = [states[i : i + batch_size] for i in range(0, len(states), batch_size)]
            # map batches back to records
            for b_idx, batch_states in enumerate(atqdm(batches, desc="Enriching with Jev", unit="batch")):
                batch_records = records[b_idx * batch_size : b_idx * batch_size + len(batch_states)]
                scores_list = await jev.score_batch(batch_states)
                for rec, scores in zip(batch_records, scores_list):
                    enriched.append(enrich_one(rec, scores, self.config.weights, self.config.thresholds))
        return enriched

    def run_sync(
        self,
        input_path: Path,
        output_dir: Path,
        output_format: str = "csv",
        sheet: Optional[str | int] = None,
        dedup: bool = True,
    ) -> tuple[List[EnrichedLead], PipelineStats, Path]:
        """Synchronous entry point — handles ingest → normalize → async enrich → export."""
        return asyncio.run(self.run(input_path, output_dir, output_format, sheet, dedup))

    async def run(
        self,
        input_path: Path,
        output_dir: Path,
        output_format: str = "csv",
        sheet: Optional[str | int] = None,
        dedup: bool = True,
    ) -> tuple[List[EnrichedLead], PipelineStats, Path]:
        t0 = time.monotonic()
        input_path = Path(input_path)
        output_dir = Path(output_dir)

        # 1. ingest
        df_raw = load_file(input_path, sheet=sheet)
        self.stats.total = len(df_raw)

        # 2. normalize
        df_norm = normalize_frame(df_raw)
        log.info(f"Normalized: {len(df_norm)} rows after cleaning")

        # 3. dedup
        dupes = pd.DataFrame()
        if dedup:
            df_norm, dupes = deduplicate(df_norm)
            if len(dupes):
                log.info(f"Dedup removed {len(dupes)} duplicates (kept {len(df_norm)})")

        # 4. to records
        records = df_to_lead_records(df_norm)
        log.info(f"Converted to {len(records)} LeadRecords — sample: {records[0].display_name() if records else 'none'}")

        # 5. enrich via Jev (or mock)
        enriched = await self.enrich_records(records)

        # 6. sort by composite desc (so S is top within each tier)
        enriched.sort(key=lambda e: (-e.composite, e.tier.priority, e.lead.row_id))

        # 7. stats
        self.stats.by_tier = {}
        self.stats.by_flag = {}
        for e in enriched:
            self.stats.by_tier[e.tier.value] = self.stats.by_tier.get(e.tier.value, 0) + 1
            for f in e.flags:
                self.stats.by_flag[f] = self.stats.by_flag.get(f, 0) + 1
        if enriched:
            self.stats.avg_composite = round(sum(e.composite for e in enriched) / len(enriched), 4)
            self.stats.avg_confidence = round(sum(e.confidence_avg for e in enriched) / len(enriched), 4)
        self.stats.elapsed_seconds = round(time.monotonic() - t0, 2)

        # 8. export
        out_path = export_all(
            enriched=enriched,
            dupes=dupes,
            raw_df=df_raw,
            output_dir=output_dir,
            output_format=output_format,
            config=self.config,
            stats=self.stats,
            input_path=input_path,
        )
        return enriched, self.stats, out_path
