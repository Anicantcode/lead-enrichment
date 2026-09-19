"""Atomic Jev questions — the heart of the enrichment.

Design principles (from docs.typesafe.ai):
- One snap judgment per question. No composite “rate this lead”.
- Decompose into orthogonal dimensions; combine in code (Composite scoring).
- Instructions are complete sentences; IDs are just for code.
- All questions see the same state; they are evaluated in parallel & isolation.
- Adding questions barely changes latency — so we go wide (14 questions).

We expose a `build_questions(icp)` factory so the ICP profile can inject
context into instructions without hardcoding prompts.
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from typesafe_sdk import Choice, Noul, Score
    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    Choice = Noul = Score = object  # type: ignore

from .config import IdealCustomerProfile


def _icp_hint(icp: IdealCustomerProfile) -> str:
    return (
        f" Ideal Customer Profile: {icp.description} "
        f"Target industries: {', '.join(icp.target_industries)}. "
        f"Target titles: {', '.join(icp.target_titles)}. "
        f"Target company sizes: {', '.join(icp.target_company_sizes)}."
    )


def build_questions(icp: IdealCustomerProfile) -> Dict[str, Any]:
    """Return dict[str, Question] ready for client.system_one(state, questions).

    If SDK is missing, returns raw dicts with type/instructions/criteria so the
    pipeline can still construct a payload or run in mock mode.
    """
    hint = _icp_hint(icp)

    def q_choice(instructions: str, criteria: Dict[str, Any]):
        if HAS_SDK:
            return Choice(instructions=instructions, criteria=criteria)
        return {"type": "choice", "instructions": instructions, "criteria": criteria}

    def q_score(instructions: str, criteria: list[str]):
        if HAS_SDK:
            return Score(instructions=instructions, criteria=criteria)
        return {"type": "score", "instructions": instructions, "criteria": criteria}

    def q_noul(instructions: str, criteria: str | None = None, true_desc: str | None = None, false_desc: str | None = None):
        kw: Dict[str, Any] = {"instructions": instructions}
        if true_desc and false_desc:
            kw["criteria"] = {"true": true_desc, "false": false_desc}
        elif criteria:
            # allow raw dict or string
            if isinstance(criteria, dict):
                kw["criteria"] = criteria
            else:
                kw["criteria"] = {"true": str(criteria), "false": "No"}
        if HAS_SDK:
            return Noul(**kw)
        return {"type": "noul", **kw}

    return {
        # ── Score dimensions (0..4, ordered levels) ──────────────────────────
        "seniority": q_score(
            instructions=(
                "Based on `lead.title` and `lead.department` in the state, "
                "how senior / decision-authoritative is this person?"
                + hint
            ),
            criteria=[
                "No authority: student, intern, junior, entry level, no title",
                "Individual contributor or specialist, no budget authority",
                "Manager / senior IC with some influence on buying",
                "Director / Head / Senior Manager — owns budget for their function",
                "VP / C-level / Founder / Owner — can sign and drives strategy",
            ],
        ),
        "icp_fit": q_score(
            instructions=(
                "Considering `lead.company`, `lead.company_size`, `lead.industry`, "
                "`lead.company_domain` and `lead.location` in the state, how well does "
                "this company match the Ideal Customer Profile?" + hint
            ),
            criteria=[
                "No fit: completely outside ICP (e.g., B2C, student org, unrelated industry or geo)",
                "Weak fit: tangentially related but misses core ICP on 2+ dimensions",
                "Moderate fit: plausible but not ideal — one key ICP dimension off",
                "Strong fit: matches most ICP dimensions, minor gap only",
                "Perfect fit: textbook ICP across industry, size, geo, and need",
            ],
        ),
        "intent": q_score(
            instructions=(
                "Based on `lead.notes`, `lead.title`, `lead.source`, and `lead.company` in the state, "
                "how strong is the buying-intent signal for a lead-enrichment / outreach platform?"
                " Look for explicit evaluation, pain points (CRM, lead gen, enrichment), or active research."
            ),
            criteria=[
                "No intent: no signal at all, passive list entry",
                "Latent pain: role suggests future need but no active signal",
                "Curious: downloaded content, attended webinar, vague interest",
                "Active: requested demo, comparing vendors, stated pain point",
                "High intent: budget/timeline mentioned, ready to buy, champion reaching out",
            ],
        ),
        "engagement": q_score(
            instructions=(
                "Given `lead.email`, `lead.phone`, `lead.linkedin_url`, `lead.source`, and `lead.created_at` in the state, "
                "how ready is this lead to be engaged right now? Consider recency, contactability, and source warmth."
            ),
            criteria=[
                "Unreachable: no contact, stale (>12 months), or bounced-source",
                "Hard to reach: single channel, cold source, old record",
                "Reachable: at least one verified channel, moderately recent",
                "Engageable: warm source or recent inbound, multiple channels",
                "Hot: recent inbound (<30 days) with verified business email + phone/LinkedIn",
            ],
        ),
        "data_quality": q_score(
            instructions=(
                "Considering all fields in `lead` — `lead.name`, `lead.email`, `lead.company`, "
                "`lead.title`, `lead.industry`, `lead.location` — how complete, consistent, and trustworthy is this record?"
            ),
            criteria=[
                "Junk: mostly empty or contradictory, likely fake",
                "Sparse: 2+ critical fields missing, many blanks",
                "Partial: core identity present but gaps remain",
                "Solid: most fields filled, consistent, minor gap",
                "Pristine: all key fields filled, consistent, verifiable business identity",
            ],
        ),
        # ── Choice dimensions ───────────────────────────────────────────────
        "persona": q_choice(
            instructions=(
                "Which buying persona best describes this lead based on `lead.title` and `lead.department`?"
                " Economic buyer can sign; Champion drives evaluation; Influencer advises; End user will use the tool; Blocker may resist."
            ),
            criteria={
                "economic_buyer": "C-level, VP, Founder, Owner — can sign budget",
                "champion": "Head/Director of Sales, RevOps, Growth, Marketing — will drive the purchase",
                "influencer": "Senior IC / Manager who influences but doesn't sign",
                "end_user": "SDR, BDR, AE, marketer — will use the product daily",
                "blocker": "IT, Legal, Procurement, Finance gatekeeper likely to slow purchase",
                "unrelated": "Student, intern, recruiter, or role unrelated to buying this product",
            },
        ),
        "industry_fit": q_choice(
            instructions=(
                "How well does `lead.industry` (and `lead.company` context) align with the target industries?"
                + hint
            ),
            criteria={
                "high_fit": "Core ICP industry (SaaS, Software, Fintech, MarTech, B2B services)",
                "medium_fit": "Adjacent B2B industry that could plausibly buy (e.g., agencies, consulting, data services)",
                "low_fit": "B2B but unlikely buyer (e.g., manufacturing, logistics, traditional enterprise)",
                "no_fit": "B2C, consumer, education, government, nonprofit, or unrelated",
                "unknown": "Industry missing or unparseable",
            },
        ),
        "company_stage": q_choice(
            instructions=(
                "Based on `lead.company_size` and `lead.company` in the state, what stage is this company? "
                "Use employee count as primary proxy; if missing, infer from context but prefer unknown."
            ),
            criteria={
                "startup": "1-10 employees, pre-seed to Seed",
                "smb": "11-50 employees",
                "mid_market": "51-500 employees",
                "enterprise": "500+ employees",
                "unknown": "Size missing or cannot be inferred",
            },
        ),
        "lead_source_quality": q_choice(
            instructions=(
                "How warm / trustworthy is `lead.source` in the state? "
                "Inbound = came to you; outbound warm = referral/partner; outbound cold = scraped list; list_purchase = bought data."
            ),
            criteria={
                "inbound": "Inbound: demo request, inbound form, referral, warm intro",
                "outbound_warm": "Outbound warm: event, webinar, content download, partner",
                "outbound_cold": "Outbound cold: scraped, prospected, cold outreach list",
                "list_purchase": "List purchase / rented / third-party bulk data",
                "unknown": "Source missing or unclear",
            },
        ),
        # ── Noul dimensions (0..1) ──────────────────────────────────────────
        "valid_business_email": q_noul(
            instructions="Is `lead.email` a valid-looking business email address (not disposable, not personal Gmail/Yahoo, has proper domain and format)?",
            criteria="valid email",
            true_desc="Appears to be a real business email on a company domain",
            false_desc="Disposable, personal, malformed, or missing email",
        ),
        "is_spam_fake": q_noul(
            instructions="Does this record look like spam, fake, test data, or a placeholder (e.g., test@test.com, asdf, John Doe at Fake Corp, all fields identical)?",
            criteria="spam",
            true_desc="Likely spam/fake/test entry that should be disqualified",
            false_desc="Appears to be a real person/company",
        ),
        "has_buying_intent": q_noul(
            instructions="Do `lead.notes` or `lead.title` or `lead.source` contain an explicit buying-intent signal (e.g., 'evaluating', 'looking for', 'need enrichment', 'CRM migration', 'outbound scale')?",
            criteria="intent",
            true_desc="Explicit intent or pain point related to lead gen/enrichment/outreach",
            false_desc="No such signal",
        ),
        "gdpr_risk": q_noul(
            instructions="Is there elevated GDPR / privacy risk in contacting this lead (e.g., EU resident with sensitive data, minor, health/political notes, or `lead.notes` suggests opted-out/Do Not Contact)?",
            criteria="gdpr",
            true_desc="Elevated privacy risk, needs legal review before outreach",
            false_desc="Standard B2B contact, no special sensitivity",
        ),
        "recently_active": q_noul(
            instructions="Is this lead recent or recently active, based on `lead.created_at` / recency cues in `lead.notes`? Consider <90 days as recent, >12 months as stale.",
            criteria="recent",
            true_desc="Recent (<90 days) or clear active signal",
            false_desc="Stale, old, or no recency info",
        ),
    }


# Keep a stable ordering for reporting
SCORE_KEYS = ["seniority", "icp_fit", "intent", "engagement", "data_quality"]
CHOICE_KEYS = ["persona", "industry_fit", "company_stage", "lead_source_quality"]
NOUL_KEYS = ["valid_business_email", "is_spam_fake", "has_buying_intent", "gdpr_risk", "recently_active"]
