#!/usr/bin/env python3
"""
Briefing Nefrológico — Top Annual Articles Fetcher
===================================================
Fetches the highest-impact nephrology articles (RCTs, meta-analyses, systematic reviews)
from the last 365 days and saves them to data/top-annual.json.

Usage:
    python scripts/fetch_top_annual.py [--days N]
"""

import json
import sys
import os
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from fetch_articles import (
    pubmed_search, pubmed_fetch, epmc_search,
    classify, extract_key_finding, compute_clinical_impact,
    SUBSPECIALTIES,
)

MIN_IMPACT_SCORE = 55
MAX_PER_SUB      = 5


def main():
    parser = argparse.ArgumentParser(description="Fetch top annual nephrology articles")
    parser.add_argument("--days", type=int, default=365,
                        help="Days to look back (default 365)")
    args = parser.parse_args()

    today      = datetime.utcnow()
    date_end   = today.strftime("%Y/%m/%d")
    date_start = (today - timedelta(days=args.days)).strftime("%Y/%m/%d")

    start_dt = today - timedelta(days=args.days)
    MONTHS_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    year_label = (f"{MONTHS_ES[start_dt.month].capitalize()} {start_dt.year} "
                  f"– {MONTHS_ES[today.month].capitalize()} {today.year}")

    print("=" * 60)
    print("  Briefing Nefrológico — Annual Top Articles Fetcher")
    print(f"  Range : {date_start} → {date_end}")
    print(f"  Period: {year_label}")
    print("=" * 60)

    print("[1/4] Searching PubMed…")
    pmids = pubmed_search(date_start, date_end, max_results=500)
    if not pmids:
        print("  No PMIDs found — keeping existing data.")
        sys.exit(0)

    print(f"\n[2/4] Fetching {len(pmids)} articles…")
    raw = pubmed_fetch(pmids)
    print(f"  Parsed: {len(raw)} articles with abstracts")

    print("\n[3/4] Querying Europe PMC…")
    existing_dois = {a["doi"] for a in raw if not a["doi"].startswith("PMID")}
    epmc_arts = epmc_search(date_start, date_end, existing_dois)
    raw.extend(epmc_arts)
    print(f"  Total pool: {len(raw)} articles")

    print("\n[4/4] Classifying and selecting top articles…")
    buckets = {s["id"]: [] for s in SUBSPECIALTIES}

    for art in raw:
        # Only high-evidence types (meta-analysis, SR, RCT)
        if art.get("evidenceRank", 7) > 3:
            continue
        sub_id = classify(art)
        if not sub_id:
            continue
        art["keyFindings"] = extract_key_finding(art["abstract"])
        impact = compute_clinical_impact(art)
        if impact["score"] < MIN_IMPACT_SCORE:
            continue
        clean = {
            "id":             f"ann-{sub_id}-{art['pmid'] or art['doi'][:20].replace('/','_')}",
            "title":          art["title"],
            "journal":        art["journal"],
            "authors":        art["authors"],
            "year":           art["year"],
            "doi":            art["doi"],
            "url":            art["url"],
            "evidenceLevel":  art["evidenceLevel"],
            "evidenceRank":   art["evidenceRank"],
            "journalRank":    art.get("journalRank", 99),
            "abstract":       art["abstract"],
            "keyFindings":    art["keyFindings"],
            "source":         art["source"],
            "clinicalImpact": impact,
        }
        buckets[sub_id].append(clean)

    output_subs = []
    total = 0
    for sub in SUBSPECIALTIES:
        arts = buckets[sub["id"]]
        # Sort by impact score desc, then evidence rank asc
        arts.sort(key=lambda a: (
            -a["clinicalImpact"]["score"],
            a["evidenceRank"],
            a.get("journalRank", 99),
        ))
        arts = arts[:MAX_PER_SUB]
        for a in arts:
            a.pop("journalRank", None)
        if arts:
            output_subs.append({
                "id":       sub["id"],
                "name":     sub["name"],
                "color":    sub["color"],
                "articles": arts,
            })
            total += len(arts)

    all_arts = [a for s in output_subs for a in s["articles"]]
    n_meta   = sum(1 for a in all_arts if a["evidenceRank"] == 1)
    n_sr     = sum(1 for a in all_arts if a["evidenceRank"] == 2)
    n_rct    = sum(1 for a in all_arts if a["evidenceRank"] == 3)

    output = {
        "yearLabel":   year_label,
        "lastUpdated": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "total":            total,
            "metaAnalysis":     n_meta,
            "systematicReview": n_sr,
            "rct":              n_rct,
        },
        "subspecialties": output_subs,
    }

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(data_dir, "top-annual.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved {total} articles across {len(output_subs)} subspecialties")
    print(f"  Meta-análisis: {n_meta}  |  Rev. sistemáticas: {n_sr}  |  RCT: {n_rct}")
    print(f"  Output → {out_path}")
    print("=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
