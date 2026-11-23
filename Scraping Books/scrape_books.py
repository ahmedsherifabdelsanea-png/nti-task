"""Asyncio + aiohttp concurrent web scraper for books.toscrape.com (v2)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE_URL = "https://books.toscrape.com/"
DEFAULT_CONCURRENCY = 16
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 4
USER_AGENT = "NTI-BookScraper/2.0-async"

RATING_MAP = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
}
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MOJIBAKE_TOKENS = ("Â£", "â€™", "â€œ", "â€ ", "â€”", "Ã©", "Ã¡", "Ã±")

logger = logging.getLogger("ver2.scrape_books")


@dataclass(frozen=True)
class ListingStub:
    """Lightweight book stub collected from category listing pages."""

    category: str
    listing_title: str
    listing_rating: Optional[int]
    product_url: str


class BookRecord(BaseModel):
    """Pydantic-validated book record extracted from detail pages."""

    model_config = ConfigDict(str_strip_whitespace=True)

    UPC: str = Field(min_length=1)
    Title: str = Field(min_length=1)
    Category: str = Field(min_length=1)
    Rating: int = Field(ge=1, le=5)
    Price_Excl_Tax: float = Field(gt=0.0)
    Price_Incl_Tax: float = Field(gt=0.0)
    Tax: float = Field(ge=0.0)
    In_Stock: bool
    Stock_Quantity: int = Field(ge=0)
    Num_Reviews: int = Field(ge=0)
    Description: str = ""
    Product_URL: str
    Image_URL: str

    @field_validator("Title", "Category", "Description")
    @classmethod
    def check_no_mojibake(cls, value: str) -> str:
        """Reject corrupted Latin-1/UTF-8 mojibake sequences."""
        for token in MOJIBAKE_TOKENS:
            if token in value:
                raise ValueError(f"Detected mojibake sequence {token!r} in text")
        return value

    @field_validator("Product_URL", "Image_URL")
    @classmethod
    def check_http_url(cls, value: str) -> str:
        """Verify absolute HTTP(S) URLs."""
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"Expected absolute HTTP URL, got {value!r}")
        return value


def parse_money(raw: str) -> Optional[float]:
    """Extract a numeric GBP value from strings like '£51.77'."""
    match = re.search(r"\d+(?:\.\d+)?", raw.replace(",", ""))
    return round(float(match.group()), 2) if match else None


def parse_rating(tag) -> Optional[int]:
    """Map BeautifulSoup star-rating CSS class to integer 1..5."""
    if tag is None:
        return None
    for cls in tag.get("class", []):
        if cls in RATING_MAP:
            return RATING_MAP[cls]
    return None


def parse_availability(raw: str) -> tuple[bool, int]:
    """Return (in_stock_bool, stock_quantity) from text like 'In stock (22 available)'."""
    normalized = " ".join(raw.split())
    in_stock = "in stock" in normalized.lower()
    qty_match = re.search(r"(\d+)\s+available", normalized, flags=re.IGNORECASE)
    quantity = int(qty_match.group(1)) if qty_match else (1 if in_stock else 0)
    return in_stock, quantity


def make_soup(raw_bytes: bytes) -> BeautifulSoup:
    """Parse raw UTF-8 HTML bytes with BeautifulSoup + lxml."""
    return BeautifulSoup(raw_bytes, "lxml", from_encoding="utf-8")


def parse_categories_html(raw_bytes: bytes, base_url: str) -> list[tuple[str, str]]:
    """Extract all leaf (category_name, absolute_url) pairs from the homepage HTML."""
    soup = make_soup(raw_bytes)
    links = soup.select("div.side_categories ul li ul li a")
    return [(" ".join(link.text.split()), urljoin(base_url, link["href"])) for link in links]


def parse_listing_page_html(
    raw_bytes: bytes,
    category_name: str,
    page_url: str,
) -> tuple[list[ListingStub], Optional[str]]:
    """Parse one category listing page, returning book stubs and optional next-page URL."""
    soup = make_soup(raw_bytes)
    stubs: list[ListingStub] = []
    for card in soup.select("article.product_pod"):
        link_tag = card.select_one("h3 a")
        if link_tag is None or not link_tag.get("href"):
            continue
        title = (link_tag.get("title") or " ".join(link_tag.text.split())).strip()
        product_url = urljoin(page_url, link_tag["href"])
        rating = parse_rating(card.select_one("p.star-rating"))
        stubs.append(ListingStub(category_name, title, rating, product_url))

    next_tag = soup.select_one("li.next a")
    next_url = urljoin(page_url, next_tag["href"]) if next_tag and next_tag.get("href") else None
    return stubs, next_url


def parse_book_detail_html(raw_bytes: bytes, stub: ListingStub) -> BookRecord:
    """Parse and Pydantic-validate a book's detail page HTML."""
    soup = make_soup(raw_bytes)

    title_tag = soup.select_one("div.product_main h1")
    title = " ".join(title_tag.text.split()) if title_tag else stub.listing_title

    rating = parse_rating(soup.select_one("div.product_main p.star-rating")) or stub.listing_rating
    if rating is None:
        raise ValueError(f"Missing star rating for {stub.product_url}")

    desc_tag = soup.select_one("#product_description + p")
    description = " ".join(desc_tag.text.split()) if desc_tag else ""

    img_tag = soup.select_one("div.item.active img") or soup.select_one("div#product_gallery img")
    image_url = urljoin(stub.product_url, img_tag["src"]) if img_tag and img_tag.get("src") else ""

    info_table: dict[str, str] = {}
    for row in soup.select("table.table.table-striped tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        if th and td:
            info_table[" ".join(th.text.split())] = " ".join(td.text.split())

    price_excl = parse_money(info_table.get("Price (excl. tax)", ""))
    price_incl = parse_money(info_table.get("Price (incl. tax)", ""))
    tax = parse_money(info_table.get("Tax", ""))
    if price_excl is None or price_incl is None or tax is None:
        raise ValueError(f"Missing price fields for {stub.product_url}")

    in_stock, stock_qty = parse_availability(info_table.get("Availability", ""))
    reviews_raw = info_table.get("Number of reviews", "0")
    num_reviews = int(reviews_raw) if reviews_raw.isdigit() else 0

    return BookRecord(
        UPC=info_table.get("UPC", ""),
        Title=title,
        Category=stub.category,
        Rating=rating,
        Price_Excl_Tax=price_excl,
        Price_Incl_Tax=price_incl,
        Tax=tax,
        In_Stock=in_stock,
        Stock_Quantity=stock_qty,
        Num_Reviews=num_reviews,
        Description=description,
        Product_URL=stub.product_url,
        Image_URL=image_url,
    )


async def fetch_bytes(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
    retries: int,
) -> bytes:
    """Fetch raw bytes from URL with semaphore throttling and exponential-backoff retries."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            async with semaphore:
                async with session.get(url) as response:
                    if response.status in RETRYABLE_STATUS_CODES and attempt < retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    response.raise_for_status()
                    return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"Failed fetching {url} after {retries + 1} attempts: {last_exc}") from last_exc


async def crawl_category(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    category_name: str,
    start_url: str,
    retries: int,
) -> list[ListingStub]:
    """Follow all paginated pages for one category."""
    all_stubs: list[ListingStub] = []
    current_url: Optional[str] = start_url
    while current_url:
        raw_bytes = await fetch_bytes(session, semaphore, current_url, retries)
        page_stubs, current_url = parse_listing_page_html(raw_bytes, category_name, current_url)
        all_stubs.extend(page_stubs)
    return all_stubs


async def scrape_catalog_async(
    base_url: str,
    concurrency: int,
    timeout_sec: float,
    retries: int,
) -> tuple[list[BookRecord], list[dict]]:
    """Run full asynchronous crawl across categories, listing pages, and book detail pages."""
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    headers = {"User-Agent": USER_AGENT}
    errors: list[dict] = []

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        homepage_bytes = await fetch_bytes(session, semaphore, base_url, retries)
        categories = parse_categories_html(homepage_bytes, base_url)
        logger.info("Discovered %d categories", len(categories))

        cat_tasks = [
            crawl_category(session, semaphore, cat_name, cat_url, retries)
            for cat_name, cat_url in categories
        ]
        cat_results = await asyncio.gather(*cat_tasks, return_exceptions=True)

        stubs: list[ListingStub] = []
        for (cat_name, cat_url), result in zip(categories, cat_results):
            if isinstance(result, Exception):
                errors.append({"stage": "category_crawl", "category": cat_name, "url": cat_url, "error": str(result)})
                logger.error("Category crawl failed for %s: %s", cat_name, result)
            else:
                stubs.extend(result)

        logger.info("Collected %d listing stubs across %d categories", len(stubs), len(categories))

        async def fetch_and_parse(stub: ListingStub) -> BookRecord:
            raw = await fetch_bytes(session, semaphore, stub.product_url, retries)
            return parse_book_detail_html(raw, stub)

        detail_tasks = [fetch_and_parse(stub) for stub in stubs]
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        records: list[BookRecord] = []
        for stub, result in zip(stubs, detail_results):
            if isinstance(result, Exception):
                errors.append({"stage": "detail_scrape", "category": stub.category, "url": stub.product_url, "error": str(result)})
                logger.error("Detail scrape failed for %s: %s", stub.product_url, result)
            else:
                records.append(result)

    return records, errors


def validate_and_export(
    records: list[BookRecord],
    errors: list[dict],
    output_dir: Path,
    base_url: str,
    elapsed_seconds: float,
) -> dict:
    """Deduplicate validated records by UPC, export UTF-8 CSV and JSON audit summary."""
    if not records:
        raise RuntimeError("Scraper produced zero validated books; aborting export.")

    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([rec.model_dump() for rec in records])
    raw_count = len(df)

    df = df.sort_values(["Category", "Title", "UPC"]).reset_index(drop=True)
    df = df.drop_duplicates(subset=["UPC"], keep="first").reset_index(drop=True)

    mojibake_hits = 0
    for col in ("Title", "Category", "Description"):
        series = df[col].fillna("").astype(str)
        for token in MOJIBAKE_TOKENS:
            mojibake_hits += int(series.str.contains(token, regex=False).sum())

    csv_path = output_dir / "books.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)

    summary = {
        "version": "ver2-async",
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": base_url,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "raw_rows_scraped": raw_count,
        "unique_books_exported": len(df),
        "categories_count": int(df["Category"].nunique()),
        "in_stock_count": int(df["In_Stock"].sum()),
        "total_stock_units": int(df["Stock_Quantity"].sum()),
        "books_without_description": int((df["Description"].str.len() == 0).sum()),
        "duplicate_titles_with_distinct_upc": int(df["Title"].duplicated(keep=False).sum()),
        "price_incl_tax_min_gbp": float(df["Price_Incl_Tax"].min()),
        "price_incl_tax_median_gbp": round(float(df["Price_Incl_Tax"].median()), 2),
        "price_incl_tax_mean_gbp": round(float(df["Price_Incl_Tax"].mean()), 2),
        "price_incl_tax_max_gbp": float(df["Price_Incl_Tax"].max()),
        "rating_distribution": {str(k): int(v) for k, v in df["Rating"].value_counts().sort_index().items()},
        "validation_checks": {
            "pydantic_validated": len(df),
            "mojibake_hits": mojibake_hits,
            "missing_upc": int((df["UPC"].str.len() == 0).sum()),
            "invalid_ratings": int((~df["Rating"].isin([1, 2, 3, 4, 5])).sum()),
            "invalid_prices": int(((df["Price_Incl_Tax"] < df["Price_Excl_Tax"]) | (df["Price_Excl_Tax"] <= 0)).sum()),
            "scrape_error_count": len(errors),
        },
        "errors": errors,
    }

    summary_path = output_dir / "scraping_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved %d books to %s and audit to %s", len(df), csv_path, summary_path)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    default_out = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Asyncio + aiohttp concurrent scraper for books.toscrape.com")
    parser.add_argument("--base-url", default=BASE_URL, help="Target root URL (default: %(default)s)")
    parser.add_argument("--output-dir", type=Path, default=default_out, help="Directory for books.csv and scraping_summary.json")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Max concurrent HTTP requests")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry attempts for transient failures")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    args = build_arg_parser().parse_args(argv)
    start = time.perf_counter()
    records, errors = asyncio.run(
        scrape_catalog_async(
            base_url=args.base_url,
            concurrency=args.concurrency,
            timeout_sec=args.timeout,
            retries=args.retries,
        )
    )
    elapsed = time.perf_counter() - start
    summary = validate_and_export(records, errors, args.output_dir, args.base_url, elapsed)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

