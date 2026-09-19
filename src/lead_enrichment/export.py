"""Export — tiered files + master + manifest + report.

Produces (depending on format):
  - leads_tier_S_platinum.{csv|xlsx}
  - leads_tier_A_gold.{csv|xlsx}
  - leads_tier_B_silver.{csv|xlsx}
  - leads_tier_C_bronze.{csv|xlsx}
  - leads_tier_D_reject.{csv|xlsx}
  - leads_REVIEW_queue.{csv|xlsx}
  - enriched_master.{csv|xlsx}  (all enriched leads)
  - duplicates_removed.csv
  - manifest.json
  - summary.md + summary.html
  - workbook.xlsx (all tiers as sheets, if xlsx requested)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd

from .config import EnrichmentConfig
from .schemas import EnrichedLead, PipelineStats, Tier

log = logging.getLogger(__name__)

TIER_ORDER = [
    Tier.S_PLATINUM,
    Tier.A_GOLD,
    Tier.B_SILVER,
    Tier.C_BRONZE,
    Tier.REVIEW,
    Tier.D_REJECT,
]

TIER_FILESTEM = {
    Tier.S_PLATINUM: "leads_tier_S_platinum",
    Tier.A_GOLD: "leads_tier_A_gold",
    Tier.B_SILVER: "leads_tier_B_silver",
    Tier.C_BRONZE: "leads_tier_C_bronze",
    Tier.REVIEW: "leads_REVIEW_queue",
    Tier.D_REJECT: "leads_tier_D_reject",
}

TIER_DESCRIPTIONS = {
    Tier.S_PLATINUM: "Top 5-15% — perfect ICP + high intent + verified contact. Call within 24h.",
    Tier.A_GOLD: "High quality — strong ICP fit, senior, reachable. Prioritized outreach.",
    Tier.B_SILVER: "Solid — plausible fit, moderate intent. Nurture + personalized sequence.",
    Tier.C_BRONZE: "Low fit — long nurture or enrichment needed. Don't cold-call yet.",
    Tier.REVIEW: "Low Jev confidence or GDPR-sensitive — human review before outreach.",
    Tier.D_REJECT: "Disqualified — spam/fake, no fit, or unrecoverable data. Suppress.",
}


def _write_df(df: pd.DataFrame, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "xlsx":
        # xlsx via openpyxl
        df.to_excel(path, index=False, engine="openpyxl")
    else:
        df.to_csv(path, index=False, encoding="utf-8-sig")


def _enriched_to_dfs(enriched: List[EnrichedLead]) -> dict[Tier, pd.DataFrame]:
    by_tier: dict[Tier, list[dict]] = {t: [] for t in TIER_ORDER}
    for e in enriched:
        by_tier[e.tier].append(e.to_flat_dict())
    return {t: pd.DataFrame(rows) for t, rows in by_tier.items()}


REPORT_MD_TEMPLATE = """# Lead Enrichment Report

**Input:** `{input_path}`  
**Generated:** {now}  
**Model:** `{model}` · **Mode:** {mode}  
**Elapsed:** {elapsed}s · **Avg composite:** {avg_composite} · **Avg confidence:** {avg_conf}

## Tier Summary

| Tier | Count | % | Action |
|------|------:|---|--------|
{rows}

## Flag Breakdown

| Flag | Count |
|------|------:|
{flag_rows}

## Thresholds & Weights

- **Thresholds:** platinum ≥{platinum}, gold ≥{gold}, silver ≥{silver}, bronze ≥{bronze}, confidence floor {conf_floor}, spam ≥{spam_thr}
- **Weights:** seniority {w_sen:.2f}, icp_fit {w_icp:.2f}, intent {w_int:.2f}, engagement {w_eng:.2f}, data_quality {w_dq:.2f}

## Files

{files}

## ICP Profile

> **{icp_name}** — {icp_desc}  
> Target industries: {icp_ind}  
> Target titles: {icp_titles}  
> Target sizes: {icp_sizes}

---

