"""Unit and integration tests for ver2/Data After cleaning/clean_data.py."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "Data After cleaning" / "clean_data.py"
SPEC = importlib.util.spec_from_file_location("clean_data", MODULE_PATH)
assert SPEC and SPEC.loader
clean_data = importlib.util.module_from_spec(SPEC)
sys.modules["clean_data"] = clean_data
SPEC.loader.exec_module(clean_data)


def write_synthetic_dataset(raw_dir: Path) -> None:
    """Create minimal synthetic raw CSVs exercising every cleaning rule."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"Customer_ID": "C0001", "Customer_Name": "Customer_001", "Region": " cairo ", "Customer_Segment": "gold"},
            {"Customer_ID": "C0002", "Customer_Name": "Customer_002", "Region": "GIZA", "Customer_Segment": "Regular"},
        ]
    ).to_csv(raw_dir / "customers_master.csv", index=False)

    pd.DataFrame(
        [
            {"Product_ID": "P001", "Product_Name": "Headphones", "Category": " electronics ", "Unit_Cost": 100, "Current_Stock": 0, "Reorder_Level": 10},
            {"Product_ID": "P002", "Product_Name": "Kettle", "Category": "home", "Unit_Cost": 50, "Current_Stock": 4, "Reorder_Level": 10},
        ]
    ).to_csv(raw_dir / "products_master.csv", index=False)

    pd.DataFrame(
        [
            {
                "Date": "2025-09-01",
                "Record_Count": 10,
                "Total_Orders": 5,
                "Total_Sales": 500.0,
                "Valid_Records_Rate": 0.9,
                "Rejected_Records": 1,
                "Missing_Rate": 0.01,
                "Duplicate_Rate": 0.0,
            }
        ]
    ).to_csv(raw_dir / "previous_days_summary.csv", index=False)

    # orders.csv: sibling Customer_ID + Order_Date recovery, missing Unit_Price imputation, and rejections
    pd.DataFrame(
        [
            {"Order_ID": "ORD1", "Order_Date": "2025-10-01", "Ship_Date": "2025-10-03", "Customer_ID": "C0001", "Product_ID": "P001", "Quantity": "2 pcs", "Unit_Price": 200.0, "Discount": "10%", "Shipping_Cost": 25.0, "Order_Status": " delivered ", "Payment_Method": "wallet"},
            {"Order_ID": "ORD1", "Order_Date": "not-a-date", "Ship_Date": "", "Customer_ID": "", "Product_ID": "P002", "Quantity": 1, "Unit_Price": "unknown", "Discount": 0.0, "Shipping_Cost": 25.0, "Order_Status": "Delivered", "Payment_Method": "Wallet"},
            {"Order_ID": "ORD2", "Order_Date": "2025-10-02", "Ship_Date": "2025-10-01", "Customer_ID": "C0002", "Product_ID": "P002", "Quantity": 1, "Unit_Price": 100.0, "Discount": 0.0, "Shipping_Cost": 20.0, "Order_Status": "Delivered", "Payment_Method": "Cash"},
            {"Order_ID": "ORD3", "Order_Date": "2025-10-02", "Ship_Date": "2025-10-04", "Customer_ID": "C0002", "Product_ID": "PX99", "Quantity": 1, "Unit_Price": 100.0, "Discount": 0.0, "Shipping_Cost": 20.0, "Order_Status": "Delivered", "Payment_Method": "Cash"},
        ]
    ).to_csv(raw_dir / "orders.csv", index=False)

    # new_daily_batch.csv: C5555 recovery vs C6666 rejection, Pending Ship_Date clearing, missing Shipping_Cost
    pd.DataFrame(
        [
            {"Order_ID": "NB1", "Order_Date": "2025-11-01", "Ship_Date": "2025-11-05", "Customer_ID": "C0001", "Product_ID": "P001", "Quantity": 1, "Unit_Price": 200.0, "Discount": 0.0, "Shipping_Cost": 30.0, "Order_Status": "Delivered", "Payment_Method": "Card"},
            {"Order_ID": "NB1", "Order_Date": "2025-11-01", "Ship_Date": "2025-11-05", "Customer_ID": "C5555", "Product_ID": "P002", "Quantity": 2, "Unit_Price": 100.0, "Discount": 0.1, "Shipping_Cost": "", "Order_Status": "Delivered", "Payment_Method": "Card"},
            {"Order_ID": "NB2", "Order_Date": "2025-11-02", "Ship_Date": "2025-11-04", "Customer_ID": "C0002", "Product_ID": "P002", "Quantity": 1, "Unit_Price": 100.0, "Discount": 0.0, "Shipping_Cost": 30.0, "Order_Status": "Pending", "Payment_Method": "Online"},
            {"Order_ID": "NB3", "Order_Date": "2025-11-02", "Ship_Date": "2025-11-04", "Customer_ID": "C6666", "Product_ID": "P002", "Quantity": 1, "Unit_Price": 100.0, "Discount": 0.0, "Shipping_Cost": 30.0, "Order_Status": "Delivered", "Payment_Method": "Online"},
        ]
    ).to_csv(raw_dir / "new_daily_batch.csv", index=False)


