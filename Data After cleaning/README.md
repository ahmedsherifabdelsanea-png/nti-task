# Data Cleaning & Analytics Pipeline (v2 — Python CLI)

## Overview

`clean_data.py` processes all five raw CSV datasets from `../Data Before cleaning/`, applies Pydantic-validated cleaning, unambiguous sibling `Order_ID` recovery, status/date reconciliation, transparent numeric imputation, and quarantine auditing, and generates both per-table cleaned CSVs and unified analytics tables inside `Data After cleaning/`.

---

## Pipeline Capabilities

1. **Normalization & Unit Parsing**: Trims whitespace, standardizes casing (`Region`, `Category`, `Order_Status`, `Payment_Method`), strips `pcs` from `Quantity`, converts `%` discounts to decimals, and parses dates safely.
2. **Sibling `Order_ID` Recovery (`recover_from_siblings`)**: Restores missing/corrupted `Customer_ID` (e.g., blank or `C5555`/`C6666` on partial lines), invalid `Order_Date` (`ORD10052`), and missing `Ship_Date` on `Shipped`/`Delivered` rows when sibling rows agree on one valid value (`Recovered_Fields`).
3. **Quarantine Audit (`rejected_records.csv`)**: Preserves every invalid or duplicate row (`28` total) with `Source_File`, `Original_Row_Number`, and `Rejection_Reason` (`Invalid Product_ID`, `Invalid or missing Customer_ID`, `Quantity <= 0 or non-numeric`, `Discount outside [0, 1]`, `Ship_Date before Order_Date`, `Exact duplicate row`).
4. **Status vs. `Ship_Date` Reconciliation**: Clears bogus `Ship_Date` values on `Pending`/`Cancelled` orders (`Ship_Date_Cleared`, `60` rows in `new_daily_batch.csv`), flags remaining `Shipped`/`Delivered` lines without `Ship_Date` (`Ship_Date_Anomaly`), and flags mixed-status orders (`Order_Status_Conflict`).
5. **Transparent Numeric Imputation**: Fills missing `Unit_Price` via product median valid price (fallback to `Unit_Cost` markup) and missing `Shipping_Cost` via sibling `Order_ID` or dataset median (`Is_Imputed`, `Imputed_Fields`).
6. **Pydantic Schema Contracts & Optional SQLite**: Validates rows with `CustomerSchema`, `ProductSchema`, `DailySummarySchema`, and `CleanOrderSchema`, and optionally exports all 9 tables to SQLite (`--sqlite`).

---

## CLI Usage

```bash
python clean_data.py --help
python clean_data.py
python clean_data.py --sqlite ecommerce_clean.db
python clean_data.py --strict
```

### Options
- `--input-dir`: Raw CSV folder (default: `../Data Before cleaning`)
- `--output-dir`: Output folder for cleaned CSVs & reports (default: script directory)
- `--sqlite [PATH]`: Export all cleaned and analytical tables to a SQLite database (defaults to `ecommerce_clean.db` inside `--output-dir` when flag is passed without value)
- `--strict`: Exit with an error if any row fails validation

---

## Outputs & Exact Run Results

| Output File | Rows | Description |
|---|---:|---|
| `customers_master_cleaned.csv` | 150 | Normalized & schema-validated customers |
| `products_master_cleaned.csv` | 30 | Normalized products + `Stock_Alert` & `Units_Below_Reorder` |
| `previous_days_summary_cleaned.csv` | 30 | Validated Sept 2025 summary (`Total_Sales = 8,418,231.21`) |
| `orders_cleaned.csv` | 1,011 | Cleaned Oct 2025 orders (`Net_Revenue = 1,055,953.26`, `Line_Profit = 273,418.26`) |
| `new_daily_batch_cleaned.csv` | 314 | Cleaned Nov 2025 batch (`Net_Revenue = 317,174.08`, `Line_Profit = 106,624.08`) |
| `all_orders_enriched.csv` | 1,325 | Unified fact + customer/product dimension table (`30` columns) |
| `daily_metrics_report.csv` | 37 | Daily rollup (`2025-10-01`..`2025-11-07`) matching `previous_days_summary` schema |
| `rejected_records.csv` | 28 | Quarantined invalid/duplicate rows (`9` from `orders.csv`, `19` from `new_daily_batch.csv`) |
| `cleaning_summary.csv` | 5 | Per-dataset audit counts and financial totals |