*Pipeline: `lead-enrichment` v1.0 · Jev (TypeSafe System One) · Composite scoring + Confidence-gated routing*
"""

REPORT_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Lead Enrichment — {now}</title>
<style>
  body{{font-family:Inter,system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.6}}
  h1{{font-size:28px;margin-bottom:4px}} h2{{margin-top:36px;border-bottom:2px solid #eee;padding-bottom:6px}}
  table{{border-collapse:collapse;width:100%;margin:12px 0}} th,td{{border:1px solid #e5e7eb;padding:8px 10px;text-align:left}}
  th{{background:#f9fafb;font-weight:600}} tr:nth-child(even){{background:#fafafa}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff}}
  .S{{background:#7c3aed}} .A{{background:#059669}} .B{{background:#2563eb}} .C{{background:#d97706}} .REVIEW{{background:#dc2626}} .D{{background:#6b7280}}
  .meta{{color:#6b7280;font-size:13px}} code{{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px}}
  .file-list code{{display:block;margin:4px 0}}
</style></head><body>
<h1>Lead Enrichment Report</h1>
<p class="meta">Input: <code>{input_path}</code> · Generated: {now} · Model: <code>{model}</code> · Mode: {mode} · Elapsed: {elapsed}s</p>
<h2>Tier Summary</h2>
<table><tr><th>Tier</th><th>Count</th><th>%</th><th>Action</th></tr>
{html_rows}
</table>
<h2>Flag Breakdown</h2>
<table><tr><th>Flag</th><th>Count</th></tr>
{html_flag_rows}
</table>
<h2>Files</h2>
<div class="file-list">{html_files}</div>
<h2>ICP</h2>
<p><strong>{icp_name}</strong> — {icp_desc}<br>
<span class="meta">Industries: {icp_ind} · Titles: {icp_titles} · Sizes: {icp_sizes}</span></p>
<hr><p class="meta">Pipeline: lead-enrichment v1.0 · Jev · Composite scoring + Confidence routing</p>
</body></html>
"""


