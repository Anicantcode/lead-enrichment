# Lead Enrichment — Jev-Powered Tiered Pipeline

> **One big file in → six tiered files out (Platinum → Reject)** — with calibrated Jev scores, confidence-gated routing, and full audit trail.

Built on **TypeSafe Jev** — the first [System One model](https://docs.typesafe.ai/concepts/system-one) — using the three [AI primitives](https://docs.typesafe.ai/introduction#typesafe-primitives) (`Choice`, `Score`, `Noul`) as atomic, composable decisions. No text generation, no parsing — just typed values your code can branch on, sort, and route.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────────┐     ┌──────────────┐
│  One large  │────▶│  Normalize   │────▶│  Jev fan-out (14 q's)   │────▶│  Composite   │
│ CSV / XLSX  │     │  + Dedup     │     │  All questions in ONE   │     │  scoring +   │
│ Parquet/JSON│     │  + Clean     │     │  call, parallel/isolated│     │  tiering     │
└─────────────┘     └──────────────┘     └─────────────────────────┘     └──────┬───────┘
                                                                              │
                                   ┌──────────────────────────────────────────┼──────────────────────┐
                                   ▼                                          ▼                      ▼
                           S Platinum  A Gold  B Silver            C Bronze    REVIEW queue   D Reject
                           (call now)  (high)  (nurture)           (long)      (low conf)     (suppress)
```

---

## Why Jev (and not an LLM)?

| LLM approach | Jev System One approach (this project) |
|---|---|
| Prompt: “rate this lead 0-100 and explain” → parse text, hope it’s stable | **14 atomic questions**, each one snap judgment a human makes in 2s |
| One big prompt → context rot, prompt drift when you retune | Questions evaluated **in parallel & isolation** — adding questions barely changes latency |
| Scores are prose, thresholds are prompt hacks | Returns **typed values + calibrated probabilities + confidence** per question |
| Retuning = rewrite prompt | Retuning = change a **weight/threshold in `config.yaml`** |

This project follows the [TypeSafe patterns](https://docs.typesafe.ai/patterns) exactly:

- **Composite scoring** — 5 `Score` dimensions → weighted composite 0..1 in code
- **Confidence-gated routing** — `confidence < 0.62` → human `REVIEW` queue, even if composite is high
- **Speculative fan-out** — all 14 questions in a **single** `system_one` call per lead (Choice/Score/Noul mixed)
- **Atomic questions** — never “rate this lead”; instead: seniority? ICP fit? intent? engagement? data quality? … each isolated

---

## Quick Start (2 minutes)

```bash
# 1. Install
pip install -e .            # or: uv pip install -e .
# or
pip install typesafe-sdk pandas openpyxl pyarrow pydantic typer rich pyyaml python-dotenv tqdm email-validator phonenumbers jinja2

# 2. (optional) Set Jev API key for live scoring — otherwise runs in deterministic mock mode
export TYPESAFE_API_KEY=tsk_...   # get at https://console.typesafe.ai/keys
# Or: cp .env.example .env && edit

# 3. Run on the bundled sample (no key needed)
lead-enrich demo -o ./out --format csv
lead-enrich enrich examples/sample_leads.csv -o ./out --format xlsx

# 4. Inspect outputs
open out/summary.html
cat out/manifest.json
ls out/
```

**Without a key** the pipeline runs in **mock mode** (deterministic heuristics keyed on the lead’s content) so CI, demos, and offline dev still work. **With a key** every lead is scored by `jev-latest` — no code change.

---

## What You Get

For a 20-row sample above, typical output with live Jev:

```
out/
├── leads_tier_S_platinum.csv    # 2–3 leads — call within 24h
├── leads_tier_A_gold.csv        # 4–5 leads — prioritized outreach
├── leads_tier_B_silver.csv      # 5–6 leads — nurture + personalization
├── leads_tier_C_bronze.csv      # 3–4 leads — long nurture
├── leads_REVIEW_queue.csv       # 1–2 leads — low confidence or GDPR → human check
├── leads_tier_D_reject.csv      # 2–3 leads — spam/fake/no-fit (suppressed)
├── enriched_master.csv          # all leads, with every Jev answer + composite + tier
├── duplicates_removed.csv       # deduped rows (email or name+company)
├── workbook_all_tiers.xlsx      # same, but all tiers as sheets (when --format xlsx)
├── manifest.json                # full audit: thresholds, weights, ICP, stats, file list
├── summary.md                   # markdown report (for PRs / Slack)
└── summary.html                 # pretty HTML report (open in browser)
```

Each tier file and `enriched_master` contains **flattened columns**:

| Group | Columns |
|---|---|
| Meta | `tier`, `tier_label`, `composite_score`, `confidence_avg`, `reason`, `flags` |
| Identity | `full_name`, `email`, `email_domain`, `phone`, `linkedin_url`, `company`, `company_size`, `industry`, `title` … |
| Jev Scores (0..4) | `score_seniority`, `score_icp_fit`, `score_intent`, `score_engagement`, `score_data_quality` (+ confidences) |
| Jev Choices | `persona` (economic_buyer/champion/…), `industry_fit`, `company_stage` (+ probs) |
| Jev Nouls (0..1) | `noul_valid_email`, `noul_is_spam`, `noul_buying_intent`, `noul_gdpr_risk` |
| Raw | any unmapped input columns preserved as `raw_*` |

---

## CLI Reference

```bash
# Enrich (main)
lead-enrich enrich INPUT -o ./out -f csv|xlsx [-c icp_config.yaml] [--sheet Sheet1] [--concurrency 30] [--no-dedup] [-v]

# Preview without calling Jev
lead-enrich preview leads.csv --rows 10
lead-enrich preview data.xlsx --sheet "Leads 2024"

# Scaffold a config
lead-enrich init-config ./my_icp.yaml --force

# Demo (uses examples/sample_leads.csv, mock if no key)
lead-enrich demo -o ./out_demo -f xlsx

# Also works as module
python -m lead_enrichment.cli enrich leads.csv -o ./out --format xlsx
```

### Supported inputs

| Format | Extension | Notes |
|---|---|---|
| CSV / TSV | `.csv` `.tsv` `.txt` | Auto-sniffs delimiter (`, ; \t \|`) + encoding (`utf-8-sig`, `utf-8`, `latin-1`) |
| Excel | `.xlsx` `.xls` | Auto-picks sheet with most rows if `--sheet` omitted; uses `openpyxl` |
| Parquet | `.parquet` `.pq` | Via `pyarrow` |
| JSON | `.json` `.jsonl` | Array, `{leads:[...]}`, or line-delimited JSON |

Column names are **fuzzy-matched** (`email` ← `Email Address`, `work email`, `e-mail`; `company` ← `Organization`, `account`; `title` ← `job title`, `role`; etc.) — see [`normalize.py`](src/lead_enrichment/normalize.py) for the full alias map. Unknown columns are preserved as `raw_*`.

---

## The 14 Jev Questions

All defined in [`questions.py`](src/lead_enrichment/questions.py) and sent in **one** `system_one` call per lead:

**Scores (0..4 with ordered criteria):**

| ID | Instructions (abridged) |
|---|---|
| `seniority` | How senior / decision-authoritative based on `lead.title` + `department`? |
| `icp_fit` | How well does `lead.company`/`size`/`industry`/`location` match ICP? |
| `intent` | How strong is buying-intent signal in `lead.notes`/`title`/`source`? |
| `engagement` | How ready to engage given `email`/`phone`/`linkedin`/`source`/`created_at`? |
| `data_quality` | How complete/consistent is the record across all `lead` fields? |

**Choices:**

| ID | Options |
|---|---|
| `persona` | `economic_buyer` · `champion` · `influencer` · `end_user` · `blocker` · `unrelated` |
| `industry_fit` | `high_fit` · `medium_fit` · `low_fit` · `no_fit` · `unknown` |
| `company_stage` | `startup` · `smb` · `mid_market` · `enterprise` · `unknown` |
| `lead_source_quality` | `inbound` · `outbound_warm` · `outbound_cold` · `list_purchase` · `unknown` |

**Nouls (0..1):**

| ID | Question |
|---|---|
| `valid_business_email` | Is `lead.email` a valid business email (not disposable/personal/malformed)? |
| `is_spam_fake` | Is this spam/fake/test data (`test@test.com`, `asdf`, `Fake Corp`)? |
| `has_buying_intent` | Does `notes`/`title`/`source` contain explicit intent (“evaluating”, “need enrichment”, “CRM migration”)? |
| `gdpr_risk` | Elevated GDPR/privacy risk (EU resident, sensitive notes, opt-out)? |
| `recently_active` | Recent / active (<90 days) vs stale (>12 months)? |

> **Jev is asked about `lead.*` via structured state** — never a flat string. Example state:
> ```json
> { "lead": {"name":"Sarah Chen","email":"sarah.chen@acmesaas.io","title":"VP Sales","company":"Acme SaaS","industry":"SaaS","notes":"Evaluating enrichment tools…"}, "context": {"has_email":true} }
> ```

---

## Composite & Tiering Logic

In [`scoring.py`](src/lead_enrichment/scoring.py) + [`tiering.py`](src/lead_enrichment/tiering.py) — **all in code, not in prompts**:

```python
# 1. Normalize 0..4 → 0..1, weight, then apply modifiers
base = 0.30*icp_fit + 0.25*seniority + 0.20*intent + 0.15*engagement + 0.10*data_quality
composite = base * email_modifier * spam_modifier * persona_modifier * industry_modifier
#          └─ weights from config.yaml, retune without touching prompts

# 2. Confidence-gated routing (before tier buckets)
if confidence < 0.62:            → REVIEW (human)
if is_spam_fake ≥ 0.75:           → D_REJECT (hard)
if gdpr_risk ≥ 0.75 and score≥gold: → REVIEW (legal)
# 3. Tier by composite
if composite ≥ 0.82: S_platinum
elif composite ≥ 0.68: A_gold
elif composite ≥ 0.50: B_silver
elif composite ≥ 0.30: C_bronze
else:                D_reject
```

Full logic is a pure function (`decide_tier`) — easy to unit test and audit.

---

## Configuration — `icp_config.yaml`

```yaml
icp:
  name: "B2B SaaS — RevOps / Growth ICP"
  target_industries: [SaaS, Software, Fintech, Marketing Tech]
  target_titles: [Head of Sales, VP Sales, RevOps, Founder]
  ...

weights:          # auto-normalized; change without touching prompts
  seniority: 0.25
  icp_fit: 0.30
  intent: 0.20
  engagement: 0.15
  data_quality: 0.10

thresholds:
  platinum: 0.82
  gold: 0.68
  silver: 0.50
  bronze: 0.30
  confidence_floor: 0.62
  spam_threshold: 0.75
  ...

runtime:
  concurrency: 20
  batch_size: 50
  rate_limit_rps: 10
  model: jev-latest
  mock_if_no_key: true
```

Generate a starter file: `lead-enrich init-config ./my.yaml`

Env overrides: `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`, `TYPESAFE_DEFAULT_MODEL`, `LEAD_ENRICH_CONCURRENCY`, etc. (see [`.env.example`](.env.example)).

---

## Programmatic Use

```python
import asyncio
from pathlib import Path
from lead_enrichment import EnrichmentPipeline, load_config

# Simple: one file → tiered outputs
cfg = load_config(Path("examples/icp_config.yaml"))
pipeline = EnrichmentPipeline(config=cfg)
enriched, stats, out_dir = pipeline.run_sync(
    input_path=Path("leads.csv"),
    output_dir=Path("./out"),
    output_format="xlsx",   # or "csv"
)
print(stats.by_tier, stats.avg_composite)
for lead in enriched[:3]:
    print(lead.tier, lead.composite, lead.reason, lead.lead.display_name())

# Async: enrich already-loaded records (e.g., from a DB)
from lead_enrichment.pipeline import df_to_lead_records
from lead_enrichment.normalize import normalize_frame
from lead_enrichment.ingest import load_file
import pandas as pd

df = load_file(Path("leads.csv"))
records = df_to_lead_records(normalize_frame(df))
enriched = asyncio.run(EnrichmentPipeline(cfg).enrich_records(records))
```

---

## Large Files & Performance

- **Chunked batching**: `batch_size` (default 50) × `concurrency` (default 20) with `rate_limit_rps` throttling → saturates Jev without 429s
- **One call per lead, 14 questions per call** — Jev evaluates all questions **in parallel & isolation**, so fan-out barely adds latency ([Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out))
- **Retries**: exponential backoff on 429/502/503/timeout (`max_retries=3`)
- **Mock fallback**: if Jev fails for a single lead after retries, that lead falls back to deterministic heuristics so the whole job doesn’t abort
- Tested with `examples/sample_leads.csv` (20 rows) → `~2s` mock, `~5–10s` live (depending on concurrency/rate limits)

For **100k+ rows**: increase `--concurrency` (30–50), reduce `--rate-limit` to your plan’s quota, or run as `python -m lead_enrichment.cli enrich big.csv …` with `TYPESAFE_LOG_LEVEL=info`.

---

## Project Layout

```
lead-enrichment/
├── pyproject.toml
├── .env.example
├── examples/
│   ├── sample_leads.csv          # 20-row demo (covers S→D, spam, GDPR, dupes, missing data)
│   └── icp_config.yaml           # starter ICP / weights / thresholds
├── src/lead_enrichment/
│   ├── cli.py                    # Typer CLI (enrich/preview/init-config/demo)
│   ├── ingest.py                 # CSV/TSV/XLSX/Parquet/JSON loader + sniffing
│   ├── normalize.py              # alias mapping, email/phone/URL normalization, dedup
│   ├── questions.py              # 14 atomic Jev questions (the heart — see docs)
│   ├── jev_client.py             # Async Jev client + rate limit + mock fallback
│   ├── scoring.py                # Composite 0..1 + confidence
│   ├── tiering.py                # Confidence-gated tier buckets (pure function)
│   ├── pipeline.py               # Orchestrator (ingest→normalize→Jev→score→tier→export)
│   ├── export.py                 # Tiered CSV/XLSX, workbook, manifest, reports
│   ├── schemas.py                # LeadRecord, JevScores, EnrichedLead, Tier
│   └── config.py                 # ICP / weights / thresholds / runtime + YAML loader
└── tests/
```

---

## Developing

```bash
pip install -e ".[dev]"
ruff check src/
mypy src/
pytest -v
```

**Design docs referenced while building:**

- [Introduction — TypeSafe primitives](https://docs.typesafe.ai/introduction)
- [State — how to structure Jev state](https://docs.typesafe.ai/concepts/state)
- [Primitives — Choice / Score / Noul](https://docs.typesafe.ai/primitives)
- [Composite scoring pattern](https://docs.typesafe.ai/patterns/composite-scoring)
- [Confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing)
- [Python SDK](https://docs.typesafe.ai/sdk/python) · [Quickstart](https://docs.typesafe.ai/introduction/quickstart)

---

## License

MIT