def test_clean_pipeline_end_to_end_and_sqlite(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    sqlite_path = clean_dir / "test_clean.db"
    write_synthetic_dataset(raw_dir)

    summary_df = clean_data.run_cleaning_pipeline(raw_dir, clean_dir, sqlite_path=sqlite_path, strict=False)
    assert len(summary_df) == 5

    products_clean = pd.read_csv(clean_dir / "products_master_cleaned.csv")
    assert products_clean.set_index("Product_ID")["Stock_Alert"].to_dict() == {
        "P001": "Out of Stock",
        "P002": "Reorder Needed",
    }

    orders_clean = pd.read_csv(clean_dir / "orders_cleaned.csv")
    assert len(orders_clean) == 2
    ord1_p2 = orders_clean[orders_clean["Product_ID"] == "P002"].iloc[0]
    assert ord1_p2["Customer_ID"] == "C0001"
    assert ord1_p2["Order_Date"] == "2025-10-01"
    assert ord1_p2["Ship_Date"] == "2025-10-03"
    assert bool(ord1_p2["Is_Imputed"]) is True
    assert ord1_p2["Imputed_Fields"] == "Unit_Price"

    batch_clean = pd.read_csv(clean_dir / "new_daily_batch_cleaned.csv")
    assert len(batch_clean) == 3
    nb1_p2 = batch_clean[(batch_clean["Order_ID"] == "NB1") & (batch_clean["Product_ID"] == "P002")].iloc[0]
    assert nb1_p2["Customer_ID"] == "C0001"
    assert nb1_p2["Shipping_Cost"] == 30.0
    assert bool(nb1_p2["Is_Imputed"]) is True

    nb2 = batch_clean[batch_clean["Order_ID"] == "NB2"].iloc[0]
    assert pd.isna(nb2["Ship_Date"])
    assert bool(nb2["Ship_Date_Cleared"]) is True

    rejected = pd.read_csv(clean_dir / "rejected_records.csv")
    assert len(rejected) == 3
    assert set(rejected["Rejection_Reason"]) == {
        "Ship_Date before Order_Date",
        "Invalid Product_ID",
        "Invalid or missing Customer_ID",
    }

    enriched = pd.read_csv(clean_dir / "all_orders_enriched.csv")
    assert len(enriched) == 5
    assert {"Source_Dataset", "Region", "Customer_Segment", "Product_Name", "Stock_Alert"}.issubset(enriched.columns)

    daily = pd.read_csv(clean_dir / "daily_metrics_report.csv")
    assert list(daily["Date"]) == ["2025-10-01", "2025-10-02", "2025-11-01", "2025-11-02"]

    assert sqlite_path.exists()
    with sqlite3.connect(sqlite_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"orders_cleaned", "all_orders_enriched", "daily_metrics_report", "rejected_records"}.issubset(tables)

