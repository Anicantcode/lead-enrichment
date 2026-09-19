# Jev Questions — Reference

All 14 questions are defined in `src/lead_enrichment/questions.py` via `build_questions(icp)`. The ICP profile is interpolated into `instructions` so the model knows what “fit” means without hardcoding.

## Scores (ordered rubric, 0..4)

Scores return `score` (float, can be 2.7 between levels), `probabilities` per level, `confidence`, `legend`. See [Score primitive](https://docs.typesafe.ai/primitives/score).

| ID | Instructions (abridged) | Levels (0..4) |
|---|---|---|
| `seniority` | Based on `lead.title` + `lead.department`, how senior/decision-authoritative? + ICP hint | 0 No authority (student/intern) · 1 IC no budget · 2 Manager/some influence · 3 Director/Head owns budget · 4 VP/C-level/Founder can sign |
| `icp_fit` | Considering `lead.company`/`size`/`industry`/`location` vs ICP | 0 No fit (B2C/student) · 1 Weak (misses 2+ dims) · 2 Moderate (one key dim off) · 3 Strong (minor gap) · 4 Perfect (textbook ICP) |
| `intent` | From `lead.notes`/`title`/`source`/`company`, buying-intent for enrichment/outreach platform | 0 No signal · 1 Latent pain · 2 Curious (content/webinar) · 3 Active (demo, comparing, pain stated) · 4 High (budget/timeline/champion reaching out) |
| `engagement` | Given `lead.email`/`phone`/`linkedin`/`source`/`created_at`, readiness to engage now | 0 Unreachable (no contact, stale >12mo) · 1 Hard (single channel, cold) · 2 Reachable (one verified, moderately recent) · 3 Engageable (warm/recent, multiple channels) · 4 Hot (recent inbound <30d + verified email+phone/linkedin) |
| `data_quality` | Across all `lead` fields (`name`,`email`,`company`,`title`,`industry`,`location`), completeness & trust | 0 Junk · 1 Sparse (2+ critical missing) · 2 Partial · 3 Solid · 4 Pristine (all key fields, verifiable) |

## Choices (nominal, no order)

Choices return `choice` (selected key), `probabilities` dict, `confidence`. See [Choice primitive](https://docs.typesafe.ai/primitives/choice).

| ID | Instructions | Options |
|---|---|---|
| `persona` | Which buying persona from `lead.title`/`department`? | `economic_buyer` (C-level/VP/Founder) · `champion` (Head/Director Sales/RevOps/Growth) · `influencer` (Senior IC/Manager) · `end_user` (SDR/BDR/AE) · `blocker` (IT/Legal/Procurement gatekeeper) · `unrelated` (student/intern/recruiter) |
| `industry_fit` | How well does `lead.industry` + `lead.company` align with target industries (+ ICP hint) | `high_fit` (SaaS/Software/Fintech/MarTech/B2B Services) · `medium_fit` (adjacent B2B) · `low_fit` (B2B unlikely: mfg/logistics) · `no_fit` (B2C/consumer/edu/gov) · `unknown` |
| `company_stage` | From `lead.company_size` (+ `lead.company`), stage proxy | `startup` (1-10) · `smb` (11-50) · `mid_market` (51-500) · `enterprise` (500+) · `unknown` |
| `lead_source_quality` | Warmth/trust of `lead.source` | `inbound` (demo/request/referral) · `outbound_warm` (event/webinar/content) · `outbound_cold` (scraped/prospected) · `list_purchase` (bought/rented bulk) · `unknown` |

## Nouls (yes/no probability 0..1)

Nouls return `noul` (P(yes)). Near 1 = strong yes, near 0 = strong no, 0.5 = uncertain. No separate confidence — the `noul` distance from 0.5 is the certainty. See [Noul primitive](https://docs.typesafe.ai/primitives/noul).

| ID | Instructions | Criteria (`true` / `false`) |
|---|---|---|
| `valid_business_email` | Is `lead.email` a valid business email (not disposable/personal Gmail/Yahoo, proper format)? | true: real business email on company domain / false: disposable/personal/malformed/missing |
| `is_spam_fake` | Does record look like spam/fake/test (`test@test.com`, `asdf`, `Fake Corp`)? | true: likely spam/fake/test → disqualify / false: real person/company |
| `has_buying_intent` | Do `lead.notes`/`title`/`source` contain explicit intent (“evaluating”, “need enrichment”, “CRM migration”, “outbound scale”)? | true: explicit intent/pain for enrichment/outreach / false: no signal |
| `gdpr_risk` | Elevated GDPR/privacy risk (EU resident, sensitive notes, opt-out, HIPAA/health)? | true: needs legal review / false: standard B2B |
| `recently_active` | Recent (<90d) vs stale (>12mo) via `lead.created_at` / recency cues in `notes`? | true: recent/active / false: stale/no recency info |

## Design Notes

- **One question, one judgment.** Never “rate this lead overall”. Each question is a gut-check a domain expert could make in 2 seconds given the right context.
- **Instructions are complete sentences.** IDs are only for code; the model never sees them. Every `instructions` string names the exact `lead.*` field(s) to look at with backticks — see [Reference specific fields](https://docs.typesafe.ai/primitives#reference-specific-fields).
- **Criteria are the model’s options.** Changing wording there changes the rubric. Changing weights/thresholds in `config.yaml` changes prioritization — two independent axes.
- **All 14 in one call.** Example payload shape (see [Quickstart](https://docs.typesafe.ai/introduction/quickstart#request-body)):

```json
{
  "state": { "lead": {"name": "Sarah Chen", "title": "VP Sales", ...}, "context": {...} },
  "model": "jev-latest",
  "questions": {
    "seniority": {"type":"score","instructions":"Based on `lead.title`...","criteria":["No authority...","IC...","Manager...","Director...","VP..."]},
    "persona": {"type":"choice","instructions":"Which persona...","criteria":{"economic_buyer":"C-level...","champion":"Head/Director..."}},
    "is_spam_fake": {"type":"noul","instructions":"Does this record look like spam...","criteria":{"true":"Likely spam...","false":"Real person..."}}
  }
}
```

- **Confidence**: Scores/Choices return `confidence` (peakedness). Nouls don’t — use `abs(noul - 0.5)*2`. The pipeline’s `confidence_for_routing()` averages score confidences and penalizes if any core dimension is <0.55 or any Noul is near 0.5 (uncertain).
