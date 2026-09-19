"""Async Jev client wrapper — batching, retries, rate limiting, mock fallback.

Implements the patterns from docs.typesafe.ai:
- Speculative fan-out (one call with many questions)
- Confidence-gated routing (exposed to tiering)
- Composite scoring (scores combined downstream)

If TYPESAFE_API_KEY is missing and mock_if_no_key=True, runs a deterministic
heuristic engine so the pipeline still works for demos/CI.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional

from .config import EnrichmentConfig, Settings
from .questions import build_questions
from .schemas import JevScores
from .normalize import normalize_email_domain, normalize_phone

log = logging.getLogger(__name__)

# ── Mock heuristic engine ───────────────────────────────────────────────────

_DISPOSABLE_DOMAINS = {"mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com"}
_FREE_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"}
_SPAM_PATTERNS = re.compile(r"(test@test|asdf|qwerty|john doe|fake corp|example\.com)", re.I)
_SENIOR_TITLES = re.compile(r"\b(vp|vice president|chief|cxo|ceo|cto|cmo|coo|founder|co-founder|partner|owner|president|director|head of)\b", re.I)
_C_LEVEL = re.compile(r"\b(ceo|cto|cmo|coo|cfo|chief|founder|owner|partner|vp|vice president)\b", re.I)
_INTENT_KWS = re.compile(r"\b(evaluat|looking for|need|demo|trial|pricing|budget|timeline|enrich|outreach|lead gen|crm|migration|scale outbound|prospect)\b", re.I)


def _stable_hash(s: str) -> float:
    """Deterministic 0..1 from string."""
    h = hashlib.sha256(s.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _mock_scores_for_lead(state: dict[str, Any], icp_hint: str = "") -> JevScores:
    lead = state.get("lead", {})
    email = (lead.get("email") or "").lower()
    domain = (lead.get("email_domain") or "").lower()
    title = (lead.get("title") or "").lower()
    company = (lead.get("company") or "").lower()
    industry = (lead.get("industry") or "").lower()
    notes = (lead.get("notes") or "").lower()
    source = (lead.get("source") or "").lower()
    has_email = bool(email)
    has_linkedin = bool(lead.get("linkedin_url"))
    has_company = bool(company)
    seed = f"{email}|{title}|{company}|{industry}"

    # seniority
    if _C_LEVEL.search(title):
        seniority = 4.0 - _stable_hash(seed + "sen") * 0.5
    elif _SENIOR_TITLES.search(title):
        seniority = 3.0 + _stable_hash(seed + "sen2") * 0.7
    elif title and title.strip():
        seniority = 1.2 + _stable_hash(seed + "sen3") * 1.2
    else:
        seniority = 0.3 + _stable_hash(seed + "sen4") * 0.7

    # icp_fit — more discriminating
    icp_fit = 1.0
    if industry in ("saas", "software", "fintech", "marketing tech", "b2b services"):
        icp_fit += 1.4
    elif industry in ("saas", "software"):
        icp_fit += 0.8
    elif industry in ("healthcare", "manufacturing", "education", ""):
        icp_fit += 0.1  # low / no fit
    else:
        # adjacents get medium
        if industry:
            icp_fit += 0.6 if _stable_hash(seed+"icp2") > 0.5 else 0.2
    if has_company and industry in ("saas", "software", "fintech", "marketing tech"):
        icp_fit += 0.3
    icp_fit = min(4, max(0, icp_fit + (_stable_hash(seed + "icp") - 0.5) * 0.7))

    # intent
    if _INTENT_KWS.search(notes) or _INTENT_KWS.search(title):
        intent = 3.2 + _stable_hash(seed + "int") * 0.8
    elif "inbound" in source or "demo" in source:
        intent = 2.8 + _stable_hash(seed + "int2") * 0.8
    else:
        intent = 0.6 + _stable_hash(seed + "int3") * 1.6
    intent = min(4, max(0, intent))

    # engagement — lower base
    engagement = 0.6
    if has_email and domain not in _DISPOSABLE_DOMAINS and domain not in _FREE_DOMAINS:
        engagement += 1.0
    elif has_email:
        engagement += 0.4
    if has_linkedin:
        engagement += 0.6
    if has_company:
        engagement += 0.2
    if "recent" in notes or "inbound" in source or "demo" in source:
        engagement += 0.7
    elif "2024" in notes or "2025" in notes or "2026" in notes:
        engagement += 0.3
    engagement = min(4, engagement + (_stable_hash(seed + "eng") - 0.5) * 0.8)

    # data_quality — count real fields: name, email, company, title, industry, phone, linkedin
    filled = sum(bool(lead.get(k)) for k in ("name", "email", "company", "title", "industry", "phone", "linkedin_url"))
    # if full_name missing but first/last present, count
    if not lead.get("name") and (lead.get("first_name") or lead.get("last_name")):
        filled += 1
    data_quality = (filled / 7) * 4
    data_quality = max(0.0, min(4.0, data_quality + (_stable_hash(seed + "dq") - 0.5) * 0.6))
    data_quality = max(0.2, data_quality)
    # also clamp others to be safe
    seniority = max(0.0, min(4.0, seniority))
    icp_fit = max(0.0, min(4.0, icp_fit))
    intent = max(0.0, min(4.0, intent))
    engagement = max(0.0, min(4.0, engagement))

    # choices
    if _C_LEVEL.search(title):
        persona = "economic_buyer"
    elif re.search(r"\b(head|director)\b", title):
        persona = "champion"
    elif re.search(r"\b(manager|senior)\b", title):
        persona = "influencer"
    elif re.search(r"\b(sdr|bdr|ae|sales rep)\b", title):
        persona = "end_user"
    elif not title:
        persona = "unrelated" if _stable_hash(seed+"p") < 0.2 else "influencer"
    else:
        persona = "influencer"

    if industry in ("saas", "software"):
        industry_fit = "high_fit"
    elif industry:
        industry_fit = "medium_fit" if _stable_hash(seed+"ind") > 0.4 else "low_fit"
    else:
        industry_fit = "unknown"

    size_raw = (lead.get("company_size") or "").lower()
    if "1000" in size_raw or "enterprise" in size_raw:
        company_stage = "enterprise"
    elif "201" in size_raw or "500" in size_raw:
        company_stage = "mid_market"
    elif "11-50" in size_raw or "51-200" in size_raw or "smb" in size_raw:
        company_stage = "smb"
    elif "1-10" in size_raw or "startup" in size_raw:
        company_stage = "startup"
    else:
        company_stage = "unknown"

    # nouls
    if not has_email:
        valid_business = 0.05
    elif domain in _DISPOSABLE_DOMAINS:
        valid_business = 0.08
    elif domain in _FREE_DOMAINS:
        valid_business = 0.25
    elif _SPAM_PATTERNS.search(email):
        valid_business = 0.12
    else:
        valid_business = 0.82 + _stable_hash(seed+"vb") * 0.15

    raw_spam = _SPAM_PATTERNS.search(f"{email} {company} {title} {notes}")
    is_spam = 0.94 if raw_spam else 0.04 + _stable_hash(seed+"spam")*0.12
    # only downgrade if NOT explicit spam pattern
    if not raw_spam and filled >= 5:
        is_spam = min(is_spam, 0.12)

    has_intent_noul = 0.85 if _INTENT_KWS.search(notes) else 0.12 + _stable_hash(seed+"hi")*0.18
    gdpr = 0.12
    # EU / sensitive geography + notes
    loc = f"{lead.get('location','')} {notes} {lead.get('country','')} {lead.get('city','')}".lower()
    # also check lead dict country explicitly
    country_raw = (lead.get("country") or "" if isinstance(lead.get("country"), str) else "") .lower() if isinstance(lead.get("country"), str) else ""
    # if any EU signal, high gdpr
    if re.search(r"\b(eu|gdpr|germany|france|berlin|paris|hipaa|health|patient)\b", loc):
        gdpr = 0.78 + _stable_hash(seed+"gdpr")*0.20
    elif loc.strip().endswith(" fr") or ", fr" in loc or country_raw in ("fr", "de", "eu"):
        gdpr = 0.80
    # also if explicitly EU country in lead
    elif lead.get("location") and "FR" in str(lead.get("location")):
        gdpr = 0.78

    # confidence — lower for sparse / ambiguous records
    base_conf = 0.72 + _stable_hash(seed+"baseconf")*0.24
    # sparse data → lower confidence
    if filled <= 3:
        base_conf *= 0.75
    elif filled == 4:
        base_conf *= 0.88
    # spam/fake → model uncertain? keep moderate
    if raw_spam:
        base_conf = min(base_conf, 0.68)
    conf = lambda k: max(0.35, min(0.97, base_conf + (_stable_hash(seed+k)-0.5)*0.10))

    return JevScores(
        seniority=round(float(seniority), 3),
        icp_fit=round(float(icp_fit), 3),
        intent=round(float(intent), 3),
        engagement=round(float(engagement), 3),
        data_quality=round(float(data_quality), 3),
        seniority_conf=round(conf("sen_conf"), 3),
        icp_fit_conf=round(conf("icp_conf"), 3),
        intent_conf=round(conf("int_conf"), 3),
        engagement_conf=round(conf("eng_conf"), 3),
        data_quality_conf=round(conf("dq_conf"), 3),
        persona=persona,
        persona_conf=round(conf("per"), 3),
        persona_probs={persona: 0.78, "influencer": 0.12, "end_user": 0.05, "champion": 0.03, "unrelated": 0.02},
        industry_fit=industry_fit,
        industry_fit_conf=round(conf("ind"), 3),
        industry_fit_probs={industry_fit: 0.7, "medium_fit": 0.2, "low_fit": 0.08, "no_fit": 0.02},
        company_stage=company_stage,
        company_stage_conf=round(conf("stage"), 3),
        valid_business_email=round(float(valid_business), 3),
        is_spam_fake=round(float(is_spam), 3),
        has_buying_intent=round(float(has_intent_noul), 3),
        gdpr_risk=round(float(gdpr), 3),
    )


# ── Real client ─────────────────────────────────────────────────────────────

class JevClient:
    """Thin async wrapper with rate limiting and concurrency control."""

    def __init__(self, config: EnrichmentConfig, settings: Optional[Settings] = None):
        self.config = config
        self.settings = settings or Settings()
        self.questions = build_questions(config.icp)
        self._sem = asyncio.Semaphore(config.runtime.concurrency)
        self._rate_lock = asyncio.Lock()
        self._last_call = 0.0
        self._mock_mode = False
        self._client = None  # lazy

        # decide mock
        has_key = bool(self.settings.typesafe_api_key and self.settings.typesafe_api_key.startswith("tsk_"))
        if not has_key and config.runtime.mock_if_no_key:
            log.warning("TYPESAFE_API_KEY missing — running in MOCK mode (deterministic heuristics). Set TYPESAFE_API_KEY for live Jev calls.")
            self._mock_mode = True
        elif not has_key:
            raise RuntimeError("TYPESAFE_API_KEY missing and mock_if_no_key=False. Set TYPESAFE_API_KEY or enable mock mode.")

        self.is_mock = self._mock_mode

    async def __aenter__(self):
        if not self._mock_mode:
            try:
                from typesafe_sdk import AsyncTypeSafeClient

                self._client = AsyncTypeSafeClient(
                    # SDK reads env vars automatically; we also pass explicit if needed
                )
                # enter context
                await self._client.__aenter__()
            except Exception as e:
                log.warning(f"Failed to init TypeSafe client ({e}) — falling back to mock mode")
                self._mock_mode = True
                self.is_mock = True
        return self

    async def __aexit__(self, *args):
        if self._client is not None:
            try:
                await self._client.__aexit__(*args)
            except Exception:
                pass

    async def _throttle(self):
        if not self.config.runtime.rate_limit_rps:
            return
        interval = 1.0 / self.config.runtime.rate_limit_rps
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._last_call + interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    def _parse_response(self, response) -> JevScores:
        """Map typed SDK response → JevScores."""
        # SDK returns SystemOneResponse with .scores/.choices/.nouls or .answers dict
        # Support both shapes defensively
        def get_score(key):
            # try typed accessor then dict
            if hasattr(response, "scores") and key in response.scores:
                ans = response.scores[key]
                return ans.score, getattr(ans, "confidence", 0.7), getattr(ans, "probabilities", {})
            if hasattr(response, "answers") and key in response.answers:
                ans = response.answers[key]
                return getattr(ans, "score", 2.0), getattr(ans, "confidence", 0.7), getattr(ans, "probabilities", {})
            return 2.0, 0.6, {}

        def get_choice(key):
            if hasattr(response, "choices") and key in response.choices:
                ans = response.choices[key]
                return ans.choice, getattr(ans, "confidence", 0.7), getattr(ans, "probabilities", {})
            if hasattr(response, "answers") and key in response.answers:
                ans = response.answers[key]
                return getattr(ans, "choice", "unknown"), getattr(ans, "confidence", 0.7), getattr(ans, "probabilities", {})
            return "unknown", 0.6, {}

        def get_noul(key):
            if hasattr(response, "nouls") and key in response.nouls:
                return float(response.nouls[key].noul)
            if hasattr(response, "answers") and key in response.answers:
                return float(getattr(response.answers[key], "noul", 0.5))
            return 0.5

        s_sen, c_sen, _ = get_score("seniority")
        s_icp, c_icp, _ = get_score("icp_fit")
        s_int, c_int, _ = get_score("intent")
        s_eng, c_eng, _ = get_score("engagement")
        s_dq, c_dq, _ = get_score("data_quality")

        p_choice, p_conf, p_probs = get_choice("persona")
        i_choice, i_conf, i_probs = get_choice("industry_fit")
        st_choice, st_conf, _ = get_choice("company_stage")

        return JevScores(
            seniority=float(s_sen),
            icp_fit=float(s_icp),
            intent=float(s_int),
            engagement=float(s_eng),
            data_quality=float(s_dq),
            seniority_conf=float(c_sen),
            icp_fit_conf=float(c_icp),
            intent_conf=float(c_int),
            engagement_conf=float(c_eng),
            data_quality_conf=float(c_dq),
            persona=str(p_choice),
            persona_conf=float(p_conf),
            persona_probs=dict(p_probs) if isinstance(p_probs, dict) else {},
            industry_fit=str(i_choice),
            industry_fit_conf=float(i_conf),
            industry_fit_probs=dict(i_probs) if isinstance(i_probs, dict) else {},
            company_stage=str(st_choice),
            company_stage_conf=float(st_conf),
            valid_business_email=float(get_noul("valid_business_email")),
            is_spam_fake=float(get_noul("is_spam_fake")),
            has_buying_intent=float(get_noul("has_buying_intent")),
            gdpr_risk=float(get_noul("gdpr_risk")),
        )

    async def score_one(self, state: dict[str, Any]) -> JevScores:
        """Score a single lead state (with throttling + semaphore)."""
        if self._mock_mode:
            # tiny async jitter to mimic real latency distribution
            await asyncio.sleep(random.uniform(0.002, 0.010))
            return _mock_scores_for_lead(state)

        async with self._sem:
            await self._throttle()
            # retry loop
            last_err = None
            for attempt in range(self.config.runtime.max_retries + 1):
                try:
                    # The SDK's system_one is async
                    response = await self._client.system_one(  # type: ignore
                        state=state,
                        questions=self.questions,  # type: ignore
                    )
                    return self._parse_response(response)
                except Exception as e:
                    last_err = e
                    # check if retryable
                    is_retryable = "rate" in str(e).lower() or "429" in str(e) or "timeout" in str(e).lower() or "503" in str(e) or "502" in str(e)
                    if attempt < self.config.runtime.max_retries and is_retryable:
                        backoff = (2 ** attempt) * 0.4 + random.random() * 0.3
                        log.warning(f"Jev call failed (attempt {attempt+1}/{self.config.runtime.max_retries+1}): {e} — retry in {backoff:.1f}s")
                        await asyncio.sleep(backoff)
                        continue
                    # non-retryable or out of retries: fall back to mock for this lead rather than failing pipeline
                    log.error(f"Jev call failed after {attempt+1} attempts: {e} — using mock fallback for this lead")
                    return _mock_scores_for_lead(state)
            # should not reach
            log.error(f"Unexpected: {last_err}")
            return _mock_scores_for_lead(state)

    async def score_batch(self, states: List[dict[str, Any]]) -> List[JevScores]:
        tasks = [self.score_one(s) for s in states]
        return await asyncio.gather(*tasks)
