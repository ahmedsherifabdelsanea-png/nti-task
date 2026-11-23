"""Modular CLI data cleaning, validation, quarantine, and analytics pipeline (v2)."""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

ALLOWED_REGIONS = {"Cairo", "Giza", "Alexandria", "Mansoura"}
ALLOWED_SEGMENTS = {"Regular", "Silver", "Gold"}
ALLOWED_CATEGORIES = {"Electronics", "Home", "Beauty", "Fashion", "Sports"}
ALLOWED_STATUSES = {"Pending", "Shipped", "Delivered", "Cancelled"}
ALLOWED_PAYMENTS = {"Cash", "Card", "Wallet", "Online"}
UNSHIPPED_STATUSES = {"Pending", "Cancelled"}
SHIPPED_STATUSES = {"Shipped", "Delivered"}

logger = logging.getLogger("ver2.clean_data")


class CustomerSchema(BaseModel):
    """Schema contract for cleaned customer dimension records."""

    Customer_ID: str = Field(pattern=r"^C\d{4}$")
    Customer_Name: str = Field(min_length=1)
    Region: str
    Customer_Segment: str


class ProductSchema(BaseModel):
    """Schema contract for cleaned product dimension records."""

    Product_ID: str = Field(pattern=r"^P\d{3}$")
    Product_Name: str = Field(min_length=1)
    Category: str
    Unit_Cost: float = Field(ge=0.0)
    Current_Stock: int = Field(ge=0)
    Reorder_Level: int = Field(ge=0)
    Stock_Alert: str
    Units_Below_Reorder: int = Field(ge=0)


class DailySummarySchema(BaseModel):
    """Schema contract for daily quality/sales summary rows."""

    Date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    Record_Count: int = Field(ge=1)
    Total_Orders: int = Field(ge=0)
    Total_Sales: float = Field(ge=0.0)
    Valid_Records_Rate: float = Field(ge=0.0, le=1.0)
    Rejected_Records: int = Field(ge=0)
    Missing_Rate: float = Field(ge=0.0, le=1.0)
    Duplicate_Rate: float = Field(ge=0.0, le=1.0)


class CleanOrderSchema(BaseModel):
    """Schema contract for cleaned order and daily batch line items."""

    Order_ID: str = Field(min_length=1)
    Order_Date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    Ship_Date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    Customer_ID: str = Field(pattern=r"^C\d{4}$")
    Product_ID: str = Field(pattern=r"^P\d{3}$")
    Quantity: int = Field(gt=0)
    Unit_Price: float = Field(gt=0.0)
    Discount: float = Field(ge=0.0, le=1.0)
    Shipping_Cost: float = Field(ge=0.0)
    Order_Status: str
    Payment_Method: str
    Gross_Revenue: float = Field(gt=0.0)
    Net_Revenue: float = Field(ge=0.0)
    Line_Cost: float = Field(gt=0.0)
    Line_Profit: float
    Delivery_Days: Optional[int] = Field(default=None, ge=0)


def validate_table_with_pydantic(df: pd.DataFrame, model_cls: type[BaseModel]) -> None:
    """Validate every row of a DataFrame against a Pydantic v2 schema."""
    work = df.copy()
    for col in work.columns:
        if pd.api.types.is_datetime64_any_dtype(work[col]):
            work[col] = work[col].dt.strftime("%Y-%m-%d")
    for record in work.to_dict(orient="records"):
        normalized = {k: (None if pd.isna(v) else v) for k, v in record.items()}
        model_cls.model_validate(normalized)


