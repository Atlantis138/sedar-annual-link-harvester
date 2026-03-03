#!/usr/bin/env python3
"""
Batch find direct downloadable links for SEDAR annual financial statements
from a symbol list (e.g. XXX.V). This script DOES NOT download documents.

Strategy (robust in practice):
1) Discover issuer filing pages via DuckDuckGo HTML search using symbol keywords.
2) Scrape candidate pages (especially investor relations 'sedar-filings' pages).
3) Extract PDF/direct file links and keep annual-report-related rows only.
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Sequence, Tuple
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

ANNUAL_KEYWORDS = [
    "annual financial statements",
    "annual report",
    "audited annual",
    "annual md&a",
    "annual mda",
    "annual statements",
]

NEGATIVE_KEYWORDS = [
    "interim financial statements",
    "interim md&a",
    "news release",
    "material change report",
]

DATE_PATTERNS = [
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
]


@dataclass
class FilingLink:
    symbol: str
    source_page: str
    filing_text: str
    filing_date: str
    url: str


def create_session(timeout: int = 20) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    s.request_timeout = timeout  # type: ignore[attr-defined]
    return s


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        return symbol
    # keep .V / .TO as provided; if user passes TSXV:XXX normalize to XXX.V
    symbol = symbol.replace("TSXV:", "").replace("TSX:", "")
    return symbol


def read_symbols(path: str, col: str = "symbol") -> List[Tuple[str, str]]:
    """Return list of (symbol, source_page_seed). source_page_seed may be empty."""
    symbols: List[Tuple[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if col not in (reader.fieldnames or []):
            raise ValueError(f"CSV must contain column '{col}'. got={reader.fieldnames}")
        has_seed = "source_page" in (reader.fieldnames or [])
        for row in reader:
            sym = normalize_symbol(row.get(col, ""))
            if not sym:
                continue
            seed = (row.get("source_page", "") if has_seed else "").strip()
            symbols.append((sym, seed))
    return symbols


def brave_search_html(session: requests.Session, query: str, limit: int = 15) -> List[str]:
    """Best-effort HTML search without API key."""
    search_url = f"https://search.brave.com/search?q={quote_plus(query)}"
    r = session.get(search_url, timeout=session.request_timeout)  # type: ignore[attr-defined]
    r.raise_for_status()

    hrefs = re.findall(r'href="(https:[^"]+)"', r.text)
    urls: List[str] = []
    seen = set()
    for h in hrefs:
        h = h.strip()
        if not h.startswith("http"):
            continue
        if "search.brave.com" in h or "brave.com/" in h and "mtm_" in h:
            continue
        if h in seen:
            continue
        seen.add(h)
        urls.append(h)
        if len(urls) >= limit:
            break
    return urls


def score_candidate(url: str) -> int:
    u = url.lower()
    score = 0
    if "sedar-filings" in u or "sedar_filings" in u:
        score += 5
    if "investor" in u or "ir." in u or "investorroom" in u:
        score += 2
    if "sedar" in u:
        score += 1
    if "services.cds.ca" in u:
        score += 4
    return score


def discover_candidate_pages(session: requests.Session, symbol: str, max_pages: int = 8) -> List[str]:
    queries = [
        f'"{symbol}" "sedar filings"',
        f'"{symbol}" "annual financial statements"',
        f'"{symbol}" "investor relations"',
    ]
    seen = set()
    candidates: List[str] = []
    for q in queries:
        try:
            results = brave_search_html(session, q, limit=20)
        except Exception:
            continue
        for u in results:
            if u in seen:
                continue
            seen.add(u)
            candidates.append(u)
    candidates.sort(key=score_candidate, reverse=True)
    return candidates[:max_pages]


def extract_date(text: str) -> str:
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""


def looks_annual(text: str) -> bool:
    t = text.lower()
    if not any(k in t for k in ANNUAL_KEYWORDS):
        return False
    if any(k in t for k in NEGATIVE_KEYWORDS):
        return False
    return True


def is_document_url(url: str) -> bool:
    u = url.lower()
    return (
        ".pdf" in u
        or "services.cds.ca/docs_csn" in u
        or "download" in u
    )


def scrape_candidate_page(session: requests.Session, symbol: str, page_url: str) -> List[FilingLink]:
    to_visit = [page_url]

    # Investorroom filing pages are paginated via index.php?o=...
    if "investorroom.com" in page_url:
        try:
            r0 = session.get(page_url, timeout=session.request_timeout)  # type: ignore[attr-defined]
            r0.raise_for_status()
            s0 = BeautifulSoup(r0.text, "html.parser")
            extra = []
            for a in s0.select("a[href]"):
                href = a.get("href", "")
                if "index.php?o=" in href:
                    extra.append(urljoin(page_url, href))
            # keep first few pages for stability/speed
            dedup = []
            seen = set([page_url])
            for u in extra:
                if u not in seen:
                    seen.add(u)
                    dedup.append(u)
            to_visit.extend(dedup[:6])
        except Exception:
            pass

    rows: List[FilingLink] = []
    for visit_url in to_visit:
        try:
            r = session.get(visit_url, timeout=session.request_timeout)  # type: ignore[attr-defined]
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            continue

        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if not href:
                continue
            doc_url = urljoin(visit_url, href)
            if not is_document_url(doc_url):
                continue

            tr = a.find_parent("tr")
            context = tr.get_text(" ", strip=True) if tr else a.get_text(" ", strip=True)
            context = re.sub(r"\s+", " ", context).strip()

            if not looks_annual(context):
                continue

            rows.append(
                FilingLink(
                    symbol=symbol,
                    source_page=visit_url,
                    filing_text=context[:300],
                    filing_date=extract_date(context),
                    url=doc_url,
                )
            )

    uniq = {}
    for row in rows:
        uniq[row.url] = row
    return list(uniq.values())


def collect_for_symbol(
    session: requests.Session,
    symbol: str,
    seed_page: str = "",
    max_pages: int = 8,
    pause_sec: float = 0.8,
) -> List[FilingLink]:
    if seed_page:
        pages = [seed_page]
    else:
        pages = discover_candidate_pages(session, symbol, max_pages=max_pages)
    results: List[FilingLink] = []

    for p in pages:
        try:
            links = scrape_candidate_page(session, symbol, p)
            if links:
                results.extend(links)
        except Exception:
            pass
        time.sleep(pause_sec)

    # Prefer newest-looking entries first (date text may be empty)
    return results


def write_output(path: str, rows: Sequence[FilingLink]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "source_page", "filing_text", "filing_date", "url"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batch discover SEDAR annual filing direct links (no downloading)."
    )
    ap.add_argument("--input", required=True, help="Input CSV path")
    ap.add_argument("--output", required=True, help="Output CSV path")
    ap.add_argument("--symbol-col", default="symbol", help="Input column name (default: symbol)")
    ap.add_argument("--max-pages", type=int, default=8, help="Candidate pages per symbol")
    ap.add_argument("--pause", type=float, default=0.8, help="Delay between page requests")
    args = ap.parse_args()

    symbols = read_symbols(args.input, col=args.symbol_col)
    sess = create_session()

    all_rows: List[FilingLink] = []
    for i, (sym, seed_page) in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] {sym} ...")
        rows = collect_for_symbol(
            sess,
            sym,
            seed_page=seed_page,
            max_pages=args.max_pages,
            pause_sec=args.pause,
        )
        if rows:
            print(f"  -> {len(rows)} annual link(s)")
            all_rows.extend(rows)
        else:
            print("  -> no annual links found")

    write_output(args.output, all_rows)
    print(f"Done. wrote {len(all_rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
