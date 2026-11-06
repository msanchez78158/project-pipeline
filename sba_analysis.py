"""
Project Pipeline — SBA Data Recon Script
RedomaWorks LLC

Run this locally to evaluate data quality and volume for:
  - Franklin County, MO
  - St. Louis County, MO

Setup: see README.md for official SBA downloads, exact local filenames,
and the separate offline synthetic verification command.
"""

import pandas as pd
from tabulate import tabulate
import os
import sys
from datetime import datetime, timedelta

# ── CONFIG ─────────────────────────────────────────────────────────────────────
TARGET_STATE = "MO"
TARGET_COUNTIES = ["FRANKLIN", "ST. LOUIS"]   # ProjectCounty values in the data
LOOKBACK_YEARS = 3                             # for recent activity analysis
REFI_WINDOW_MIN_MONTHS = 18
REFI_WINDOW_MAX_MONTHS = 36
# ──────────────────────────────────────────────────────────────────────────────

FILES = {
    # SBA occasionally inserts a hyphen in "as-of"; use the exact filenames shipped.
    "7a_2010_2019": "foia-7a-fy2010-fy2019-as-of-251231.csv",
    "7a_2020_present": "foia-7a-fy2020-present-as-of-251231.csv",
    "504_2010_present": "foia-504-fy2010-present-asof-251231.csv",
}

def load_files():
    frames = []
    for label, fname in FILES.items():
        if not os.path.exists(fname):
            print(f"  [SKIP] {fname} not found")
            continue
        print(f"  Loading {fname}...")
        df = pd.read_csv(fname, low_memory=False, encoding="latin1")
        df["_source"] = label
        frames.append(df)
    if not frames:
        print("\nNo CSV files found. Download them from data.sba.gov (see header).")
        sys.exit(1)
    return pd.concat(frames, ignore_index=True)


def normalize_columns(df):
    """Standardize column names across 7(a) and 504 schemas (case-insensitive)."""

    # SBA downloads occasionally ship with all-lowercase headers; normalize first so
    # downstream code can rely on consistent camel/pascal case column names.
    df.columns = [c.strip() for c in df.columns]

    rename = {
        # 7(a) field names
        "borrname":         "BorrowerName",
        "borrstreet":       "BorrowerStreet",
        "borrcity":         "BorrowerCity",
        "borrstate":        "BorrowerState",
        "borrzip":          "BorrowerZip",
        "grossapproval":    "GrossApproval",
        "approvaldate":     "ApprovalDate",
        "approvalfiscalyear": "FiscalYear",
        "naicscode":        "NaicsCode",
        "naicsdescription": "NaicsDescription",
        "projectcounty":    "ProjectCounty",
        "projectstate":     "ProjectState",
        "bank":             "LenderName",
        "bankstreet":       "LenderStreet",
        "bankcity":         "LenderCity",
        "bankstate":        "LenderState",
        "terminmonths":          "TermMonths",
        "loanstatus":            "LoanStatus",
        "jobssupported":         "JobsSupported",
        "initialinterestrate":   "InitialInterestRate",
        "fixedorvariableinterestind": "RateType",
        # 504 field names (some differ)
        "cdc_name":         "LenderName",
        "thirdpartylender_name": "TPLenderName",
    }

    df = df.rename(columns=lambda col: rename.get(col.lower(), col))
    return df


def parse_dates(df):
    if "ApprovalDate" not in df.columns:
        raise KeyError("ApprovalDate column is missing after normalization; check headers")
    df["ApprovalDate"] = pd.to_datetime(df["ApprovalDate"], errors="coerce")
    return df


def filter_to_market(df):
    state_mask = df["ProjectState"].str.upper().str.strip() == TARGET_STATE
    county_mask = df["ProjectCounty"].str.upper().str.strip().isin(
        [c.upper() for c in TARGET_COUNTIES]
    )
    return df[state_mask & county_mask].copy()


def section(title):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print(f"{'═'*60}")


