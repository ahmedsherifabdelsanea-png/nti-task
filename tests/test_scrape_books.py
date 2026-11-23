"""Unit tests for ver2/Scraping Books/scrape_books.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

MODULE_PATH = Path(__file__).resolve().parents[1] / "Scraping Books" / "scrape_books.py"
SPEC = importlib.util.spec_from_file_location("scrape_books", MODULE_PATH)
assert SPEC and SPEC.loader
scrape_books = importlib.util.module_from_spec(SPEC)
sys.modules["scrape_books"] = scrape_books
SPEC.loader.exec_module(scrape_books)


def test_parse_helpers() -> None:
    assert scrape_books.parse_money("£51.77") == 51.77
    assert scrape_books.parse_money("£1,234.50") == 1234.50
    assert scrape_books.parse_money("unknown") is None

    assert scrape_books.parse_availability("In stock (22 available)") == (True, 22)
    assert scrape_books.parse_availability("In stock") == (True, 1)
    assert scrape_books.parse_availability("Out of stock") == (False, 0)


def test_parse_categories_and_pagination_utf8() -> None:
    home_html = """
    <div class="side_categories"><ul><li>Books<ul>
        <li><a href="catalogue/category/books/travel_2/index.html"> Travel </a></li>
        <li><a href="catalogue/category/books/mystery_3/index.html"> Mystery </a></li>
    </ul></li></ul></div>
    """.encode("utf-8")

    cats = scrape_books.parse_categories_html(home_html, "https://books.toscrape.com/")
    assert cats == [
        ("Travel", "https://books.toscrape.com/catalogue/category/books/travel_2/index.html"),
        ("Mystery", "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html"),
    ]

    listing_html = """
    <article class="product_pod">
        <p class="star-rating Four"></p>
        <h3><a href="../../../full-moon-over-noahs-ark_1/index.html" title="Full Moon over Noah’s Ark">Full Moon...</a></h3>
    </article>
    <ul class="pager"><li class="next"><a href="page-2.html">next</a></li></ul>
    """.encode("utf-8")

    stubs, next_url = scrape_books.parse_listing_page_html(
        listing_html,
        "Travel",
        "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
    )
    assert len(stubs) == 1
    assert stubs[0].listing_title == "Full Moon over Noah’s Ark"
    assert stubs[0].listing_rating == 4
    assert stubs[0].product_url == "https://books.toscrape.com/catalogue/full-moon-over-noahs-ark_1/index.html"
    assert next_url == "https://books.toscrape.com/catalogue/category/books/travel_2/page-2.html"


def test_parse_book_detail_and_mojibake_rejection() -> None:
    detail_html = """
    <html><body>
      <div class="product_main">
        <h1>At The Existentialist Café</h1>
        <p class="star-rating Five"></p>
      </div>
      <div id="product_gallery"><div class="item active"><img src="../../media/cache/cafe.jpg" /></div></div>
      <div id="product_description"></div><p>A crisp UTF-8 summary — with dashes.</p>
      <table class="table table-striped">
        <tr><th>UPC</th><td>a1b2c3d4e5f67890</td></tr>
        <tr><th>Price (excl. tax)</th><td>£29.93</td></tr>
        <tr><th>Price (incl. tax)</th><td>£29.93</td></tr>
        <tr><th>Tax</th><td>£0.00</td></tr>
        <tr><th>Availability</th><td>In stock (14 available)</td></tr>
        <tr><th>Number of reviews</th><td>0</td></tr>
      </table>
    </body></html>
    """.encode("utf-8")

    stub = scrape_books.ListingStub(
        category="Philosophy",
        listing_title="At The Existentialist Café",
        listing_rating=5,
        product_url="https://books.toscrape.com/catalogue/cafe_1/index.html",
    )
    record = scrape_books.parse_book_detail_html(detail_html, stub)
    assert record.Title == "At The Existentialist Café"
    assert record.Stock_Quantity == 14
    assert record.Price_Incl_Tax == 29.93
    assert record.Image_URL == "https://books.toscrape.com/media/cache/cafe.jpg"

    with pytest.raises(ValidationError):
        scrape_books.BookRecord(
            UPC="bad123",
            Title="Corrupted Noahâ€™s Title",
            Category="Travel",
            Rating=3,
            Price_Excl_Tax=10.0,
            Price_Incl_Tax=10.0,
            Tax=0.0,
            In_Stock=True,
            Stock_Quantity=2,
            Num_Reviews=0,
            Description="",
            Product_URL="https://books.toscrape.com/book/index.html",
            Image_URL="https://books.toscrape.com/media/img.jpg",
        )


def test_validate_and_export_deduplicates_upc(tmp_path: Path) -> None:
    rec1 = scrape_books.BookRecord(
        UPC="1111",
        Title="Book A",
        Category="Travel",
        Rating=4,
        Price_Excl_Tax=20.0,
        Price_Incl_Tax=20.0,
        Tax=0.0,
        In_Stock=True,
        Stock_Quantity=5,
        Num_Reviews=0,
        Description="Desc",
        Product_URL="https://books.toscrape.com/a/index.html",
        Image_URL="https://books.toscrape.com/a.jpg",
    )
    rec1_dup = rec1.model_copy()
    rec2 = rec1.model_copy(update={"UPC": "2222", "Title": "Book A", "Price_Incl_Tax": 25.0, "Price_Excl_Tax": 25.0})

    summary = scrape_books.validate_and_export([rec1, rec1_dup, rec2], [], tmp_path, "https://books.toscrape.com/", 1.2)
    assert summary["raw_rows_scraped"] == 3
    assert summary["unique_books_exported"] == 2
    assert summary["duplicate_titles_with_distinct_upc"] == 2
    assert summary["validation_checks"]["mojibake_hits"] == 0

    exported_df = pd.read_csv(tmp_path / "books.csv")
    assert len(exported_df) == 2
    saved_summary = json.loads((tmp_path / "scraping_summary.json").read_text(encoding="utf-8"))
    assert saved_summary["unique_books_exported"] == 2

