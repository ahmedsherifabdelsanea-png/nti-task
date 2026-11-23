# Scraping Books (v2 — Asyncio + aiohttp CLI)

## Overview

`scrape_books.py` is a pure-Python asynchronous CLI scraper for [books.toscrape.com](https://books.toscrape.com/). It discovers all **50 leaf categories**, follows every paginated listing page, fetches all **1,000 book detail pages** concurrently with `aiohttp`, validates every record through a Pydantic v2 schema (`BookRecord`), and exports `books.csv` alongside `scraping_summary.json`.

---

## Architecture & Improvements

- **Async Concurrency**: Uses `asyncio.gather` + `asyncio.Semaphore` and pooled `aiohttp.TCPConnector` connections (`--concurrency`, default `16`).
- **Transient Error Resilience**: Custom `fetch_bytes()` retry loop with exponential backoff on network errors, timeouts, and HTTP `429`/`500`/`502`/`503`/`504`.
- **Zero Mojibake Encoding**: Decodes raw `bytes` via `BeautifulSoup(raw_bytes, "lxml", from_encoding="utf-8")` and enforces a Pydantic validator checking against Latin-1/UTF-8 corruption sequences (`Â£`, `â€™`, `â€œ`, `Ã©`, etc.).
- **Pure Parsers for Testability**: `parse_categories_html()`, `parse_listing_page_html()`, and `parse_book_detail_html()` are isolated from HTTP I/O for deterministic offline `pytest` verification.

---

## CLI Usage

```bash
python scrape_books.py --help
python scrape_books.py --concurrency 16 --timeout 15 --retries 4
```

### Options
- `--base-url`: Target root URL (default: `https://books.toscrape.com/`)
- `--output-dir`: Directory for `books.csv` and `scraping_summary.json` (default: script directory)
- `--concurrency`: Maximum concurrent HTTP requests (default: `16`)
- `--timeout`: Total per-request timeout in seconds (default: `15.0`)
- `--retries`: Retry attempts for transient failures (default: `4`)

---

## Output Schema (`books.csv` — 1,000 rows)

| Column | Type | Description |
|---|---|---|
| `UPC` | `str` | 16-character product identifier |
| `Title` | `str` | Full book title from detail page `<h1>` |
| `Category` | `str` | Category name (50 categories) |
| `Rating` | `int` | Star rating (`1`–`5`) |
| `Price_Excl_Tax` | `float` | Price excluding tax (GBP) |
| `Price_Incl_Tax` | `float` | Price including tax (GBP) |
| `Tax` | `float` | Tax amount (GBP) |
| `In_Stock` | `bool` | Stock availability flag |
| `Stock_Quantity` | `int` | Exact units available |
| `Num_Reviews` | `int` | Customer review count |
| `Description` | `str` | Product description (`""` if absent) |
| `Product_URL` | `str` | Canonical book detail URL |
| `Image_URL` | `str` | Absolute cover image URL |

---

## Latest Verified Run (`scraping_summary.json`)

- **Runtime**: `22.15s` (`0` errors)
- **Books Exported**: `1,000` unique `UPC`s across `50` categories (`1,000` Pydantic-validated)
- **Total Stock**: `8,585` units (`1,000` in stock)
- **Price (incl. tax, GBP)**: Min `10.00`, Median `35.98`, Mean `35.07`, Max `59.99`
- **Ratings**: `1★: 226`, `2★: 196`, `3★: 203`, `4★: 179`, `5★: 196`
- **Checks**: `mojibake_hits: 0`, `missing_upc: 0`, `invalid_ratings: 0`, `invalid_prices: 0`, `scrape_error_count: 0`

