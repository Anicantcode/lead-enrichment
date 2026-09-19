"""Normalization & dedup — clean messy CSV/Excel inputs before Jev sees them."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from email_validator import validate_email, EmailNotValidError
    HAS_EMAIL_VALIDATOR = True
except ImportError:
    HAS_EMAIL_VALIDATOR = False

try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False


# Maps common column name variants → canonical field
COLUMN_ALIASES: Dict[str, str] = {
    # email
    "email": "email", "e-mail": "email", "email address": "email", "work email": "email", "business email": "email",
    # name
    "first_name": "first_name", "firstname": "first_name", "fname": "first_name", "given name": "first_name",
    "last_name": "last_name", "lastname": "last_name", "lname": "last_name", "surname": "last_name", "family name": "last_name",
    "full_name": "full_name", "full name": "full_name", "name": "full_name", "contact name": "full_name", "lead name": "full_name",
    # phone
    "phone": "phone", "phone number": "phone", "mobile": "phone", "cell": "phone", "telephone": "phone",
    # company
    "company": "company", "company name": "company", "organization": "company", "org": "company", "account": "company", "employer": "company",
    "company_domain": "company_domain", "domain": "company_domain", "company domain": "company_domain",
    "website": "website", "company website": "website", "site": "website", "url": "website",
    "company_size": "company_size", "size": "company_size", "employees": "company_size", "employee count": "company_size", "company size": "company_size",
    "industry": "industry", "sector": "industry", "vertical": "industry",
    # title
    "title": "title", "job title": "title", "role": "title", "position": "title", "job_title": "title",
    "seniority": "seniority_raw", "seniority_raw": "seniority_raw",
    "department": "department", "dept": "department", "function": "department", "team": "department",
    # linkedin
    "linkedin": "linkedin_url", "linkedin_url": "linkedin_url", "linkedin url": "linkedin_url", "linkedin profile": "linkedin_url",
    # geo
    "city": "city", "town": "city",
    "state": "state", "region": "state", "province": "state",
    "country": "country", "country code": "country",
    # source
    "source": "source", "lead source": "source", "origin": "source", "channel": "source",
    # notes
    "notes": "notes", "description": "description", "comments": "notes", "about": "notes", "bio": "notes",
    "created_at": "created_at", "created": "created_at", "date": "created_at", "timestamp": "created_at",
}


def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to canonical names; preserve unknowns in place."""
    new_cols = {}
    for c in df.columns:
        key = str(c).strip().lower()
        key = re.sub(r"\s+", " ", key)
        key = key.replace("-", "_").replace(" ", "_")
        # try alias lookup with original and underscored
        alias = COLUMN_ALIASES.get(key) or COLUMN_ALIASES.get(str(c).strip().lower())
        if alias:
            # avoid collision: if canonical already exists, keep raw_{original}
            if alias in new_cols.values():
                new_cols[c] = f"raw_{c}"
            else:
                new_cols[c] = alias
        else:
            # also try lower without underscore
            alias2 = COLUMN_ALIASES.get(str(c).strip().lower().replace("_", " "))
            if alias2 and alias2 not in new_cols.values():
                new_cols[c] = alias2
            else:
                new_cols[c] = c  # keep as-is, will go to raw later
    return df.rename(columns=new_cols)


def normalize_email(email: Any) -> Optional[str]:
    if not email or pd.isna(email):
        return None
    s = str(email).strip().lower()
    s = s.strip("<> ;,")
    if not s or "@" not in s:
        return None
    if HAS_EMAIL_VALIDATOR:
        try:
            v = validate_email(s, check_deliverability=False)
            return v.normalized  # type: ignore
        except EmailNotValidError:
            # still return lowercased if it looks like email, but flag via domain
            return s
    return s


def normalize_email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.split("@")[-1].lower().strip()


def normalize_phone(phone: Any, default_region: str = "US") -> Optional[str]:
    if not phone or pd.isna(phone):
        return None
    s = str(phone).strip()
    if not s:
        return None
    if HAS_PHONENUMBERS:
        try:
            # remove common noise
            parsed = phonenumbers.parse(s, default_region)
            if phonenumbers.is_possible_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            pass
    # fallback: digits only
    digits = re.sub(r"[^\d+]", "", s)
    return digits if len(digits) >= 7 else None


