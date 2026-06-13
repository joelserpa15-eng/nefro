#!/usr/bin/env python3
"""
Briefing Nefrológico Semanal — Article Fetcher
===============================================
Queries PubMed (primary) and Europe PMC (secondary) for recent high-evidence
nephrology articles from the world's top nephrology journals, ranked by
evidence level and journal impact factor.

Usage:
    python scripts/fetch_articles.py [--days N]

Environment variables:
    NCBI_EMAIL      Required by NCBI ToS (defaults to placeholder)
    NCBI_API_KEY    Optional — raises rate limit from 3 to 10 req/s
"""

import json
import time
import re
import os
import sys
import argparse
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

NCBI_EMAIL   = os.environ.get("NCBI_EMAIL", "briefing.nefrologico@noreply.com")
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

ESEARCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EPMC_URL     = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

NCBI_DELAY   = 0.12 if NCBI_API_KEY else 0.36

JOURNALS = [
    {"nlm": "N Engl J Med",               "display": "New England Journal of Medicine",                "if": 176, "rank": 1},
    {"nlm": "Lancet",                      "display": "The Lancet",                                     "if": 168, "rank": 2},
    {"nlm": "JAMA",                        "display": "JAMA",                                           "if": 120, "rank": 3},
    {"nlm": "BMJ",                         "display": "BMJ",                                            "if": 105, "rank": 4},
    {"nlm": "Nat Med",                     "display": "Nature Medicine",                                "if": 82,  "rank": 5},
    {"nlm": "Nat Rev Nephrol",            "display": "Nature Reviews Nephrology",                      "if": 40,  "rank": 6},
    {"nlm": "Ann Intern Med",             "display": "Annals of Internal Medicine",                    "if": 35,  "rank": 7},
    {"nlm": "Kidney Int",                 "display": "Kidney International",                           "if": 14,  "rank": 8},
    {"nlm": "J Am Soc Nephrol",           "display": "Journal of the American Society of Nephrology",  "if": 13,  "rank": 9},
    {"nlm": "Am J Transplant",            "display": "American Journal of Transplantation",            "if": 9,   "rank": 10},
    {"nlm": "Am J Kidney Dis",            "display": "American Journal of Kidney Diseases",            "if": 9,   "rank": 11},
    {"nlm": "Clin J Am Soc Nephrol",      "display": "Clinical Journal of the American Society of Nephrology", "if": 8, "rank": 12},
    {"nlm": "Transplantation",            "display": "Transplantation",                                "if": 6,   "rank": 13},
    {"nlm": "Nephrol Dial Transplant",    "display": "Nephrology Dialysis Transplantation",            "if": 5,   "rank": 14},
    {"nlm": "Kidney Int Rep",             "display": "Kidney International Reports",                   "if": 4,   "rank": 15},
    {"nlm": "Am J Nephrol",               "display": "American Journal of Nephrology",                 "if": 4,   "rank": 16},
    {"nlm": "J Nephrol",                  "display": "Journal of Nephrology",                          "if": 3,   "rank": 17},
    {"nlm": "Perit Dial Int",             "display": "Peritoneal Dialysis International",              "if": 3,   "rank": 18},
    {"nlm": "Semin Dial",                 "display": "Seminars in Dialysis",                           "if": 3,   "rank": 19},
    {"nlm": "Nephron",                    "display": "Nephron",                                        "if": 3,   "rank": 20},
]

NLM_TO_RANK    = {j["nlm"]: j["rank"] for j in JOURNALS}
NLM_TO_DISPLAY = {j["nlm"]: j["display"] for j in JOURNALS}

EVIDENCE_LEVELS = [
    (1, "Meta-análisis",                  ["meta-analysis"]),
    (2, "Revisión Sistemática",           ["systematic review"]),
    (3, "Ensayo Clínico Aleatorizado",    ["randomized controlled trial", "controlled clinical trial",
                                           "clinical trial, phase iii", "clinical trial, phase iv"]),
    (3, "Ensayo Clínico",                 ["clinical trial", "clinical trial, phase ii",
                                           "clinical trial, phase i"]),
    (4, "Estudio Multicéntrico / Cohorte",["multicenter study", "observational study",
                                           "prospective study"]),
    (5, "Estudio Caso-Control",           ["case-control study"]),
    (6, "Serie de Casos",                 ["case reports"]),
    (7, "Artículo Original",              ["journal article", "review"]),
]

