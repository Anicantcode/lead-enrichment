import pandas as pd
from lead_enrichment.normalize import canonicalize_columns, normalize_email, normalize_frame, deduplicate


def test_canonicalize():
    df = pd.DataFrame({"Email Address": ["a@b.com"], "Company Name": ["Acme"], "Job Title": ["CEO"]})
    out = canonicalize_columns(df)
    assert "email" in out.columns
    assert "company" in out.columns
    assert "title" in out.columns


def test_normalize_email():
    assert normalize_email("  SARAH@Example.COM ") == "sarah@example.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email(None) is None


def test_dedup():
    df = pd.DataFrame({"email": ["a@b.com", "A@B.COM", "c@d.com"], "company": ["X", "X", "Y"]})
    deduped, dupes = deduplicate(df)
    assert len(deduped) == 2
    assert len(dupes) == 1


def test_normalize_frame_preserves():
    df = pd.DataFrame({"email": ["a@b.com"], "full_name": ["John Doe"], "company": ["Acme"]})
    out = normalize_frame(df)
    assert out.iloc[0]["email"] == "a@b.com"
    assert "first_name" in out.columns