def export_all(
    enriched: List[EnrichedLead],
    dupes: pd.DataFrame,
    raw_df: pd.DataFrame,
    output_dir: Path,
    output_format: str,
    config: EnrichmentConfig,
    stats: PipelineStats,
    input_path: Path,
) -> Path:
    fmt = "xlsx" if output_format.lower() in ("xlsx", "excel", "xls") else "csv"
    ext = "xlsx" if fmt == "xlsx" else "csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    dfs = _enriched_to_dfs(enriched)
    written: List[Path] = []

    # 1. per-tier files (skip empty if no rows? we still write empty with header for consistency if you want — here we skip empty)
    for tier in TIER_ORDER:
        df = dfs[tier]
        if df.empty:
            log.info(f"Tier {tier.value}: 0 leads — skipping file")
            continue
        # sort already by composite desc
        stem = TIER_FILESTEM[tier]
        path = output_dir / f"{stem}.{ext}"
        _write_df(df, path, fmt)
        written.append(path)
        log.info(f"Wrote {tier.label}: {len(df)} → {path.name}")

    # 2. master
    if enriched:
        master_rows = [e.to_flat_dict() for e in enriched]
        master_df = pd.DataFrame(master_rows)
        # order columns: meta first
        cols_priority = ["tier", "composite_score", "confidence_avg", "full_name", "email", "company", "title", "industry", "reason", "flags"]
        other_cols = [c for c in master_df.columns if c not in cols_priority]
        master_df = master_df[cols_priority + other_cols]
        master_path = output_dir / f"enriched_master.{ext}"
        _write_df(master_df, master_path, fmt)
        written.append(master_path)

        # workbook with all tiers as sheets (if xlsx)
        if fmt == "xlsx":
            wb_path = output_dir / "workbook_all_tiers.xlsx"
            with pd.ExcelWriter(wb_path, engine="openpyxl") as w:
                for tier in TIER_ORDER:
                    df = dfs[tier]
                    if df.empty:
                        continue
                    # sheet name max 31 chars
                    sname = tier.value[:31]
                    df.to_excel(w, sheet_name=sname, index=False)
                master_df.to_excel(w, sheet_name="MASTER", index=False)
                if not dupes.empty:
                    dupes.to_excel(w, sheet_name="DUPLICATES", index=False)
            written.append(wb_path)
    else:
        master_df = pd.DataFrame()

    # 3. dupes
    if not dupes.empty:
        dupe_path = output_dir / "duplicates_removed.csv"
        dupes.to_csv(dupe_path, index=False, encoding="utf-8-sig")
        written.append(dupe_path)

    # 4. manifest.json
    manifest = {
        "input": str(input_path),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": config.runtime.model,
        "mode": "mock" if stats.mock_mode else "live",
        "elapsed_seconds": stats.elapsed_seconds,
        "stats": {
            "total_input_rows": stats.total,
            "enriched": len(enriched),
            "duplicates_removed": len(dupes),
            "by_tier": stats.by_tier,
            "by_flag": stats.by_flag,
            "avg_composite": stats.avg_composite,
            "avg_confidence": stats.avg_confidence,
        },
        "thresholds": config.thresholds.model_dump(),
        "weights": config.weights.model_dump(),
        "icp": config.icp.model_dump(),
        "files": [str(p.relative_to(output_dir)) for p in written],
        "tier_descriptions": {t.value: TIER_DESCRIPTIONS[t] for t in TIER_ORDER},
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(manifest_path)

    # 5. summary.md + summary.html
    total = len(enriched) or 1
    rows_md = ""
    rows_html = ""
    for tier in TIER_ORDER:
        count = stats.by_tier.get(tier.value, 0)
        pct = (count / total * 100) if enriched else 0
        rows_md += f"| {tier.label} | {count} | {pct:.1f}% | {TIER_DESCRIPTIONS[tier]} |\n"
        rows_html += f"<tr><td><span class='badge {tier.value.split('_')[0]}'>{tier.value}</span> {tier.label}</td><td>{count}</td><td>{pct:.1f}%</td><td>{TIER_DESCRIPTIONS[tier]}</td></tr>\n"

    flag_rows_md = "\n".join(f"| {k} | {v} |" for k, v in sorted(stats.by_flag.items(), key=lambda x: -x[1])) or "| — | 0 |"
    flag_rows_html = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(stats.by_flag.items(), key=lambda x: -x[1])) or "<tr><td>—</td><td>0</td></tr>"

    files_md = "\n".join(f"- `{p.name}`" for p in written)
    files_html = "\n".join(f"<code>{p.name}</code>" for p in written)

    w = config.weights.normalized()
    report_md = REPORT_MD_TEMPLATE.format(
        input_path=input_path,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        model=config.runtime.model,
        mode="MOCK (heuristics)" if stats.mock_mode else "LIVE (Jev)",
        elapsed=stats.elapsed_seconds,
        avg_composite=stats.avg_composite,
        avg_conf=stats.avg_confidence,
        rows=rows_md,
        flag_rows=flag_rows_md,
        platinum=config.thresholds.platinum,
        gold=config.thresholds.gold,
        silver=config.thresholds.silver,
        bronze=config.thresholds.bronze,
        conf_floor=config.thresholds.confidence_floor,
        spam_thr=config.thresholds.spam_threshold,
        w_sen=w["seniority"],
        w_icp=w["icp_fit"],
        w_int=w["intent"],
        w_eng=w["engagement"],
        w_dq=w["data_quality"],
        files=files_md,
        icp_name=config.icp.name,
        icp_desc=config.icp.description,
        icp_ind=", ".join(config.icp.target_industries),
        icp_titles=", ".join(config.icp.target_titles),
        icp_sizes=", ".join(config.icp.target_company_sizes),
    )
    (output_dir / "summary.md").write_text(report_md, encoding="utf-8")
    written.append(output_dir / "summary.md")

    report_html = REPORT_HTML_TEMPLATE.format(
        input_path=input_path,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        model=config.runtime.model,
        mode="MOCK" if stats.mock_mode else "LIVE",
        elapsed=stats.elapsed_seconds,
        html_rows=rows_html,
        html_flag_rows=flag_rows_html,
        html_files=files_html,
        icp_name=config.icp.name,
        icp_desc=config.icp.description,
        icp_ind=", ".join(config.icp.target_industries),
        icp_titles=", ".join(config.icp.target_titles),
        icp_sizes=", ".join(config.icp.target_company_sizes),
    )
    (output_dir / "summary.html").write_text(report_html, encoding="utf-8")
    written.append(output_dir / "summary.html")

    log.info(f"Export complete → {output_dir} ({len(written)} files)")
    return output_dir