def get_evidence(pub_types):
    lowered = [pt.lower() for pt in pub_types]
    for rank, label, keys in EVIDENCE_LEVELS:
        if any(k in pt for k in keys for pt in lowered):
            return rank, label
    return 7, "Artículo Original"

SUBSPECIALTIES = [
    {
        "id": "erca",
        "name": "Enfermedad Renal Crónica",
        "color": "#1565C0",
        "keywords": [
            "chronic kidney disease", "ckd", "renal insufficiency chronic",
            "glomerular filtration rate", "gfr", "egfr", "kidney function decline",
            "ckd progression", "renal fibrosis", "tubular atrophy",
            "sglt2 inhibitor ckd", "dapagliflozin kidney", "empagliflozin kidney",
            "finerenone ckd", "crd management", "uremic syndrome",
            "mineral bone disorder ckd", "ckd-mbd", "secondary hyperparathyroidism",
            "anemia ckd", "erythropoiesis-stimulating agent", "cardiovascular ckd",
            "cardiorenal syndrome", "albuminuria", "proteinuria reduction",
            "renin-angiotensin", "ace inhibitor kidney", "arb kidney",
        ],
        "mesh": [
            "Renal Insufficiency, Chronic", "Glomerular Filtration Rate",
            "Albuminuria", "Kidney Failure, Chronic",
            "Sodium-Glucose Transporter 2 Inhibitors",
            "Hyperparathyroidism, Secondary",
        ],
    },
    {
        "id": "aki",
        "name": "Lesión Renal Aguda",
        "color": "#C62828",
        "keywords": [
            "acute kidney injury", "aki", "acute renal failure",
            "acute tubular necrosis", "contrast-induced nephropathy",
            "cardiorenal syndrome acute", "hepatorenal syndrome",
            "sepsis-associated aki", "cisplatin nephrotoxicity",
            "vancomycin nephrotoxicity", "aminoglycoside nephrotoxicity",
            "renal recovery aki", "kidney recovery", "biomarker aki",
            "ngal", "kim-1", "timp-2", "igfbp7", "renal replacement therapy acute",
            "continuous renal replacement", "crrt", "intermittent hemodialysis aki",
            "aki prevention", "fluid overload aki", "oliguria",
        ],
        "mesh": [
            "Acute Kidney Injury", "Renal Replacement Therapy",
            "Sepsis", "Contrast Media",
        ],
    },
    {
        "id": "glomerulopatias",
        "name": "Glomerulopatías",
        "color": "#6A1B9A",
        "keywords": [
            "glomerulonephritis", "glomerulopathy", "nephrotic syndrome",
            "focal segmental glomerulosclerosis", "fsgs", "membranous nephropathy",
            "iga nephropathy", "iga vasculitis", "minimal change disease",
            "membranoproliferative", "lupus nephritis", "anti-gbm disease",
            "goodpasture", "anca vasculitis", "granulomatosis polyangiitis",
            "microscopic polyangiitis", "podocyte", "sparsentan", "budesonide kidney",
            "iptacopan", "avacopan", "rituximab lupus", "mycophenolate nephritis",
            "complement-mediated nephropathy", "c3 glomerulopathy",
        ],
        "mesh": [
            "Glomerulonephritis", "Nephrotic Syndrome",
            "Glomerulosclerosis, Focal Segmental",
            "Glomerulonephritis, Membranous",
            "Glomerulonephritis, IGA",
            "Lupus Nephritis", "Vasculitis",
        ],
    },
    {
        "id": "nefro-diabetica",
        "name": "Nefropatía Diabética",
        "color": "#2E7D32",
        "keywords": [
            "diabetic nephropathy", "diabetic kidney disease", "dkd",
            "diabetic glomerulosclerosis", "kimmelstiel-wilson",
            "microalbuminuria diabetes", "macroalbuminuria diabetes",
            "sglt2 diabetic kidney", "finerenone diabetic",
            "glucagon-like peptide", "glp-1 kidney", "semaglutide kidney",
            "liraglutide kidney", "tirzepatide kidney",
            "type 2 diabetes kidney", "type 1 diabetes nephropathy",
            "diabetic ckd", "renal outcome diabetes",
            "kidney endpoint diabetes", "uacr diabetes",
        ],
        "mesh": [
            "Diabetic Nephropathies", "Diabetes Mellitus, Type 2",
            "Albuminuria", "Sodium-Glucose Transporter 2 Inhibitors",
            "Glucagon-Like Peptide-1 Receptor Agonists",
        ],
    },
    {
        "id": "trasplante",
        "name": "Trasplante Renal",
        "color": "#00695C",
        "keywords": [
            "kidney transplantation", "renal transplantation", "renal allograft",
            "transplant rejection", "acute rejection", "chronic allograft nephropathy",
            "calcineurin inhibitor", "tacrolimus", "cyclosporine transplant",
            "mycophenolate transplant", "mtor inhibitor transplant",
            "belatacept", "donor-specific antibody", "dsa", "desensitization",
            "living donor kidney", "deceased donor kidney", "extended criteria donor",
            "machine perfusion kidney", "normothermic perfusion",
            "ischemia reperfusion transplant", "delayed graft function",
            "kidney paired donation", "hla sensitization", "panel reactive antibody",
            "bk virus nephropathy", "cytomegalovirus transplant",
        ],
        "mesh": [
            "Kidney Transplantation", "Graft Rejection",
            "Calcineurin Inhibitors", "Immunosuppressive Agents",
            "Donor Selection", "Tissue Donors",
        ],
    },
    {
        "id": "hta-renal",
        "name": "Hipertensión y Riñón",
        "color": "#AD1457",
        "keywords": [
            "hypertension kidney", "hypertensive nephrosclerosis",
            "renovascular hypertension", "renal artery stenosis",
            "blood pressure ckd", "blood pressure target kidney",
            "antihypertensive kidney protection",
            "resistant hypertension kidney", "aldosterone kidney",
            "primary aldosteronism", "conn syndrome",
            "renin-angiotensin system kidney", "mineralocorticoid receptor kidney",
            "sympathetic activation kidney", "renal denervation hypertension",
            "blood pressure lowering kidney", "systolic pressure ckd",
        ],
        "mesh": [
            "Hypertension, Renal", "Hypertension", "Blood Pressure",
            "Renal Artery Obstruction", "Aldosteronism",
            "Renin-Angiotensin System",
        ],
    },
    {
        "id": "dialisis",
        "name": "Terapia Renal Sustitutiva",
        "color": "#0277BD",
        "keywords": [
            "hemodialysis", "haemodialysis", "peritoneal dialysis",
            "end-stage renal disease", "esrd", "end-stage kidney disease",
            "renal replacement therapy", "dialysis adequacy", "kt/v",
            "high-flux dialysis", "hemodiafiltration", "online hemodiafiltration",
            "convective volume", "dialysate", "vascular access",
            "arteriovenous fistula", "tunneled dialysis catheter",
            "peritoneal membrane", "peritoneal transport",
            "automated peritoneal dialysis", "continuous ambulatory peritoneal dialysis",
            "nocturnal hemodialysis", "home hemodialysis",
            "incremental dialysis", "dialysis initiation", "dialysis mortality",
            "conservative management esrd", "renal supportive care",
        ],
        "mesh": [
            "Renal Dialysis", "Peritoneal Dialysis",
            "Kidney Failure, Chronic", "Vascular Access Devices",
            "Arteriovenous Shunt, Surgical",
        ],
    },
    {
        "id": "nefrolitiasis",
        "name": "Nefrolitiasis y Uropatías",
        "color": "#E65100",
        "keywords": [
            "nephrolithiasis", "kidney stones", "urolithiasis",
            "calcium oxalate stone", "calcium phosphate stone",
            "uric acid stone", "struvite stone", "cystinuria",
            "hyperoxaluria", "hypercalciuria", "hyperuricosuria",
            "stone recurrence", "stone prevention", "potassium citrate",
            "thiazide diuretics stones", "allopurinol stones",
            "shock wave lithotripsy", "ureteroscopy", "percutaneous nephrolithotomy",
            "obstructive nephropathy", "hydronephrosis", "ureteral obstruction",
        ],
        "mesh": [
            "Nephrolithiasis", "Urolithiasis", "Kidney Calculi",
            "Lithotripsy", "Ureteroscopy",
        ],
    },
    {
        "id": "nefro-genetica",
        "name": "Nefropatías Hereditarias y Genéticas",
        "color": "#37474F",
        "keywords": [
            "alport syndrome", "pkd", "polycystic kidney disease",
            "autosomal dominant pkd", "adpkd", "autosomal recessive pkd",
            "arpkd", "tolvaptan", "mtor pkd", "pkd1", "pkd2",
            "tuberous sclerosis kidney", "von hippel-lindau kidney",
            "fabry disease kidney", "alpha-galactosidase",
            "hereditary nephritis", "thin basement membrane",
            "nail-patella syndrome", "congenital nephrotic syndrome",
            "nephronophthisis", "bardet-biedl", "genetic testing kidney",
            "whole exome sequencing nephrology",
        ],
        "mesh": [
            "Polycystic Kidney Diseases", "Alport Syndrome",
            "Fabry Disease", "Kidney Diseases", "Genetic Testing",
        ],
    },
    {
        "id": "onconefrologia",
        "name": "Onconefrología",
        "color": "#BF360C",
        "keywords": [
            "onco-nephrology", "cancer kidney", "checkpoint inhibitor nephrotoxicity",
            "immune checkpoint inhibitor kidney", "immune-related nephritis",
            "acute interstitial nephritis immunotherapy",
            "myeloma kidney", "cast nephropathy", "monoclonal gammopathy kidney",
            "mgrs", "chemotherapy nephrotoxicity", "cisplatin kidney injury",
            "renal cell carcinoma", "kidney cancer treatment",
            "tyrosine kinase inhibitor kidney", "vegf inhibitor kidney",
            "hematopoietic stem cell transplant kidney",
            "thrombotic microangiopathy cancer", "cancer associated aki",
        ],
        "mesh": [
            "Neoplasms", "Acute Kidney Injury",
            "Multiple Myeloma", "Immunotherapy",
            "Carcinoma, Renal Cell",
        ],
    },
]

