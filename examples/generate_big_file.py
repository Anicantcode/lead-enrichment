#!/usr/bin/env python3
"""Generate a big synthetic lead file (CSV/XLSX) for stress-testing the pipeline.

Usage:
  python examples/generate_big_file.py --rows 5000 --out /tmp/big_leads.csv
  python examples/generate_big_file.py --rows 10000 --out /tmp/big_leads.xlsx
"""

import argparse
import csv
import random
from pathlib import Path

FIRST = ["Sarah","Marcus","Priya","David","Alex","Fatima","James","Sophie","Liu","Carlos","Hannah","Noah","Isabella","Raj","Olivia","Grace","Yuki","Emma","Liam","Ava","Noah","Ethan","Mia","Lucas","Amara","Kenji","Zara","Omar","Sofia","Mateo"]
LAST = ["Chen","Johnson","Patel","Kim","Turner","Al-Hassan","OBrien","Laurent","Wei","Mendez","Smith","Brown","Garcia","Mehta","Wilson","Tanaka","Lee","Davis","Miller","Wilson","Taylor","Anderson","Thomas","Moore","Jackson","Martin","White","Harris","Clark","Lewis"]
COMPANIES = [
    ("Acme SaaS","SaaS","51-200"),("Fintech Labs","Fintech","201-500"),("GrowthCraft","Marketing Tech","11-50"),
    ("Bolt Data","Software","501-1000"),("MidMarket Co","B2B Services","51-200"),("SaaS Unicorn","SaaS","1000+"),
    ("Old Manufacturing Inc","Manufacturing","501-1000"),("Paris Startup","SaaS","11-50"),("Fake Corp","Software","1-10"),
    ("APAC SaaS","SaaS","51-200"),("Agency LATAM","B2B Services","11-50"),("Enterprise Global","Software","5000+"),
    ("SMB Tools","SaaS","11-50"),("DataDriven","Software","51-200"),("HealthTech Example","Healthcare","51-200"),
    ("NewCo AI","SaaS","1-10"),("Tokyo SaaS","SaaS","201-500"),("Berlin Tech","Software","51-200"),
    ("NYC Growth","Marketing Tech","11-50"),("Lagos Fin","Fintech","51-200"),
]
TITLES = ["VP Sales","Head of Growth","Founder & CEO","Director RevOps","Sales Manager","Chief Revenue Officer","Plant Manager","Head of Sales","VP Marketing","Senior SDR","Procurement Lead","Marketing Manager","Product Manager","CTO","Head of Growth","Founder","Director of Sales","Student","Intern","Recruiter"]
SOURCES = ["inbound demo request","webinar","referral","inbound form","outbound cold","outbound warm","content download","list_purchase"]
NOTES_TPL = [
    "Evaluating enrichment tools for outbound scale. Need CRM migration support. Budget approved Q2.",
    "Attended webinar on lead enrichment. Wants pricing for 10k leads/mo.",
    "Referred by customer. Actively comparing vendors - timeline 2 weeks.",
    "Looking to enrich 50k records. Currently on ZoomInfo but unhappy with data quality.",
    "Prospected via LinkedIn. No reply yet.",
    "Need to scale outbound from 200 to 2000 touches/day. Evaluating now.",
    "No notes",
    "Student project - test entry",
    "Downloaded ebook on outbound. EU contact - check GDPR.",
    "asdf test fake entry",
    "Interested in APAC enrichment coverage. Wants sample data.",
]

def gen_one(i: int) -> dict:
    fn = random.choice(FIRST)
    ln = random.choice(LAST)
    comp, ind, size = random.choice(COMPANIES)
    title = random.choice(TITLES)
    # occasional bad data
    if random.random() < 0.03:
        email = "test@test.com"
    elif random.random() < 0.02:
        email = f"{fn.lower()}.{ln.lower()}@gmail.com"
    else:
        domain = comp.lower().replace(" ", "").replace("-","") + random.choice([".io",".co",".com",".ai"])
        email = f"{fn.lower()}.{ln.lower()}@{domain}"
    city = random.choice(["San Francisco","New York","London","Paris","Berlin","Tokyo","Singapore","Austin","Boston","Chicago"])
    country = random.choice(["US","GB","FR","DE","JP","SG","IN","MX","BR"])
    source = random.choice(SOURCES)
    notes = random.choice(NOTES_TPL)
    # inject duplicate emails occasionally
    if random.random() < 0.02 and i > 10:
        email = f"duplicate{i%5}@acmesaas.io"
    return {
        "first_name": fn, "last_name": ln, "email": email, "company": comp, "company_size": size,
        "industry": ind, "title": title, "phone": f"+1 415 555 {random.randint(1000,9999)}",
        "city": city, "country": country, "source": source, "notes": notes,
        "linkedin_url": f"https://linkedin.com/in/{fn.lower()}{ln.lower()}{i}",
        "website": f"https://{comp.lower().replace(' ','')}.io"
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--out", type=str, default="examples/big_leads.csv")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [gen_one(i) for i in range(args.rows)]
    if out.suffix.lower() in (".xlsx",".xls"):
        import pandas as pd
        pd.DataFrame(rows).to_excel(out, index=False)
        print(f"Wrote {len(rows)} rows → {out} (xlsx)")
    else:
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows → {out} (csv)")
    # print stats
    import pandas as pd
    df = pd.DataFrame(rows)
    print(df["industry"].value_counts().head())
    print(df["source"].value_counts().head())

if __name__ == "__main__":
    main()
