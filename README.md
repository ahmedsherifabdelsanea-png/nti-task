# Scraping & Data Cleaning Pipeline — Version 2 (Pure Python CLI + Tests)

Version 2 re-architects both pipelines as standalone, modular, Pydantic-validated **Python CLI scripts** (`scrape_books.py` and `clean_data.py`) backed by a `pytest` unit/integration test suite—no `.ipynb` notebooks required.

---

## Directory Layout

```text
ver2/
├── README.md
├── requirements.txt
├── tests/
│   ├── test_scrape_books.py            # Offline HTML parser, mojibake & export tests
│   └── test_clean_data.py              # Recovery, quarantine, enrichment & SQLite tests
├── Scraping Books/
│   ├── README.md
│   ├── scrape_books.py                 # Asyncio + aiohttp concurrent scraper CLI
│   ├── books.csv                       # 1,000 Pydantic-validated books (13 columns)
│   └── scraping_summary.json           # Crawl metrics, pricing/rating stats & audit
├── Data Before cleaning/
│   ├── customers_master.csv            # 150 raw customer records
│   ├── products_master.csv             # 30 raw product records
│   ├── previous_days_summary.csv       # 30 daily summary rows (2025-09-01..2025-09-30)
│   ├── orders.csv                      # 1,020 raw October 2025 order lines
│   └── new_daily_batch.csv             # 333 raw November 2025 daily batch lines
└── Data After cleaning/
    ├── README.md
    ├── clean_data.py                   # Modular cleaning, recovery, enrichment & SQLite CLI
    ├── customers_master_cleaned.csv    # 150 cleaned customer rows
    ├── products_master_cleaned.csv     # 30 cleaned product rows + stock alerts
    ├── previous_days_summary_cleaned.csv # 30 validated Sept 2025 summary rows
    ├── orders_cleaned.csv              # 1,011 cleaned & KPI-enriched order rows
    ├── new_daily_batch_cleaned.csv     # 314 cleaned & KPI-enriched batch rows
    ├── all_orders_enriched.csv         # 1,325 combined fact+dimension analytics rows
    ├── daily_metrics_report.csv        # 37 daily KPI rows (2025-10-01..2025-11-07)
    ├── rejected_records.csv            # 28 quarantined rows with rejection reasons
    └── cleaning_summary.csv            # Per-dataset cleaning & financial summary
```

---

## Key Upgrades Over v1

1. **Asyncio + `aiohttp` Scraper (`scrape_books.py`)**: Replaces synchronous/threaded `requests` with `asyncio.Semaphore` throttling, exponential-backoff retries, `urljoin`, UTF-8 byte decoding, and `pydantic` v2 validation (`BookRecord`), crawling all **1,000 books** across **50 categories** in **22.15s** with `0` errors and `0` mojibake hits.
2. **Pydantic-Enforced Cleaning CLI (`clean_data.py`)**: Adds `CustomerSchema`, `ProductSchema`, `DailySummarySchema`, and `CleanOrderSchema` validation, CLI flags (`--input-dir`, `--output-dir`, `--sqlite`, `--strict`), a unified denormalized table (`all_orders_enriched.csv`, **1,325 rows**), and a schema-matched daily rollup (`daily_metrics_report.csv`, **37 days**).
3. **Automated Test Suite (`tests/`)**: `pytest` coverage across HTML parsers, mojibake rejection, UPC deduplication, sibling `Order_ID` recovery, quarantine auditing, numeric imputation, and SQLite export.

---

## Quickstart

From the `ver2/` directory (or passing `ver2/` paths from the repository root):

```bash
# Install dependencies
pip install -r requirements.txt

# Run automated tests
pytest tests -v

# Run async book scraper
python "Scraping Books/scrape_books.py"

# Run data cleaning pipeline (add --sqlite to also export ecommerce_clean.db)
python "Data After cleaning/clean_data.py"
python "Data After cleaning/clean_data.py" --sqlite
```

For schema details and design notes, see:
- [Scraping Books/README.md](Scraping%20Books/README.md)
- [Data After cleaning/README.md](Data%20After%20cleaning/README.md)

