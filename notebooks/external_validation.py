#!/usr/bin/env python3
"""T066: check the forecast METHOD (feature sets of T061 / T068) on a public multi-year daily sales series.

Dataset: UCI "Online Retail II" (CC BY 4.0), the invoices of a UK online gift-ware retailer, 1 Dec 2009 - 9 Dec 2011.
Daily revenue of that business is mapped to the `transactions` frame the harness expects and scored with T060's
`rolling_origin` + `score` for each feature set that can run on revenue-only bookings. The data is evidence about
the method only: it never trains, tunes or ships the production model, and it is never committed (the download
folder is git-ignored).

Run (the XLSX needs `openpyxl`, which is not a backend dependency, so it is added for this one run):

    uv run --project backend --with openpyxl python notebooks/external_validation.py

The downloaded file is untrusted data: it is only read with pandas (`read_excel`), nothing in it is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "external_data"  # git-ignored
SOURCE_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
MEMBER = "online_retail_II.xlsx"
XLSX_NAME = "online_retail_II.xlsx"
DAILY_NAME = "online_retail_ii_daily_revenue.csv"
LICENCE = "CC BY 4.0 (UCI Machine Learning Repository, dataset 502, Online Retail II)"
USER_AGENT = "generic-python-client"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

# Feature sets that run on revenue-only bookings, and the ones that do not.
FEATURE_SETS = ("calendar", "level", "level_baseline")
NOT_RUNNABLE = {
    "components": "its point is the fixed-cost schedule (payroll, rent, tax, ...); this series has revenue only, so "
                  "the schedule is empty. It does run technically (checked on 2 origins) but would only be a "
                  "level_baseline variant with other booster parameters, so it says nothing about the schedule",
}
# Declared before any score was looked at: the first year of history (no earlier year to learn from) against the
# origins that already have a full year behind them.
SECOND_YEAR_FROM = pd.Timestamp("2010-12-01")
TRANSACTION_COLUMNS = ["booking_date", "amount", "category", "team_id", "counterparty", "invoice_id"]


# -- data -------------------------------------------------------------------------------------------------
def download(data_dir: Path, url: str = SOURCE_URL) -> Path:
    """Fetch the ZIP and write the one expected member to `data_dir` (bytes only, no path from the archive)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / XLSX_NAME
    if target.exists():
        return target
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 (fixed https URL)
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise SystemExit("download is larger than expected; refusing it")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo(MEMBER)  # KeyError if the archive does not hold the expected file
        if info.file_size > MAX_DOWNLOAD_BYTES:
            raise SystemExit("archive member is larger than expected; refusing it")
        target.write_bytes(archive.read(info))
    print(f"downloaded {SOURCE_URL}: {target.stat().st_size / 1e6:.1f} MB, "
          f"sha256 {hashlib.sha256(target.read_bytes()).hexdigest()[:16]}...", file=sys.stderr)
    return target


def daily_revenue(invoices: pd.DataFrame) -> pd.Series:
    """Daily revenue of product sales: Quantity * Price of non-cancelled lines with a product stock code.

    Cancellations (invoice numbers starting with `C`), non-positive quantities / prices and the non-product codes
    (postage, bank charges, manual adjustments, ... : codes that do not start with a digit) are dropped. The two
    yearly sheets overlap in December 2010, so exact duplicate lines are dropped first. The definition was fixed
    before any forecast score was computed.
    """
    lines = invoices.drop_duplicates().copy()
    invoice = lines["Invoice"].astype(str)
    stock = lines["StockCode"].astype(str)
    keep = (~invoice.str.startswith("C") & (lines["Quantity"] > 0) & (lines["Price"] > 0)
            & stock.str.match(r"^\d"))
    lines = lines[keep]
    day = pd.to_datetime(lines["InvoiceDate"]).dt.normalize()
    revenue = (lines["Quantity"] * lines["Price"]).groupby(day).sum()
    revenue.index.name = "booking_date"
    return revenue.sort_index().rename("amount")