def run_analysis(df):
    section("1. RAW DATA SNAPSHOT")
    print(f"  Total rows loaded (all states): {len(df):,}")
    print(f"  Columns: {list(df.columns[:12])} ...")
    print(f"  Date range: {df['ApprovalDate'].min().date()} → {df['ApprovalDate'].max().date()}")

    # ── FILTER TO MARKET ──────────────────────────────────────────────────────
    mkt = filter_to_market(df)
    section("2. MARKET FILTER — Franklin + St. Louis County, MO")
    print(f"  Total loans in target market: {len(mkt):,}")

    county_counts = mkt.groupby("ProjectCounty").size().reset_index(name="TotalLoans")
    print("\n  Loans by county (all time):")
    print(tabulate(county_counts, headers="keys", showindex=False, tablefmt="simple"))

    # ── RECENT VOLUME (last N years) ─────────────────────────────────────────
    section(f"3. RECENT ACTIVITY — Last {LOOKBACK_YEARS} Years")
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=LOOKBACK_YEARS)
    recent = mkt[mkt["ApprovalDate"] >= cutoff].copy()
    print(f"  Loans since {cutoff.date()}: {len(recent):,}")

    # By county + year
    recent["Year"] = recent["ApprovalDate"].dt.year
    by_county_year = (
        recent.groupby(["ProjectCounty", "Year"])
        .agg(Count=("GrossApproval", "count"), TotalDollars=("GrossApproval", "sum"))
        .reset_index()
    )
    by_county_year["TotalDollars"] = by_county_year["TotalDollars"].apply(
        lambda x: f"${x/1_000_000:.1f}M"
    )
    print("\n  Annual volume by county:")
    print(tabulate(by_county_year, headers="keys", showindex=False, tablefmt="simple"))

    # ── QUARTERLY CADENCE ────────────────────────────────────────────────────
    section("4. QUARTERLY CADENCE — What a single report quarter looks like")
    recent["Quarter"] = recent["ApprovalDate"].dt.to_period("Q")
    quarterly = (
        recent.groupby(["ProjectCounty", "Quarter"])
        .agg(Count=("GrossApproval", "count"), Dollars=("GrossApproval", "sum"))
        .reset_index()
    )
    quarterly["Dollars"] = quarterly["Dollars"].apply(lambda x: f"${x/1_000_000:.2f}M")
    # Show last 8 quarters
    last_8 = quarterly[quarterly["Quarter"] >= (pd.Period.now("Q") - 8)]
    print("\n  Loans per quarter (last 8 quarters):")
    print(tabulate(last_8, headers="keys", showindex=False, tablefmt="simple"))

    # ── COMPETITOR LENDERS ───────────────────────────────────────────────────
    section("5. COMPETITOR LENDERS — Who is winning SBA deals in this market")
    lender_vol = (
        recent.groupby("LenderName")
        .agg(Loans=("GrossApproval", "count"), Volume=("GrossApproval", "sum"))
        .sort_values("Loans", ascending=False)
        .head(15)
        .reset_index()
    )
    lender_vol["Volume"] = lender_vol["Volume"].apply(lambda x: f"${x/1_000_000:.2f}M")
    print("\n  Top 15 lenders by deal count (recent):")
    print(tabulate(lender_vol, headers="keys", showindex=False, tablefmt="simple"))

    # ── INDUSTRY BREAKDOWN ───────────────────────────────────────────────────
    section("6. INDUSTRY BREAKDOWN — Top NAICS sectors in market")
    industry = (
        recent.groupby("NaicsDescription")
        .agg(Count=("GrossApproval", "count"), TotalDollars=("GrossApproval", "sum"))
        .sort_values("Count", ascending=False)
        .head(20)
        .reset_index()
    )
    industry["TotalDollars"] = industry["TotalDollars"].apply(lambda x: f"${x/1_000_000:.2f}M")
    print("\n  Top 20 industries by loan count (recent):")
    print(tabulate(industry, headers="keys", showindex=False, tablefmt="simple"))

    # ── REFINANCE WINDOW ─────────────────────────────────────────────────────
    section(f"7. REFINANCE WINDOW — Loans {REFI_WINDOW_MIN_MONTHS}–{REFI_WINDOW_MAX_MONTHS} months old from competitors")
    now = pd.Timestamp.now()
    refi_min = now - pd.DateOffset(months=REFI_WINDOW_MAX_MONTHS)
    refi_max = now - pd.DateOffset(months=REFI_WINDOW_MIN_MONTHS)
    refi = mkt[
        (mkt["ApprovalDate"] >= refi_min) &
        (mkt["ApprovalDate"] <= refi_max) &
        (mkt["LoanStatus"].str.upper().str.strip() != "PIF")  # exclude paid in full
    ].copy()

    print(f"  Loans in refi window ({REFI_WINDOW_MIN_MONTHS}–{REFI_WINDOW_MAX_MONTHS} months old): {len(refi):,}")
    print(f"  Estimated total exposure: ${refi['GrossApproval'].sum()/1_000_000:.1f}M")

    rate_cols = [c for c in ["InitialInterestRate", "RateType"] if c in refi.columns]
    refi_sample = refi[
        ["BorrowerName", "BorrowerCity", "ProjectCounty", "LenderName",
         "GrossApproval", "ApprovalDate", "NaicsDescription"] + rate_cols
    ].sort_values("GrossApproval", ascending=False).head(20)
    refi_sample["GrossApproval"] = refi_sample["GrossApproval"].apply(
        lambda x: f"${x:,.0f}"
    )
    refi_sample["ApprovalDate"] = refi_sample["ApprovalDate"].dt.strftime("%Y-%m-%d")
    print("\n  Top 20 refi window prospects by loan size:")
    print(tabulate(refi_sample, headers="keys", showindex=False, tablefmt="simple"))

    # ── DATA QUALITY ─────────────────────────────────────────────────────────
    section("8. DATA QUALITY CHECKS")
    key_fields = ["BorrowerName", "BorrowerStreet", "BorrowerCity",
                  "LenderName", "NaicsCode", "GrossApproval", "ApprovalDate"]
    for field in key_fields:
        if field in mkt.columns:
            null_pct = mkt[field].isna().mean() * 100
            print(f"  {field:<25} null: {null_pct:.1f}%")

    # ── SYNTHETIC COMMUNITY BANK FIT ──────────────────────────────────────────
    section("9. SYNTHETIC COMMUNITY BANK FIT SUMMARY")
    franklin = mkt[mkt["ProjectCounty"].str.upper().str.strip() == "FRANKLIN"]
    recent_franklin = franklin[franklin["ApprovalDate"] >= cutoff]
    refi_franklin = refi[refi["ProjectCounty"].str.upper().str.strip() == "FRANKLIN"]
    stl = mkt[mkt["ProjectCounty"].str.upper().str.strip() == "ST. LOUIS"]
    recent_stl = stl[stl["ApprovalDate"] >= cutoff]

    print(f"""
  Franklin County
    All-time loans in dataset:        {len(franklin):,}
    Loans last {LOOKBACK_YEARS} years:               {len(recent_franklin):,}
    Loans per quarter (avg):          {len(recent_franklin)/(LOOKBACK_YEARS*4):.1f}
    Refi window prospects:            {len(refi_franklin):,}

  St. Louis County
    All-time loans in dataset:        {len(stl):,}
    Loans last {LOOKBACK_YEARS} years:               {len(recent_stl):,}
    Loans per quarter (avg):          {len(recent_stl)/(LOOKBACK_YEARS*4):.1f}

  PRODUCT VIABILITY
    Combined quarterly new records:   ~{(len(recent_franklin)+len(recent_stl))/(LOOKBACK_YEARS*4):.0f} loans
    Combined refi prospects right now: {len(refi):,} businesses
    Data fields sufficient for call list: {'YES' if 'BorrowerName' in mkt.columns and 'LenderName' in mkt.columns else 'CHECK COLUMNS'}
    Missing contact info (phone/email): YES — enrichment needed for V2
    Recommend proceeding:             {'YES' if len(recent) > 50 else 'BORDERLINE — check volume'}
    """)

    # ── EXPORT SAMPLE ────────────────────────────────────────────────────────
    section("10. EXPORTING SAMPLE FILES")
    recent.to_csv("market_recent_loans.csv", index=False)
    refi.to_csv("refi_window_prospects.csv", index=False)
    print("  market_recent_loans.csv   — all recent loans in target market")
    print("  refi_window_prospects.csv — refinance window prospects")
    print("\n  Done.")


if __name__ == "__main__":
    print("\nProject Pipeline — SBA Data Recon")
    print("RedomaWorks LLC\n")
    print("Loading files...")
    df = load_files()
    df = normalize_columns(df)
    df = parse_dates(df)
    run_analysis(df)