SUB_INDEX = {s["id"]: s for s in SUBSPECIALTIES}


def http_get(url, params=None, timeout=30):
    full_url = url + ("?" + urlencode(params) if params else "")
    req = Request(full_url, headers={
        "User-Agent": "BriefingNefrologico/2.0 (https://github.com/joelserpa15-eng/nefro)"
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (URLError, HTTPError) as e:
        print(f"    [WARN] HTTP error for {full_url[:80]}…: {e}", file=sys.stderr)
        return None


def ncbi_get(url, params):
    params = dict(params)
    params["email"] = NCBI_EMAIL
    params["tool"]  = "BriefingNefrologico"
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    result = http_get(url, params)
    time.sleep(NCBI_DELAY)
    return result


def build_pubmed_query(date_start, date_end):
    journal_clause = " OR ".join(f'"{j["nlm"]}"[Journal]' for j in JOURNALS)
    evidence_clause = (
        'meta-analysis[pt] OR "systematic review"[pt] OR '
        '"randomized controlled trial"[pt] OR "controlled clinical trial"[pt] OR '
        '"clinical trial"[pt] OR "multicenter study"[pt] OR '
        '"observational study"[pt]'
    )
    nephro_clause = (
        '"kidney diseases"[MeSH] OR "renal insufficiency"[MeSH] OR '
        '"kidney"[tiab] OR "renal"[tiab] OR "nephro"[tiab] OR '
        '"glomerulo"[tiab] OR "dialysis"[tiab] OR "hemodialysis"[tiab] OR '
        '"transplant"[tiab] OR "proteinuria"[tiab] OR '
        '"albuminuria"[tiab] OR "nephritis"[tiab]'
    )
    return (
        f"({journal_clause}) AND ({evidence_clause}) "
        f"AND ({nephro_clause}) "
        f"AND {date_start}:{date_end}[pdat]"
    )


def pubmed_search(date_start, date_end, max_results=300):
    query = build_pubmed_query(date_start, date_end)
    print(f"  Query dates: {date_start} → {date_end}")
    data = ncbi_get(ESEARCH_URL, {
        "db": "pubmed", "term": query,
        "retmax": max_results, "retmode": "json", "sort": "relevance",
    })
    if not data:
        return []
    result = json.loads(data)
    ids = result.get("esearchresult", {}).get("idlist", [])
    print(f"  PubMed: {len(ids)} PMIDs found")
    return ids


def pubmed_fetch(pmids, batch_size=20):
    articles = []
    batches = [pmids[i:i+batch_size] for i in range(0, len(pmids), batch_size)]
    for idx, batch in enumerate(batches):
        xml = ncbi_get(EFETCH_URL, {
            "db": "pubmed", "id": ",".join(batch),
            "retmode": "xml", "rettype": "abstract",
        })
        if xml:
            parsed = parse_pubmed_xml(xml)
            articles.extend(parsed)
            print(f"  Fetched batch {idx+1}/{len(batches)} → {len(parsed)} parsed")
        else:
            print(f"  [WARN] Batch {idx+1} failed", file=sys.stderr)
    return articles


def parse_pubmed_xml(xml_text):
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  [WARN] XML parse error: {exc}", file=sys.stderr)
        return []
    for pa in root.findall(".//PubmedArticle"):
        art = _parse_one(pa)
        if art:
            articles.append(art)
    return articles


def _parse_one(pa):
    medline = pa.find("MedlineCitation")
    if medline is None:
        return None
    pmid_el = medline.find("PMID")
    pmid = pmid_el.text.strip() if pmid_el is not None else ""
    article = medline.find("Article")
    if article is None:
        return None
    title_el = article.find("ArticleTitle")
    title = re.sub(r'\s+', ' ', "".join(title_el.itertext())).strip().rstrip('.') if title_el is not None else ""
    if not title:
        return None
    abstract = _extract_abstract(article)
    if not abstract:
        return None
    author_str = _extract_authors(article)
    journal_abbr, journal_display, year = _extract_journal(article)
    doi = ""
    for id_el in pa.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi":
            doi = (id_el.text or "").strip()
            break
    url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    doi_display = doi if doi else f"PMID:{pmid}"
    pub_types = [pt.text for pt in article.findall("PublicationTypeList/PublicationType") if pt.text]
    ev_rank, ev_label = get_evidence(pub_types)
    mesh = [mh.findtext("DescriptorName", "") for mh in medline.findall("MeshHeadingList/MeshHeading")]
    kws  = [kw.text for kw in medline.findall("KeywordList/Keyword") if kw.text]
    j_rank = _journal_rank(journal_abbr, journal_display)
    return {
        "pmid":            pmid,
        "title":           title,
        "journal":         journal_display,
        "journalAbbr":     journal_abbr,
        "journalRank":     j_rank,
        "authors":         author_str,
        "year":            year,
        "doi":             doi_display,
        "url":             url,
        "abstract":        abstract,
        "evidenceLevel":   ev_label,
        "evidenceRank":    ev_rank,
        "meshTerms":       mesh,
        "keywords":        kws,
        "keyFindings":     "",
        "source":          "PubMed",
    }


def _extract_abstract(article):
    abstract_el = article.find("Abstract")
    if abstract_el is None:
        return ""
    parts = []
    for text_el in abstract_el.findall("AbstractText"):
        label = text_el.get("Label", "")
        text  = "".join(text_el.itertext()).strip()
        if text:
            parts.append(f"{label + ': ' if label else ''}{text}")
    return " ".join(parts).strip()


def _extract_authors(article):
    author_list = article.find("AuthorList")
    if author_list is None:
        return "Authors not available"
    names = []
    for auth in author_list.findall("Author"):
        last    = auth.findtext("LastName", "")
        initials = auth.findtext("Initials", "")
        if last:
            names.append(f"{last} {initials}".strip())
    if not names:
        return "Authors not available"
    display = ", ".join(names[:4])
    if len(names) > 4:
        display += ", et al."
    return display


def _extract_journal(article):
    j_el = article.find("Journal")
    if j_el is None:
        return "", "", datetime.now().year
    abbr    = (j_el.findtext("ISOAbbreviation") or "").strip()
    full    = (j_el.findtext("Title") or abbr).strip()
    display = NLM_TO_DISPLAY.get(abbr, full)
    pub = j_el.find("JournalIssue/PubDate")
    year = datetime.now().year
    if pub is not None:
        yr_el = pub.find("Year")
        if yr_el is not None:
            try:
                year = int(yr_el.text)
            except ValueError:
                pass
        else:
            m = re.match(r'(\d{4})', pub.findtext("MedlineDate", ""))
            if m:
                year = int(m.group(1))
    return abbr, display, year


def _journal_rank(abbr, full_name):
    if abbr in NLM_TO_RANK:
        return NLM_TO_RANK[abbr]
    al = abbr.lower()
    fl = full_name.lower()
    for j in JOURNALS:
        jl = j["nlm"].lower()
        if jl in al or al in jl or j["display"].lower() in fl:
            return j["rank"]
    return 99


def epmc_search(date_start, date_end, existing_dois):
    journal_q = " OR ".join(f'JOURNAL:"{j["nlm"]}"' for j in JOURNALS[:12])
    date_q    = f'FIRST_PDATE:[{date_start.replace("/","-")} TO {date_end.replace("/","-")}]'
    pub_q     = ('PUB_TYPE:"meta-analysis" OR PUB_TYPE:"systematic-review" OR '
                 'PUB_TYPE:"research-article" OR PUB_TYPE:"randomized-controlled-trial"')
    query = f'({journal_q}) AND ({pub_q}) AND ({date_q})'
    params = {
        "query":      query,
        "format":     "json",
        "pageSize":   100,
        "resultType": "core",
        "sort":       "RELEVANCE",
    }
    raw = http_get(EPMC_URL, params)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    articles = []
    for r in data.get("resultList", {}).get("result", []):
        doi = r.get("doi", "")
        if doi and doi in existing_dois:
            continue
        title    = r.get("title", "").rstrip('.')
        abstract = r.get("abstractText", "")
        if not title or not abstract:
            continue
        authors_raw = r.get("authorString", "")
        authors = authors_raw[:100] + ("…" if len(authors_raw) > 100 else "")
        journal_abbr = r.get("journalAbbreviation", r.get("journalTitle", ""))
        journal_full = r.get("journalTitle", journal_abbr)
        j_rank       = _journal_rank(journal_abbr, journal_full)
        year_str = r.get("pubYear", str(datetime.now().year))
        try:
            year = int(year_str)
        except ValueError:
            year = datetime.now().year
        pub_types = [pt.get("pubType", "") for pt in r.get("pubTypeList", {}).get("pubType", [])]
        ev_rank, ev_label = get_evidence(pub_types)
        url = f"https://doi.org/{doi}" if doi else r.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "#")
        doi_display = doi if doi else f"PMID:{r.get('pmid','')}"
        articles.append({
            "pmid":          r.get("pmid", ""),
            "title":         title,
            "journal":       NLM_TO_DISPLAY.get(journal_abbr, journal_full),
            "journalAbbr":   journal_abbr,
            "journalRank":   j_rank,
            "authors":       authors,
            "year":          year,
            "doi":           doi_display,
            "url":           url,
            "abstract":      abstract,
            "evidenceLevel": ev_label,
            "evidenceRank":  ev_rank,
            "meshTerms":     [],
            "keywords":      r.get("keywordList", {}).get("keyword", []),
            "keyFindings":   "",
            "source":        "EuropePMC",
        })
    time.sleep(0.5)
    print(f"  Europe PMC: {len(articles)} additional articles found")
    return articles


def classify(article):
    title    = article.get("title", "").lower()
    abstract = article.get("abstract", "").lower()
    mesh_set = {m.lower() for m in article.get("meshTerms", [])}
    kw_set   = {k.lower() for k in article.get("keywords", [])}
    scores = {}
    for sub in SUBSPECIALTIES:
        score = 0
        for kw in sub["keywords"]:
            kl = kw.lower()
            if kl in title:
                score += 2
            elif kl in abstract:
                score += 1
        for mesh in sub["mesh"]:
            if mesh.lower() in mesh_set:
                score += 3
        for kw in sub["keywords"]:
            if kw.lower() in kw_set:
                score += 1
        scores[sub["id"]] = score
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


CONCLUSION_MARKERS = [
    "conclusion", "conclusions", "in conclusion", "in summary",
    "our findings", "we found", "results show", "significantly",
    "primary endpoint", "primary outcome", "this trial", "this study",
    "demonstrated", "reduced", "improved", "superior", "non-inferior",
    "this analysis", "these results",
]


def extract_key_finding(abstract):
    m = re.search(
        r'(?:CONCLUSIONS?|INTERPRETATION|SIGNIFICANCE)[:\s]+(.+?)(?=\s+[A-Z]{3,}:|$)',
        abstract,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        text = m.group(1).strip()[:500]
        if len(text) > 30:
            return text
    sentences = re.split(r'(?<=[.!?])\s+', abstract)
    for sentence in reversed(sentences):
        sl = sentence.lower()
        if any(marker in sl for marker in CONCLUSION_MARKERS):
            s = sentence.strip()
            if 30 < len(s) < 500:
                return s
    for sentence in reversed(sentences):
        s = sentence.strip()
        if 40 < len(s) < 500:
            return s
    return abstract[:300] + "…"


JOURNAL_IF_MAP = {j["display"].lower(): j["if"] for j in JOURNALS}
JOURNAL_IF_MAP.update({j["nlm"].lower(): j["if"] for j in JOURNALS})

def compute_clinical_impact(article):
    base = {1: 75, 2: 55, 3: 60, 4: 30, 5: 20, 6: 10, 7: 5}
    score = base.get(article.get("evidenceRank", 7), 5)
    jname = article.get("journal", "").lower()
    jabbr = article.get("journalAbbr", "").lower()
    journal_if = 0
    for key, ifval in JOURNAL_IF_MAP.items():
        if key in jname or key in jabbr:
            journal_if = ifval
            break
    if journal_if >= 50:
        score += 15
    elif journal_if >= 20:
        score += 8
    elif journal_if >= 7:
        score += 4
    text = (article.get("abstract", "") + " " + article.get("keyFindings", "")).lower()
    if any(p in text for p in ["p<0.0", "p=0.0", "redujo", "reduci", "superior",
                                "non-inferior", "no inferior", "significantly reduced",
                                "significativamente"]):
        score += 8
    if any(p in text for p in ["infra-potenciad", "infraestimad", "underpowered",
                                "no alcanzó significación", "no fue significativ",
                                "not significant", "did not reach significance"]):
        score -= 15
    for pat in ["n=", "(n ="]:
        idx = text.find(pat)
        if idx >= 0:
            num = ""
            for ch in text[idx + len(pat):]:
                if ch.isdigit():
                    num += ch
                elif ch == ",":
                    pass
                else:
                    break
            try:
                n = int(num)
                if n >= 5000:
                    score += 7
                elif n >= 1000:
                    score += 4
                elif n >= 300:
                    score += 2
            except ValueError:
                pass
            break
    score = max(0, min(100, score))
    if score >= 70:
        label = "Alta"
    elif score >= 40:
        label = "Moderada"
    else:
        label = "Baja"
    ev_names = {1: "Meta-análisis", 2: "Revisión sistemática", 3: "RCT",
                4: "Estudio de cohorte", 5: "Caso-control", 6: "Serie de casos", 7: "Estudio"}
    ev_str = ev_names.get(article.get("evidenceRank", 7), "Estudio")
    journal_str = article.get("journal", "revista indexada")
    if label == "Alta":
        rationale = (f"{ev_str} publicado en {journal_str} con resultado positivo "
                     f"en un escenario clínico común — alta probabilidad de modificar la práctica nefrológica.")
    elif label == "Moderada":
        rationale = (f"{ev_str} en {journal_str} — puede influir en la práctica "
                     f"de centros especializados en nefrología o en actualizaciones de guías a medio plazo.")
    else:
        rationale = (f"{ev_str} con evidencia limitada o resultado no concluyente "
                     f"— impacto inmediato en práctica clínica nefrológica reducido.")
    return {"score": score, "label": label, "rationale": rationale}


MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def week_info(today=None):
    today = today or datetime.utcnow()
    start = today - timedelta(days=today.weekday())
    end   = start + timedelta(days=6)
    if start.month == end.month:
        label = f"{start.day} – {end.day} de {MONTHS_ES[start.month]}, {end.year}"
    else:
        label = (f"{start.day} de {MONTHS_ES[start.month]} – "
                 f"{end.day} de {MONTHS_ES[end.month]}, {end.year}")
    week_id = f"{today.year}-W{today.strftime('%W').zfill(2)}"
    return week_id, label


def main():
    parser = argparse.ArgumentParser(description="Fetch weekly nephrology articles")
    parser.add_argument("--days", type=int, default=14,
                        help="Days to look back (default 14 — ensures enough content)")
    args = parser.parse_args()

    today      = datetime.utcnow()
    date_end   = today.strftime("%Y/%m/%d")
    date_start = (today - timedelta(days=args.days)).strftime("%Y/%m/%d")
    week_id, week_label = week_info(today)

    print("=" * 60)
    print("  Briefing Nefrológico — Weekly Article Fetcher")
    print("=" * 60)
    print(f"  Week  : {week_label}")
    print(f"  Range : {date_start} → {date_end}")
    print(f"  Email : {NCBI_EMAIL}")
    print(f"  API   : {'YES' if NCBI_API_KEY else 'no (3 req/s limit)'}")
    print()

    print("[1/4] Searching PubMed…")
    pmids = pubmed_search(date_start, date_end)
    if not pmids:
        print("  No PMIDs found — keeping existing data.")
        sys.exit(0)

    print(f"\n[2/4] Fetching PubMed article details ({len(pmids)} PMIDs)…")
    raw = pubmed_fetch(pmids)
    print(f"  Parsed: {len(raw)} articles with abstracts")

    print("\n[3/4] Querying Europe PMC for additional articles…")
    existing_dois = {a["doi"] for a in raw if not a["doi"].startswith("PMID")}
    epmc_articles = epmc_search(date_start, date_end, existing_dois)
    raw.extend(epmc_articles)
    print(f"  Total pool: {len(raw)} articles")

    print("\n[4/4] Classifying and ranking articles…")
    buckets = {s["id"]: [] for s in SUBSPECIALTIES}

    for art in raw:
        sub_id = classify(art)
        if not sub_id:
            continue
        art["keyFindings"] = extract_key_finding(art["abstract"])
        clean = {
            "id":            f"{sub_id}-{art['pmid'] or art['doi'][:20].replace('/','_')}",
            "title":         art["title"],
            "journal":       art["journal"],
            "authors":       art["authors"],
            "year":          art["year"],
            "doi":           art["doi"],
            "url":           art["url"],
            "evidenceLevel": art["evidenceLevel"],
            "evidenceRank":  art["evidenceRank"],
            "journalRank":   art["journalRank"],
            "abstract":      art["abstract"],
            "keyFindings":   art["keyFindings"],
            "source":        art["source"],
            "clinicalImpact": compute_clinical_impact(art),
        }
        buckets[sub_id].append(clean)

    output_subs = []
    total = 0
    for sub in SUBSPECIALTIES:
        arts = buckets[sub["id"]]
        arts.sort(key=lambda a: (a["evidenceRank"], a["journalRank"], -a["year"]))
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

    all_arts    = [a for s in output_subs for a in s["articles"]]
    n_meta      = sum(1 for a in all_arts if a["evidenceRank"] == 1)
    n_sr        = sum(1 for a in all_arts if a["evidenceRank"] == 2)
    n_rct       = sum(1 for a in all_arts if a["evidenceRank"] == 3)

    output = {
        "week":        week_id,
        "weekLabel":   week_label,
        "lastUpdated": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "total":       total,
            "metaAnalysis": n_meta,
            "systematicReview": n_sr,
            "rct":         n_rct,
        },
        "subspecialties": output_subs,
    }

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)

    out_path = os.path.join(data_dir, "articles.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    archive_name = f"articles-{week_id.replace('/', '-')}.json"
    archive_path = os.path.join(data_dir, archive_name)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    index_path = os.path.join(data_dir, "index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            idx_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        idx_data = {"current": "", "weeks": []}

    new_entry = {"id": week_id, "label": week_label, "file": archive_name}
    existing_ids = [w["id"] for w in idx_data.get("weeks", [])]
    if week_id not in existing_ids:
        idx_data["weeks"].insert(0, new_entry)
        idx_data["weeks"] = idx_data["weeks"][:4]
    else:
        for w in idx_data["weeks"]:
            if w["id"] == week_id:
                w.update(new_entry)
    idx_data["current"] = week_id

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(idx_data, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved {total} articles across {len(output_subs)} subspecialties")
    print(f"  Meta-análisis: {n_meta}  |  Rev. sistemáticas: {n_sr}  |  RCT: {n_rct}")
    print(f"  Output → {out_path}")
    print("=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