def load_daily(data_dir: Path) -> pd.Series:
    """Daily revenue, cached as a small CSV next to the download (parsing the XLSX takes about a minute)."""
    cache = data_dir / DAILY_NAME
    if cache.exists():
        frame = pd.read_csv(cache, parse_dates=["booking_date"])
        return frame.set_index("booking_date")["amount"]
    xlsx = download(data_dir)
    try:
        sheets = pd.read_excel(xlsx, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
    except ImportError as exc:  # openpyxl is not a backend dependency
        raise SystemExit("reading the XLSX needs openpyxl: run with "
                         "`uv run --project backend --with openpyxl python notebooks/external_validation.py`") from exc
    revenue = daily_revenue(pd.concat(sheets.values(), ignore_index=True))
    revenue.reset_index().to_csv(cache, index=False)
    return revenue


def to_transactions(revenue: pd.Series) -> pd.DataFrame:
    """The frame `features.fetch_transactions` returns: one revenue booking per trading day, other columns empty."""
    frame = pd.DataFrame({
        "booking_date": pd.to_datetime(revenue.index),
        "amount": revenue.to_numpy(dtype=float),
        "category": "revenue",
        "team_id": None,
        "counterparty": None,
        "invoice_id": None,
    })
    return frame[TRANSACTION_COLUMNS].sort_values("booking_date", kind="stable").reset_index(drop=True)


# -- scoring ----------------------------------------------------------------------------------------------
def _trainer(feature_set: str):
    from cfo.forecast import model as fm

    return lambda tx: fm.train(tx, feature_set=feature_set, log_to_mlflow=False, holdout_days=0, band=False)


def run_folds(tx: pd.DataFrame, origins: list[pd.Timestamp], feature_sets: Sequence[str]) -> dict[str, pd.DataFrame]:
    from cfo.forecast import evaluate as ev

    return {name: ev.rolling_origin(tx, _trainer(name), origins=origins) for name in feature_sets}


def score_tables(folds: dict[str, pd.DataFrame]) -> dict[str, dict[str, dict[str, float]]]:
    """Scores per subset ("all", "first_year" = origins before SECOND_YEAR_FROM, "second_year" = from it on)."""
    from cfo.forecast import evaluate as ev

    out: dict[str, dict[str, dict[str, float]]] = {}
    subsets = {
        "all": lambda f: f,
        "first_year": lambda f: f[f["origin"] < SECOND_YEAR_FROM],
        "second_year": lambda f: f[f["origin"] >= SECOND_YEAR_FROM],
    }
    for subset, pick in subsets.items():
        out[subset] = {}
        for name, fold in folds.items():
            part = pick(fold)
            if not part.empty:
                out[subset][name] = ev.score(part)
    return out


def per_origin_wins(folds: dict[str, pd.DataFrame], challenger: str, reference: str) -> pd.DataFrame:
    """For each origin with a full 90-day fold: |cum_error at day 90| of both sets and who is closer."""
    rows = []
    for origin, ref in folds[reference].groupby("origin"):
        ref90 = ref.loc[ref["horizon"] == 90, "cum_error"]
        cha = folds[challenger]
        cha90 = cha.loc[(cha["origin"] == origin) & (cha["horizon"] == 90), "cum_error"]
        if ref90.empty or cha90.empty:
            continue
        rows.append({"origin": origin, reference: abs(float(ref90.iloc[0])), challenger: abs(float(cha90.iloc[0]))})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["challenger_closer"] = frame[challenger] < frame[reference]
    return frame


def format_table(scores: dict[str, dict[str, float]]) -> str:
    names = list(scores)
    keys = list(next(iter(scores.values())))
    width = max(len(k) for k in keys)
    lines = [f"{'metric':<{width}}  " + "  ".join(f"{n:>15}" for n in names)]
    for key in keys:
        cells = []
        for name in names:
            value = scores[name][key]
            cells.append(f"{value:>15.0f}" if key == "n_origins" else
                         f"{value:>15.3f}" if key == "monthly_net_mape" else f"{value:>15.1f}")
        lines.append(f"{key:<{width}}  " + "  ".join(cells))
    return "\n".join(lines)


def build_origins(tx: pd.DataFrame, kind: str) -> list[pd.Timestamp]:
    from cfo.forecast import evaluate as ev
    from cfo.forecast import folds as fold_origins

    if kind == "weekly":
        return fold_origins.weekly_origins(tx)
    days = pd.date_range(tx["booking_date"].min(), tx["booking_date"].max(), freq="D")
    return ev._default_origins(days, ev.DEFAULT_HORIZON_DAYS)  # the harness' own month-end origins


# -- main -------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notebooks/external_validation.py",
        description="T066: score the forecast feature sets (calendar, level, level_baseline) with T060's rolling-origin "
                    "harness on a public multi-year daily revenue series (UCI Online Retail II, CC BY 4.0). Downloads "
                    "into a git-ignored folder; the data is evidence about the method only and never trains or tunes "
                    "the production model. Needs openpyxl for the XLSX: "
                    "`uv run --project backend --with openpyxl python notebooks/external_validation.py`.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"download / cache folder, git-ignored (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--folds", choices=["month-end", "weekly"], nargs="+", default=["month-end", "weekly"],
                        help="origin grids to score (default: both)")
    parser.add_argument("--feature-sets", nargs="+", default=list(FEATURE_SETS), choices=list(FEATURE_SETS),
                        help="feature sets to score")
    args = parser.parse_args(argv)

    revenue = load_daily(args.data_dir)
    tx = to_transactions(revenue)
    span = f"{tx['booking_date'].min().date()} .. {tx['booking_date'].max().date()}"
    print(f"source:  {SOURCE_URL}\nlicence: {LICENCE}\ntrading days: {len(tx)}, range {span}, "
          f"mean daily revenue {tx['amount'].mean():.0f} (GBP, product lines only)")
    for name, why in NOT_RUNNABLE.items():
        print(f"not scored: {name}: {why}")

    for kind in args.folds:
        origins = build_origins(tx, "weekly" if kind == "weekly" else "month-end")
        print(f"\n== {kind} folds: {len(origins)} origins, {origins[0].date()} .. {origins[-1].date()} ==")
        folds = run_folds(tx, origins, args.feature_sets)
        tables = score_tables(folds)
        for subset, scores in tables.items():
            if scores:
                label = {"all": "every origin", "first_year": f"origins before {SECOND_YEAR_FROM.date()}",
                         "second_year": f"origins from {SECOND_YEAR_FROM.date()} on"}[subset]
                print(f"\n-- {subset}: {label} --")
                print(format_table(scores))
        if "level_baseline" in folds and "calendar" in folds:
            for challenger in [s for s in args.feature_sets if s != "calendar"]:
                wins = per_origin_wins(folds, challenger, "calendar")
                if wins.empty:
                    continue
                late = wins[wins["origin"] >= SECOND_YEAR_FROM]
                print(f"\n{challenger} closer than calendar at day 90: {int(wins['challenger_closer'].sum())} of "
                      f"{len(wins)} origins with a full 90-day fold ({int(late['challenger_closer'].sum())} of "
                      f"{len(late)} from {SECOND_YEAR_FROM.date()} on)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
