# Architecture — Jev at the Core

This project is a reference for **how to build with TypeSafe Jev** (System One). It deliberately avoids LLM-style “prompt → text → parse” and instead uses **typed, atomic decisions** that code composes.

## 1. State vs Questions

```
┌─────────────────────────────────────────────────────────────────┐
│  State (JSON) — the material you want judged                    │
│  {                                                              │
│    \"lead\": {\"name\":\"Sarah Chen\", \"email\":\"sarah@acme.io\",   │
│              \"title\":\"VP Sales\", \"company\":\"Acme SaaS\", …},│
│    \"context\": {\"has_email\":true}                              │
│  }                                                              │
│                    │                                            │
│                    ▼                                            │
│  Questions (14) — each one snap judgment, independent            │
│    • seniority  (Score 0..4)   “How senior given title?”        │
│    • icp_fit    (Score 0..4)   “How well does company match ICP?”│
│    • intent     (Score 0..4)   “Buying-intent signal?”          │
│    • persona    (Choice)       “economic_buyer / champion / …”  │
│    • is_spam_fake (Noul 0..1)  “Is this fake?”                  │
│    … 9 more, all in ONE system_one call                         │
│                    │                                            │
│                    ▼                                            │
│  Answers — typed, calibrated, with confidence                    │
│    {\"seniority\":{\"score\":3.5,\"confidence\":0.88}, …}          │
└─────────────────────────────────────────────────────────────────┘
```

Key points from [State](https://docs.typesafe.ai/concepts/state) and [Primitives](https://docs.typesafe.ai/primitives):

- **State is a JSON object**, not a flat prompt string. Each field has a descriptive key so Jev can be told to “look at `lead.title`”.
- **Instructions reference state paths with backticks** (e.g., “`lead.title` and `lead.notes`”) — the model knows which part to judge.
- All 14 questions see **the same state**, evaluated **in parallel & isolation**. Adding questions barely changes latency — so we fan out widely instead of trying to pack judgment into one prompt.

## 2. Why Atomic Questions?

> “System One models work best when each question asks one specific, well-scoped thing … If the judgment you want depends on several independent factors, ask about each factor separately and combine the results with logic in your code.” — [Atomic questions](https://docs.typesafe.ai/primitives#ask-for-one-snap-judgment-per-question)

Bad: `Rate this lead 0–100 for sales readiness (consider title, company, intent, data quality)`.

Good: 5 separate `Score` questions + 4 `Choice` + 5 `Noul`, then:

```python
composite = 0.30*icp_fit + 0.25*seniority + 0.20*intent + ...
composite *= spam_modifier * email_modifier   # all in code
```

Now retuning is `weights.seniority = 0.30` in `icp_config.yaml`, not “rewrite the prompt and hope”.

## 3. Composite Scoring + Confidence Routing

Two patterns composed:

### Composite scoring ([docs](https://docs.typesafe.ai/patterns/composite-scoring))

- Each `Score` is normalized `0..4 → 0..1`
- Weighted sum → `base` composite
- Multiplicative modifiers for `Noul`/`Choice` signals (spam, email, persona) — bounded, auditable
- Breakdown dict is kept for debugging (`_base`, `_modifier`, `_composite`)

### Confidence-gated routing ([docs](https://docs.typesafe.ai/patterns/confidence-routing))

```python
conf_route = confidence_for_routing(scores)  # avg of score confidences + noul certainty
if is_spam_fake >= 0.75:            → D_REJECT (hard, before confidence)
if conf_route < 0.62:               → REVIEW   (human, even if composite high)
if gdpr_risk >= 0.75 and gold+:      → REVIEW   (legal)
elif composite >= 0.82:             → S_PLATINUM
elif composite >= 0.68:             → A_GOLD
...
```

- `confidence` is **not** `probability`. It’s how peaked the distribution is. We expose both.
- Low-confidence leads never auto-route to S/A — they go to `REVIEW` queue regardless of score.
- Thresholds live in `config.yaml`, not in prompts.

## 4. End-to-End Pipeline

```
ingest.py  →  normalize.py  →  pipeline.py  →  jev_client.py  →  scoring.py  →  tiering.py  →  export.py
   │               │                │                │                │               │              │
 CSV/TSV        alias map      df → records    AsyncTypeSafe    composite      decide_tier    tiered CSV/XLSX
 XLSX/Parquet   email/phone    state_for_jev   + rate limit     + confidence  + flags       + master + manifest
 JSON           dedup          enrich_records  + mock fallback   + modifiers   + reason      + reports
 sniffing       + clean        sort by composite
```

- **Ingest**: auto-sniffs delimiter/encoding, picks Excel sheet with most rows if `--sheet` omitted, handles JSONL.
- **Normalize**: `COLUMN_ALIASES` maps ~40 variant names → canonical fields; `email-validator` + `phonenumbers` normalize; `raw_*` preserves unknown columns.
- **Jev client**: `AsyncTypeSafeClient` with `Semaphore(concurrency)` + token-bucket throttle (`rate_limit_rps`), exponential backoff on 429/502/503, per-lead mock fallback so one failure doesn’t abort a 100k job.
- **Mock mode**: deterministic, hash-seeded heuristics so CI/demo work without a key. Produces calibrated spread across tiers (S 10%, D 5%, REVIEW 5% on the sample).

## 5. Scaling to Large Files

- `batch_size` (default 50) × `concurrency` (20) = up to 1000 in-flight `system_one` calls. Each call is **one lead, 14 questions** — Jev evaluates questions in parallel inside a single call (no need to fan out across calls for one lead).
- `rate_limit_rps` (10) enforces a global throttle via `asyncio.Lock` + `monotonic()` interval.
- For 100k rows: `100k / 50 = 2000 batches` streamed via `tqdm`. Memory is O(batch_size), not O(total).
- `TYPESAFE_LOG_LEVEL=info` + `mock_if_no_key` lets you test the full pipeline offline before spending API quota.

## 6. What “high → low quality” Means Here

| Tier | Composite | Confidence | Typical Profile |
|------|-----------|------------|-----------------|
| S Platinum | ≥0.82 | ≥0.62 | VP/C-level + perfect ICP (SaaS 51-500) + inbound/high intent + verified business email + recent |
| A Gold | 0.68–0.82 | ≥0.62 | Strong ICP, senior or champion, warm source, maybe one gap |
| B Silver | 0.50–0.68 | ≥0.62 | Plausible fit, moderate intent, nurture with personalization |
| C Bronze | 0.30–0.50 | ≥0.62 | Low fit or sparse data — long nurture, needs enrichment |
| REVIEW | any | <0.62 or GDPR | Model uncertain or privacy-sensitive → human check before outreach |
| D Reject | <0.30 or spam≥0.75 | any | Fake/spam, no-fit, student/intern, undeliverable |

All tiering is a **pure function** `decide_tier(lead, scores, composite, conf) → Tier` — unit-testable, no hidden state.
