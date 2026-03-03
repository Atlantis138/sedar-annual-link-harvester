# sedar-annual-link-harvester

Batch discover **downloadable direct links** for Canadian issuers' **annual financial statements / annual report** using a symbol list (supports `XXX.V`, `XXX.TO`).

> This tool only outputs links. It does **not** download PDFs.

## Why this approach

`sedarplus.ca` often has anti-bot protections (captcha / shield). In real-world workflows, many issuer IR pages mirror SEDAR filings and expose direct file links (commonly `services.cds.ca/docs_csn/...pdf`).

This script focuses on a stable pipeline:
1. Discover issuer filing pages by symbol via Brave Search HTML pages (no API key).
2. Scrape candidate filing pages.
3. Keep only annual-report-related entries.
4. Export clean CSV links for downstream use.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Input format

CSV with a symbol column (optional `source_page` seed):

```csv
symbol,source_page
NSCI.V,https://nanalysis.investorroom.com/sedar-filings
MDA.V,https://mda-en.investorroom.com/sedar-filings
ABX.TO,
```

## Usage

```bash
python src/sedar_annual_links.py \
  --input examples/symbols.csv \
  --output out_links.csv
```

Optional args:
- `--symbol-col symbol` (default)
- `--max-pages 8` candidate pages per symbol
- `--pause 0.8` request delay in seconds

## Output columns

- `symbol`
- `source_page` (where the link was found)
- `filing_text` (row/context text)
- `filing_date` (best-effort parsed)
- `url` (direct downloadable link)

## Notes

- This is a practical link-harvesting approach for batch work.
- If a symbol returns no result, manually checking issuer IR page and adding custom domain seeds can improve recall.
- For strict, official SEDAR+ API-level integration, a licensed data feed may be required.