def clean_customers(raw_dir: Path, clean_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Clean and validate customers_master.csv."""
    df = pd.read_csv(raw_dir / "customers_master.csv")
    raw_rows = len(df)

    df["Customer_ID"] = df["Customer_ID"].astype(str).str.strip().str.upper()
    df["Customer_Name"] = df["Customer_Name"].astype(str).str.strip()
    df["Region"] = df["Region"].astype(str).str.strip().str.title()
    df["Customer_Segment"] = df["Customer_Segment"].astype(str).str.strip().str.title()

    df = df.drop_duplicates().drop_duplicates(subset=["Customer_ID"], keep="first").reset_index(drop=True)

    assert set(df["Region"]).issubset(ALLOWED_REGIONS), f"Unexpected regions: {set(df['Region']) - ALLOWED_REGIONS}"
    assert set(df["Customer_Segment"]).issubset(ALLOWED_SEGMENTS), "Unexpected segments"
    validate_table_with_pydantic(df, CustomerSchema)

    df.to_csv(clean_dir / "customers_master_cleaned.csv", index=False)
    stats = {
        "dataset": "customers_master",
        "raw_rows": raw_rows,
        "duplicates_removed": raw_rows - len(df),
        "recovered_rows": 0,
        "imputed_rows": 0,
        "ship_date_cleared_rows": 0,
        "ship_date_anomaly_rows": 0,
        "rejected_rows": 0,
        "clean_rows": len(df),
        "net_revenue": np.nan,
        "line_profit": np.nan,
    }
    return df, stats


def clean_products(raw_dir: Path, clean_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Clean, validate, and enrich products_master.csv with stock alert indicators."""
    df = pd.read_csv(raw_dir / "products_master.csv")
    raw_rows = len(df)

    df["Product_ID"] = df["Product_ID"].astype(str).str.strip().str.upper()
    df["Product_Name"] = df["Product_Name"].astype(str).str.strip()
    df["Category"] = df["Category"].astype(str).str.strip().str.title()
    for col in ("Unit_Cost", "Current_Stock", "Reorder_Level"):
        df[col] = pd.to_numeric(df[col], errors="raise")

    df = df.drop_duplicates().drop_duplicates(subset=["Product_ID"], keep="first").reset_index(drop=True)
    assert set(df["Category"]).issubset(ALLOWED_CATEGORIES), f"Unexpected categories: {set(df['Category']) - ALLOWED_CATEGORIES}"

    df["Stock_Alert"] = np.select(
        [df["Current_Stock"] == 0, df["Current_Stock"] < df["Reorder_Level"]],
        ["Out of Stock", "Reorder Needed"],
        default="Healthy",
    )
    df["Units_Below_Reorder"] = (df["Reorder_Level"] - df["Current_Stock"]).clip(lower=0).astype(int)
    validate_table_with_pydantic(df, ProductSchema)

    df.to_csv(clean_dir / "products_master_cleaned.csv", index=False)
    stats = {
        "dataset": "products_master",
        "raw_rows": raw_rows,
        "duplicates_removed": raw_rows - len(df),
        "recovered_rows": 0,
        "imputed_rows": 0,
        "ship_date_cleared_rows": 0,
        "ship_date_anomaly_rows": 0,
        "rejected_rows": 0,
        "clean_rows": len(df),
        "net_revenue": np.nan,
        "line_profit": np.nan,
    }
    return df, stats


def clean_previous_summary(raw_dir: Path, clean_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Clean and validate previous_days_summary.csv."""
    df = pd.read_csv(raw_dir / "previous_days_summary.csv")
    raw_rows = len(df)

    df["Date"] = pd.to_datetime(df["Date"], errors="raise")
    df = df.drop_duplicates().sort_values("Date").reset_index(drop=True)

    assert df["Date"].is_unique, "Duplicate dates in previous_days_summary"
    assert (df["Record_Count"] >= df["Total_Orders"]).all()
    validate_table_with_pydantic(df, DailySummarySchema)

    df.to_csv(clean_dir / "previous_days_summary_cleaned.csv", index=False)
    stats = {
        "dataset": "previous_days_summary",
        "raw_rows": raw_rows,
        "duplicates_removed": raw_rows - len(df),
        "recovered_rows": 0,
        "imputed_rows": 0,
        "ship_date_cleared_rows": 0,
        "ship_date_anomaly_rows": 0,
        "rejected_rows": 0,
        "clean_rows": len(df),
        "net_revenue": round(float(df["Total_Sales"].sum()), 2),
        "line_profit": np.nan,
    }
    return df, stats


def normalize_raw_orders(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize whitespace, casing, 'pcs' quantities, '%' discounts, and dates."""
    out = df.copy()
    out["Original_Row_Number"] = np.arange(2, len(out) + 2)
    out["Raw_Missing_Cells"] = out.drop(columns=["Original_Row_Number"]).isna().sum(axis=1)

    for col in ("Order_ID", "Customer_ID", "Product_ID"):
        out[col] = out[col].where(out[col].notna(), np.nan)
        mask = out[col].notna()
        out.loc[mask, col] = out.loc[mask, col].astype(str).str.strip().str.upper()
        out.loc[out[col] == "", col] = np.nan

    for col in ("Order_Status", "Payment_Method"):
        out[col] = out[col].astype(str).str.strip().str.title()

    qty_clean = out["Quantity"].astype(str).str.replace(r"\s*pcs$", "", case=False, regex=True).str.strip()
    out["Quantity"] = pd.to_numeric(qty_clean, errors="coerce")
    out["Unit_Price"] = pd.to_numeric(out["Unit_Price"], errors="coerce")
    out["Shipping_Cost"] = pd.to_numeric(out["Shipping_Cost"], errors="coerce")

    disc_str = out["Discount"].astype(str).str.strip()
    is_pct = disc_str.str.contains("%", regex=False, na=False)
    out["Discount"] = pd.to_numeric(disc_str.str.replace("%", "", regex=False), errors="coerce")
    out.loc[is_pct, "Discount"] = out.loc[is_pct, "Discount"] / 100.0

    out["Order_Date"] = pd.to_datetime(out["Order_Date"], errors="coerce")
    out["Ship_Date"] = pd.to_datetime(out["Ship_Date"], errors="coerce")
    return out


def build_unambiguous_sibling_map(df: pd.DataFrame, column: str, valid_mask: pd.Series) -> dict:
    """Map Order_ID -> single unique valid value from sibling rows when unambiguous."""
    valid_subset = df.loc[valid_mask, ["Order_ID", column]].dropna()
    grouped = valid_subset.groupby("Order_ID")[column].nunique()
    unambiguous_ids = grouped[grouped == 1].index
    return (
        valid_subset[valid_subset["Order_ID"].isin(unambiguous_ids)]
        .drop_duplicates(subset=["Order_ID"])
        .set_index("Order_ID")[column]
        .to_dict()
    )


def recover_from_siblings(df: pd.DataFrame, valid_customers: set[str]) -> tuple[pd.DataFrame, pd.Series]:
    """Recover missing/corrupted order-level fields from unambiguous sibling Order_ID rows."""
    out = df.copy()
    recovered_tags = pd.Series([[] for _ in range(len(out))], index=out.index, dtype=object)

    cust_valid = out["Customer_ID"].isin(valid_customers)
    cust_map = build_unambiguous_sibling_map(out, "Customer_ID", cust_valid)
    need_cust = (~cust_valid) & out["Order_ID"].isin(cust_map)
    if need_cust.any():
        out.loc[need_cust, "Customer_ID"] = out.loc[need_cust, "Order_ID"].map(cust_map)
        for idx in out.index[need_cust]:
            recovered_tags[idx].append("Customer_ID")

    date_valid = out["Order_Date"].notna()
    date_map = build_unambiguous_sibling_map(out, "Order_Date", date_valid)
    need_date = (~date_valid) & out["Order_ID"].isin(date_map)
    if need_date.any():
        out.loc[need_date, "Order_Date"] = out.loc[need_date, "Order_ID"].map(date_map)
        for idx in out.index[need_date]:
            recovered_tags[idx].append("Order_Date")

    ship_valid = (
        out["Order_Status"].isin(SHIPPED_STATUSES)
        & out["Ship_Date"].notna()
        & out["Order_Date"].notna()
        & (out["Ship_Date"] >= out["Order_Date"])
    )
    ship_map = build_unambiguous_sibling_map(out, "Ship_Date", ship_valid)
    need_ship = out["Order_Status"].isin(SHIPPED_STATUSES) & out["Ship_Date"].isna() & out["Order_ID"].isin(ship_map)
    if need_ship.any():
        candidate_dates = out.loc[need_ship, "Order_ID"].map(ship_map)
        safe = candidate_dates >= out.loc[need_ship, "Order_Date"]
        safe_idx = out.index[need_ship][safe]
        out.loc[safe_idx, "Ship_Date"] = candidate_dates.loc[safe_idx]
        for idx in safe_idx:
            recovered_tags[idx].append("Ship_Date")

    return out, recovered_tags.apply(lambda tags: ", ".join(tags))


def build_rejection_reasons(
    df: pd.DataFrame,
    valid_customers: set[str],
    valid_products: set[str],
) -> pd.Series:
    """Evaluate hard validation rules per row and return semicolon-delimited rejection reasons."""
    checks = [
        (df["Order_ID"].isna(), "Missing Order_ID"),
        (~df["Customer_ID"].isin(valid_customers), "Invalid or missing Customer_ID"),
        (~df["Product_ID"].isin(valid_products), "Invalid Product_ID"),
        (df["Quantity"].isna() | (df["Quantity"] <= 0), "Quantity <= 0 or non-numeric"),
        (df["Discount"].isna() | (df["Discount"] < 0) | (df["Discount"] > 1), "Discount outside [0, 1]"),
        (df["Order_Date"].isna(), "Invalid or missing Order_Date"),
        (df["Order_Date"].notna() & df["Ship_Date"].notna() & (df["Ship_Date"] < df["Order_Date"]), "Ship_Date before Order_Date"),
        (~df["Order_Status"].isin(ALLOWED_STATUSES), "Invalid Order_Status"),
        (~df["Payment_Method"].isin(ALLOWED_PAYMENTS), "Invalid Payment_Method"),
    ]
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    for mask, message in checks:
        for idx in df.index[mask]:
            reasons[idx].append(message)
    return reasons.apply(lambda r: "; ".join(r))


def clean_orders_dataset(
    raw_df: pd.DataFrame,
    source_name: str,
    valid_customers: set[str],
    valid_products: set[str],
    products_df: pd.DataFrame,
    global_price_map: dict[str, float],
    fallback_markup: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Run cleaning, sibling recovery, quarantine, imputation, and KPI enrichment on an order DataFrame."""
    raw_rows = len(raw_df)
    norm_df = normalize_raw_orders(raw_df)

    # Deduplicate exact normalized rows
    feature_cols = [c for c in norm_df.columns if c not in {"Original_Row_Number", "Raw_Missing_Cells"}]
    dup_mask = norm_df.duplicated(subset=feature_cols, keep="first")
    dup_rejected = norm_df.loc[dup_mask].copy()
    dup_rejected["Source_File"] = source_name
    dup_rejected["Rejection_Reason"] = "Exact duplicate row"

    work_df = norm_df.loc[~dup_mask].copy()

    # Sibling recovery (performed on deduplicated working rows so Order_Date like ORD10052 is restored for metrics)
    work_df, recovered_series = recover_from_siblings(work_df, valid_customers)
    work_df["Recovered_Fields"] = recovered_series

    # Propagate recovered Order_Date back into a date-aligned view for accurate daily metrics
    norm_with_dates = norm_df.copy()
    date_lookup = build_unambiguous_sibling_map(work_df, "Order_Date", work_df["Order_Date"].notna())
    missing_norm_date = norm_with_dates["Order_Date"].isna() & norm_with_dates["Order_ID"].isin(date_lookup)
    if missing_norm_date.any():
        norm_with_dates.loc[missing_norm_date, "Order_Date"] = norm_with_dates.loc[missing_norm_date, "Order_ID"].map(date_lookup)
    norm_with_dates["Is_Duplicate"] = dup_mask

    # Hard rule quarantine
    rejection_reasons = build_rejection_reasons(work_df, valid_customers, valid_products)
    invalid_mask = rejection_reasons != ""
    rule_rejected = work_df.loc[invalid_mask].copy()
    rule_rejected["Source_File"] = source_name
    rule_rejected["Rejection_Reason"] = rejection_reasons.loc[invalid_mask]

    valid_df = work_df.loc[~invalid_mask].copy()

    # Key-level duplicate check on (Order_ID, Product_ID)
    key_dup_mask = valid_df.duplicated(subset=["Order_ID", "Product_ID"], keep="first")
    key_rejected = valid_df.loc[key_dup_mask].copy()
    key_rejected["Source_File"] = source_name
    key_rejected["Rejection_Reason"] = "Duplicate (Order_ID, Product_ID) key"

    valid_df = valid_df.loc[~key_dup_mask].copy()

    # Status vs Ship_Date reconciliation
    clear_mask = valid_df["Order_Status"].isin(UNSHIPPED_STATUSES) & valid_df["Ship_Date"].notna()
    valid_df["Ship_Date_Cleared"] = clear_mask
    valid_df.loc[clear_mask, "Ship_Date"] = pd.NaT
    valid_df["Ship_Date_Anomaly"] = valid_df["Order_Status"].isin(SHIPPED_STATUSES) & valid_df["Ship_Date"].isna()

    order_has_cancelled = valid_df.groupby("Order_ID")["Order_Status"].transform(lambda s: (s == "Cancelled").any())
    order_has_active = valid_df.groupby("Order_ID")["Order_Status"].transform(lambda s: (s != "Cancelled").any())
    valid_df["Order_Status_Conflict"] = order_has_cancelled & order_has_active

    # Numeric imputation
    imputed_tags = pd.Series([[] for _ in range(len(valid_df))], index=valid_df.index, dtype=object)
    cost_map = products_df.set_index("Product_ID")["Unit_Cost"].to_dict()
    local_price_map = valid_df.dropna(subset=["Unit_Price"]).groupby("Product_ID")["Unit_Price"].median().to_dict()
    merged_price_map = {**global_price_map, **local_price_map}

    miss_price = valid_df["Unit_Price"].isna()
    if miss_price.any():
        for idx in valid_df.index[miss_price]:
            pid = valid_df.at[idx, "Product_ID"]
            imputed_val = merged_price_map.get(pid, round(cost_map[pid] * fallback_markup, 2))
            valid_df.at[idx, "Unit_Price"] = round(float(imputed_val), 2)
            imputed_tags[idx].append("Unit_Price")

    sibling_ship_cost_map = build_unambiguous_sibling_map(valid_df, "Shipping_Cost", valid_df["Shipping_Cost"].notna())
    dataset_ship_median = round(float(valid_df["Shipping_Cost"].median()), 2)
    miss_ship = valid_df["Shipping_Cost"].isna()
    if miss_ship.any():
        for idx in valid_df.index[miss_ship]:
            oid = valid_df.at[idx, "Order_ID"]
            valid_df.at[idx, "Shipping_Cost"] = round(float(sibling_ship_cost_map.get(oid, dataset_ship_median)), 2)
            imputed_tags[idx].append("Shipping_Cost")

    valid_df["Imputed_Fields"] = imputed_tags.apply(lambda tags: ", ".join(tags))
    valid_df["Is_Imputed"] = valid_df["Imputed_Fields"] != ""

    # Derived KPIs
    valid_df["Quantity"] = valid_df["Quantity"].astype("Int64")
    valid_df["Unit_Price"] = valid_df["Unit_Price"].round(2)
    valid_df["Discount"] = valid_df["Discount"].round(4)
    valid_df["Shipping_Cost"] = valid_df["Shipping_Cost"].round(2)

    unit_cost_series = valid_df["Product_ID"].map(cost_map)
    valid_df["Gross_Revenue"] = (valid_df["Quantity"] * valid_df["Unit_Price"]).round(2)
    valid_df["Net_Revenue"] = (valid_df["Gross_Revenue"] * (1.0 - valid_df["Discount"])).round(2)
    valid_df["Line_Cost"] = (valid_df["Quantity"] * unit_cost_series).round(2)
    valid_df["Line_Profit"] = (valid_df["Net_Revenue"] - valid_df["Line_Cost"]).round(2)
    valid_df["Delivery_Days"] = (valid_df["Ship_Date"] - valid_df["Order_Date"]).dt.days.astype("Int64")

    valid_df = valid_df.drop(columns=["Original_Row_Number", "Raw_Missing_Cells"]).reset_index(drop=True)
    validate_table_with_pydantic(valid_df, CleanOrderSchema)

    rejected_all = pd.concat([dup_rejected, rule_rejected, key_rejected], ignore_index=True)
    if not rejected_all.empty:
        rejected_all = rejected_all.sort_values("Original_Row_Number").reset_index(drop=True)

    stats = {
        "dataset": source_name,
        "raw_rows": raw_rows,
        "duplicates_removed": int(len(dup_rejected) + len(key_rejected)),
        "recovered_rows": int((valid_df["Recovered_Fields"] != "").sum()),
        "imputed_rows": int(valid_df["Is_Imputed"].sum()),
        "ship_date_cleared_rows": int(valid_df["Ship_Date_Cleared"].sum()),
        "ship_date_anomaly_rows": int(valid_df["Ship_Date_Anomaly"].sum()),
        "rejected_rows": int(len(rejected_all)),
        "clean_rows": int(len(valid_df)),
        "net_revenue": round(float(valid_df["Net_Revenue"].sum()), 2),
        "line_profit": round(float(valid_df["Line_Profit"].sum()), 2),
    }
    return valid_df, rejected_all, norm_with_dates, stats


def build_all_orders_enriched(
    orders_clean: pd.DataFrame,
    batch_clean: pd.DataFrame,
    customers_df: pd.DataFrame,
    products_df: pd.DataFrame,
) -> pd.DataFrame:
    """Combine cleaned orders and daily batch with customer & product dimensions."""
    orders_tagged = orders_clean.copy()
    orders_tagged.insert(0, "Source_Dataset", "orders.csv")
    batch_tagged = batch_clean.copy()
    batch_tagged.insert(0, "Source_Dataset", "new_daily_batch.csv")

    combined = pd.concat([orders_tagged, batch_tagged], ignore_index=True)
    enriched = combined.merge(customers_df, on="Customer_ID", how="left").merge(
        products_df[["Product_ID", "Product_Name", "Category", "Unit_Cost", "Stock_Alert"]],
        on="Product_ID",
        how="left",
    )
    enriched = enriched.sort_values(["Order_Date", "Order_ID", "Product_ID"]).reset_index(drop=True)
    return enriched


def build_daily_metrics_report(
    norm_frames: list[pd.DataFrame],
    clean_frames: list[pd.DataFrame],
    rejected_frames: list[pd.DataFrame],
    raw_column_count: int = 11,
) -> pd.DataFrame:
    """Generate a daily summary table matching the previous_days_summary schema."""
    raw_all = pd.concat(norm_frames, ignore_index=True)
    raw_all = raw_all[raw_all["Order_Date"].notna()].copy()

    clean_all = pd.concat(clean_frames, ignore_index=True)
    rej_all = pd.concat([df for df in rejected_frames if not df.empty], ignore_index=True) if any(not df.empty for df in rejected_frames) else pd.DataFrame(columns=raw_all.columns)
    if not rej_all.empty and "Order_Date" in rej_all.columns:
        rej_all = rej_all[rej_all["Order_Date"].notna()].copy()

    raw_daily = raw_all.groupby("Order_Date").agg(
        Record_Count=("Order_ID", "size"),
        Missing_Cells=("Raw_Missing_Cells", "sum"),
        Duplicate_Count=("Is_Duplicate", "sum"),
    )
    clean_daily = clean_all.groupby("Order_Date").agg(
        Clean_Count=("Order_ID", "size"),
        Total_Orders=("Order_ID", "nunique"),
        Total_Sales=("Net_Revenue", "sum"),
    )
    rej_daily = (
        rej_all.groupby("Order_Date").size().rename("Rejected_Records")
        if not rej_all.empty
        else pd.Series(dtype=int, name="Rejected_Records")
    )

    merged = raw_daily.join(clean_daily, how="left").join(rej_daily, how="left").fillna(0).reset_index()
    merged["Record_Count"] = merged["Record_Count"].astype(int)
    merged["Total_Orders"] = merged["Total_Orders"].astype(int)
    merged["Rejected_Records"] = merged["Rejected_Records"].astype(int)
    merged["Total_Sales"] = merged["Total_Sales"].round(2)
    merged["Valid_Records_Rate"] = (merged["Clean_Count"] / merged["Record_Count"]).round(4)
    merged["Missing_Rate"] = (merged["Missing_Cells"] / (merged["Record_Count"] * raw_column_count)).round(4)
    merged["Duplicate_Rate"] = (merged["Duplicate_Count"] / merged["Record_Count"]).round(4)

    merged = merged.rename(columns={"Order_Date": "Date"}).sort_values("Date").reset_index(drop=True)
    cols = [
        "Date", "Record_Count", "Total_Orders", "Total_Sales",
        "Valid_Records_Rate", "Rejected_Records", "Missing_Rate", "Duplicate_Rate",
    ]
    report = merged[cols].copy()
    validate_table_with_pydantic(report, DailySummarySchema)
    return report


def export_sqlite_database(sqlite_path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Export all cleaned and analytical DataFrames to a SQLite database."""
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if sqlite_path.exists():
        sqlite_path.unlink()
    with sqlite3.connect(sqlite_path) as conn:
        for table_name, table_df in tables.items():
            serializable = table_df.copy()
            for col in serializable.columns:
                if pd.api.types.is_datetime64_any_dtype(serializable[col]):
                    serializable[col] = serializable[col].dt.strftime("%Y-%m-%d")
            serializable.to_sql(table_name, conn, index=False, if_exists="replace")
    logger.info("Exported %d tables to SQLite database: %s", len(tables), sqlite_path)


def run_cleaning_pipeline(
    raw_dir: Path,
    clean_dir: Path,
    sqlite_path: Optional[Path] = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Execute the full v2 data cleaning pipeline and export all artifacts."""
    clean_dir.mkdir(parents=True, exist_ok=True)

    customers_df, cust_stats = clean_customers(raw_dir, clean_dir)
    products_df, prod_stats = clean_products(raw_dir, clean_dir)
    prev_summary_df, prev_stats = clean_previous_summary(raw_dir, clean_dir)

    valid_customers = set(customers_df["Customer_ID"])
    valid_products = set(products_df["Product_ID"])

    orders_raw = pd.read_csv(raw_dir / "orders.csv")
    batch_raw = pd.read_csv(raw_dir / "new_daily_batch.csv")

    combined_ref = pd.concat([normalize_raw_orders(orders_raw), normalize_raw_orders(batch_raw)], ignore_index=True)
    combined_ref = combined_ref[combined_ref["Product_ID"].isin(valid_products) & combined_ref["Unit_Price"].notna()]
    global_price_map = combined_ref.groupby("Product_ID")["Unit_Price"].median().to_dict()
    cost_lookup = products_df.set_index("Product_ID")["Unit_Cost"].to_dict()
    fallback_markup = float((combined_ref["Unit_Price"] / combined_ref["Product_ID"].map(cost_lookup)).median())

    orders_clean, orders_rejected, orders_norm, orders_stats = clean_orders_dataset(
        orders_raw, "orders.csv", valid_customers, valid_products, products_df, global_price_map, fallback_markup
    )
    orders_clean.to_csv(clean_dir / "orders_cleaned.csv", index=False)

    batch_clean, batch_rejected, batch_norm, batch_stats = clean_orders_dataset(
        batch_raw, "new_daily_batch.csv", valid_customers, valid_products, products_df, global_price_map, fallback_markup
    )
    batch_clean.to_csv(clean_dir / "new_daily_batch_cleaned.csv", index=False)

    rejected_all = pd.concat([orders_rejected, batch_rejected], ignore_index=True)
    audit_cols = [
        "Source_File", "Original_Row_Number", "Rejection_Reason", "Order_ID",
        "Order_Date", "Ship_Date", "Customer_ID", "Product_ID", "Quantity",
        "Unit_Price", "Discount", "Shipping_Cost", "Order_Status", "Payment_Method",
    ]
    rejected_all = rejected_all[[c for c in audit_cols if c in rejected_all.columns]]
    rejected_all.to_csv(clean_dir / "rejected_records.csv", index=False)

    summary_report_df = pd.DataFrame([cust_stats, prod_stats, prev_stats, orders_stats, batch_stats])
    summary_report_df.to_csv(clean_dir / "cleaning_summary.csv", index=False)

    all_orders_enriched = build_all_orders_enriched(orders_clean, batch_clean, customers_df, products_df)
    all_orders_enriched.to_csv(clean_dir / "all_orders_enriched.csv", index=False)

    daily_metrics_report = build_daily_metrics_report(
        [orders_norm, batch_norm],
        [orders_clean, batch_clean],
        [orders_rejected, batch_rejected],
    )
    daily_metrics_report.to_csv(clean_dir / "daily_metrics_report.csv", index=False)

    if strict and not rejected_all.empty:
        raise ValueError(f"Strict mode enabled and {len(rejected_all)} records were rejected.")

    if sqlite_path is not None:
        export_sqlite_database(
            sqlite_path,
            {
                "customers_master": customers_df,
                "products_master": products_df,
                "previous_days_summary": prev_summary_df,
                "orders_cleaned": orders_clean,
                "new_daily_batch_cleaned": batch_clean,
                "all_orders_enriched": all_orders_enriched,
                "daily_metrics_report": daily_metrics_report,
                "rejected_records": rejected_all,
                "cleaning_summary": summary_report_df,
            },
        )

    return summary_report_df


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    default_clean = Path(__file__).resolve().parent
    default_raw = default_clean.parent / "Data Before cleaning"
    parser = argparse.ArgumentParser(description="Modular data cleaning & quality audit pipeline (v2)")
    parser.add_argument("--input-dir", type=Path, default=default_raw, help="Directory with raw CSVs")
    parser.add_argument("--output-dir", type=Path, default=default_clean, help="Directory for cleaned CSVs & reports")
    parser.add_argument("--sqlite", nargs="?", const="ecommerce_clean.db", default=None, help="Optional SQLite filename or path inside output-dir")
    parser.add_argument("--strict", action="store_true", help="Exit with error if any rows fail validation")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    args = build_arg_parser().parse_args(argv)
    sqlite_path: Optional[Path] = None
    if args.sqlite is not None:
        candidate = Path(args.sqlite)
        sqlite_path = candidate if candidate.is_absolute() else args.output_dir / candidate

    summary = run_cleaning_pipeline(
        raw_dir=args.input_dir,
        clean_dir=args.output_dir,
        sqlite_path=sqlite_path,
        strict=args.strict,
    )
    print("=== Cleaning Summary ===")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