def normalize_url(url: Any) -> Optional[str]:
    if not url or pd.isna(url):
        return None
    s = str(url).strip()
    if not s:
        return None
    if not re.match(r"https?://", s, re.I):
        s = "https://" + s
    # trim trailing slash
    s = s.rstrip("/")
    return s


def split_full_name(full: Any) -> tuple[Optional[str], Optional[str]]:
    if not full or pd.isna(full):
        return None, None
    parts = str(full).strip().split()
    if len(parts) == 0:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[-1]


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Apply per-column normalization in place and return new frame."""
    df = canonicalize_columns(df.copy())
    # strip whitespace from all string cells
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(lambda x: str(x).strip() if isinstance(x, str) else x)
            df[col] = df[col].replace({"": None, "nan": None, "NaN": None, "None": None, "-": None, "--": None})

    # email
    if "email" in df.columns:
        df["email"] = df["email"].apply(normalize_email)
        df["email_domain"] = df["email"].apply(normalize_email_domain)
    # phone
    if "phone" in df.columns:
        df["phone"] = df["phone"].apply(lambda x: normalize_phone(x))
    # linkedin/company urls
    if "linkedin_url" in df.columns:
        df["linkedin_url"] = df["linkedin_url"].apply(normalize_url)
    if "website" in df.columns:
        df["website"] = df["website"].apply(normalize_url)
    if "company_domain" in df.columns:
        df["company_domain"] = df["company_domain"].apply(lambda x: str(x).lower().strip().lstrip("https://").lstrip("http://").rstrip("/") if x and not pd.isna(x) else None)

    # names: if full_name present but first/last missing, split
    if "full_name" in df.columns:
        mask_first_missing = df.get("first_name").isna() if "first_name" in df.columns else pd.Series([True]*len(df))
        if isinstance(mask_first_missing, pd.Series):
            for idx in df[mask_first_missing].index:
                fn, ln = split_full_name(df.at[idx, "full_name"])
                if fn and (pd.isna(df.at[idx, "first_name"]) if "first_name" in df.columns else True):
                    if "first_name" not in df.columns:
                        df["first_name"] = None
                    df.at[idx, "first_name"] = fn
                if ln and (pd.isna(df.at[idx, "last_name"]) if "last_name" in df.columns else True):
                    if "last_name" not in df.columns:
                        df["last_name"] = None
                    df.at[idx, "last_name"] = ln

    # title/company: title case for display
    for col in ("company", "industry", "city", "country"):
        if col in df.columns:
            df[col] = df[col].apply(lambda x: str(x).strip() if x and not pd.isna(x) else None)

    return df


def deduplicate(df: pd.DataFrame, keep: str = "first") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduplicate on email (primary) then (full_name+company). Returns (deduped, duplicates)."""
    if df.empty:
        return df, df.iloc[0:0]

    # primary key: email lower
    email_key = df["email"].str.lower() if "email" in df.columns else pd.Series([None]*len(df))
    # secondary key
    name = df.get("full_name", df.get("first_name", pd.Series([None]*len(df)))).astype(str).str.lower()
    comp = df.get("company", pd.Series([None]*len(df))).astype(str).str.lower()
    sec_key = name + "|" + comp

    # build composite dedup key: prefer email, else sec_key
    dedup_key = email_key.where(email_key.notna() & (email_key != "none"), sec_key)

    # find duplicates
    duplicated = df.duplicated(subset=[dedup_key.name or "email"], keep=keep) if "email" in df.columns else pd.Series([False]*len(df))
    # more robust: duplicated on the series itself
    duplicated = dedup_key.duplicated(keep=keep)

    dupes = df[duplicated].copy()
    deduped = df[~duplicated].copy()
    # also tag dupe reason
    if not dupes.empty:
        dupes["_dedupe_reason"] = "duplicate_email_or_name_company"
    return deduped, dupes
