"""T066: the data mapping of notebooks/external_validation.py (no download, no model fit)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[2] / "notebooks" / "external_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("external_validation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoices() -> pd.DataFrame:
    rows = [
        ("1", "85048", 2, "2010-01-04 10:00", 5.0),    # 10.0 counted
        ("1", "79323P", 1, "2010-01-04 11:00", 2.5),   # 2.5 counted, same day
        ("C2", "85048", -1, "2010-01-04 12:00", 5.0),  # cancellation: dropped
        ("3", "POST", 1, "2010-01-04 12:30", 18.0),    # postage: not a product
        ("4", "21000", 3, "2010-01-06 09:00", 1.0),    # 3.0 counted
        ("5", "21000", 1, "2010-01-06 09:30", 0.0),    # free: dropped
    ]
    df = pd.DataFrame(rows, columns=["Invoice", "StockCode", "Quantity", "InvoiceDate", "Price"])
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])
    return pd.concat([df, df.iloc[[0]]], ignore_index=True)  # one exact duplicate (overlapping sheets)


def test_daily_revenue_keeps_product_sales_only_and_drops_duplicates():
    revenue = _load().daily_revenue(_invoices())
    assert revenue.to_dict() == {pd.Timestamp("2010-01-04"): 12.5, pd.Timestamp("2010-01-06"): 3.0}


def test_to_transactions_has_the_harness_columns_and_revenue_category():
    ev = _load()
    tx = ev.to_transactions(ev.daily_revenue(_invoices()))
    assert list(tx.columns) == ["booking_date", "amount", "category", "team_id", "counterparty", "invoice_id"]
    assert (tx["category"] == "revenue").all()
    assert tx["booking_date"].dtype.kind == "M" and tx["amount"].dtype == float
    assert tx["team_id"].isna().all()
