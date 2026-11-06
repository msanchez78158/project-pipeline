"""
build_demo_data.py — Project Pipeline Demo Data Builder
========================================================
Reads local SBA FOIA CSVs, filters to the STL metro demo counties,
scores prospects, fetches current prime rate from FRED, and outputs
a single prospects.json into demo/data/.

Run this whenever you want to refresh the demo dataset:
    python3 scripts/build_demo_data.py

Requirements:
    pip install pandas python-dateutil requests python-dotenv

Environment variables (in .env):
    FRED_API_KEY=your_fred_key
    PRIME_RATE_OVERRIDE=8.50   # optional — overrides FRED if set
"""

import os
from pathlib import Path
import glob
import json
import requests
import pandas as pd
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()

# ── CONFIGURATION ─────────────────────────────────────────────────────────────

# SBA data folder — drop new CSV files here before running
REPO_ROOT = Path(__file__).resolve().parents[1]
SBA_DATA_DIR = REPO_ROOT / "data"

def find_csv_files():
    patterns = [
        "foia-7a-*.csv",
        "sba_7a_*.csv",
        "sba-7a-*.csv",
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(os.path.join(SBA_DATA_DIR, pattern)))
    found = sorted(set(found), reverse=True)
    if not found:
        print(f"ERROR: No SBA 7(a) CSV files found in {SBA_DATA_DIR}")
        print("Download files from https://data.sba.gov/dataset/7a-504-foia")
        print("and drop them into the data/ folder.")
        print("Files present in data/:", os.listdir(SBA_DATA_DIR) if os.path.exists(SBA_DATA_DIR) else "folder not found")
        raise SystemExit(1)
    print(f"   Found {len(found)} CSV file(s) in data/:")
    for f in found:
        size_mb = os.path.getsize(f) / 1_048_576
        print(f"   · {os.path.basename(f)} ({size_mb:.1f} MB)")
    return found

# Target counties for the demo — STL metro focus
TARGET_STATE = "MO"
TARGET_COUNTIES = {
    "ST. LOUIS":   ["ST LOUIS", "SAINT LOUIS", "ST. LOUIS"],
    "ST. LOUIS CITY": ["ST LOUIS CITY", "SAINT LOUIS CITY", "ST. LOUIS CITY"],
    "ST. CHARLES": ["ST CHARLES", "SAINT CHARLES", "ST. CHARLES"],
    "JEFFERSON":   ["JEFFERSON"],
}

# All raw county strings that map to our targets
ALL_TARGET_STRINGS = [v for vals in TARGET_COUNTIES.values() for v in vals]

# Refi window — months since approval
REFI_WINDOW_MIN = 18
REFI_WINDOW_MAX = 36

# Minimum loan size for the demo
MIN_LOAN_AMOUNT = 50_000

# Output path
OUTPUT_PATH = REPO_ROOT / "demo" / "data" / "prospects.json"

# FRED series ID for US Prime Rate
FRED_SERIES_ID = "DPRIME"


# ── PRIME RATE ────────────────────────────────────────────────────────────────

def fetch_prime_rate() -> dict:
    """
    Fetch current US prime rate from FRED API.
    Falls back to PRIME_RATE_OVERRIDE env var, then hardcoded fallback.
    Returns a dict with rate, source, and date.
    """
    # Check for manual override first
    override = os.getenv("PRIME_RATE_OVERRIDE")
    if override:
        try:
            rate = float(override)
            print(f"   Prime rate: {rate}% (manual override)")
            return {"rate": rate, "source": "manual_override", "date": date.today().isoformat()}
        except ValueError:
            print("   WARNING: Invalid PRIME_RATE_OVERRIDE; expected a number")

    fred_key = os.getenv("FRED_API_KEY")
    if not fred_key:
        print("   WARNING: No FRED_API_KEY found — using fallback rate 8.50%")
        return {"rate": 8.50, "source": "fallback", "date": date.today().isoformat()}

    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={FRED_SERIES_ID}"
            f"&api_key={fred_key}"
            f"&file_type=json"
            f"&sort_order=desc"
            f"&limit=1"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        obs = data["observations"][0]
        rate = float(obs["value"])
        obs_date = obs["date"]
        print(f"   Prime rate: {rate}% (FRED, as of {obs_date})")
        return {"rate": rate, "source": "fred", "date": obs_date}
    except Exception:
        print("   WARNING: FRED fetch failed — using fallback 8.50%")
        return {"rate": 8.50, "source": "fallback", "date": date.today().isoformat()}


# ── SBA RATE BENCHMARK ────────────────────────────────────────────────────────

def sba_benchmark_rate(gross_approval: float, prime_rate: float) -> float:
    """
    Calculate current SBA 7(a) market rate benchmark by loan size.
    SBA caps: > $350K = Prime + 2.75%, $50K–$350K = Prime + 3.25%, < $50K = Prime + 4.75%
    """
    if gross_approval >= 350_000:
        return round(prime_rate + 2.75, 2)
    if gross_approval >= 50_000:
        return round(prime_rate + 3.25, 2)
    return round(prime_rate + 4.75, 2)


# ── COUNTY NORMALIZATION ──────────────────────────────────────────────────────

def normalize_county(raw: str) -> str | None:
    """Map raw county string to canonical display name. Returns None if not a target."""
    clean = (
        str(raw).strip().upper()
        .replace(" COUNTY", "")
        .replace(" CO.", "")
        .strip()
    )
    for canonical, variants in TARGET_COUNTIES.items():
        if clean in variants:
            return canonical
    return None


# ── PRIORITY SCORING ──────────────────────────────────────────────────────────

def score_prospect(loan_amount: float, months_since: int, county: str) -> dict:
    """
    Score a refi prospect 0–100.
    Loan amount: 40pts | Refi window: 30pts | County: 20pts | Base: 10pts
    """
    # Loan amount (40pts)
    if loan_amount >= 1_000_000:
        amount_pts = 40
    elif loan_amount >= 500_000:
        amount_pts = 35
    elif loan_amount >= 300_000:
        amount_pts = 28
    elif loan_amount >= 150_000:
        amount_pts = 20
    elif loan_amount >= 50_000:
        amount_pts = 10
    else:
        amount_pts = 5

    # Refi window proximity (30pts) — peak at 24–30 months
    if 24 <= months_since <= 30:
        window_pts = 30
    elif 20 <= months_since < 24:
        window_pts = 24
    elif 18 <= months_since < 20:
        window_pts = 18
    elif 31 <= months_since <= 33:
        window_pts = 20
    elif 34 <= months_since <= 36:
        window_pts = 12
    else:
        window_pts = 5

    # County (20pts)
    county_pts_map = {
        "ST. LOUIS":      20,
        "ST. LOUIS CITY": 18,
        "ST. CHARLES":    16,
        "JEFFERSON":      14,
    }
    county_pts = county_pts_map.get(county, 10)

    # Base pts
    base_pts = 10

    total = amount_pts + window_pts + county_pts + base_pts

    if total >= 80:
        priority = "HIGH"
    elif total >= 55:
        priority = "MEDIUM"
    else:
        priority = "MONITOR"

    return {
        "score": total,
        "priority": priority,
        "score_breakdown": {
            "amount_pts": amount_pts,
            "window_pts": window_pts,
            "county_pts": county_pts,
            "base_pts":   base_pts,
        }
    }


# ── LENDER CLASSIFICATION ─────────────────────────────────────────────────────

def classify_lender(lender_state: str, borrower_state: str) -> str:
    """Classify lender as local, regional, or national/out-of-state."""
    if not lender_state or not borrower_state:
        return "unknown"
    ls = str(lender_state).strip().upper()
    bs = str(borrower_state).strip().upper()
    if ls == bs:
        return "in_state"
    return "out_of_state"


# ── MAIN PROCESSING ───────────────────────────────────────────────────────────

def build_prospects():
    today = date.today()
    print(f"\nProject Pipeline — Demo Data Builder")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Fetch prime rate
    print("\n1. Fetching prime rate...")
    prime_info = fetch_prime_rate()
    prime_rate = prime_info["rate"]

    # Load and filter CSVs
    print("\n2. Loading FOIA data...")
    csv_files = find_csv_files()
    frames = []
    for csv_file in csv_files:
        if not os.path.exists(csv_file):
            print(f"   WARNING: {csv_file} not found — skipping")
            continue
        print(f"   Reading {csv_file}...")
        df = pd.read_csv(csv_file, low_memory=False)
        df.columns = [c.strip().lower() for c in df.columns]
        frames.append(df)
        print(f"   {len(df):,} rows loaded")

    if not frames:
        raise SystemExit("ERROR: No CSV files found. Run from the Project Pipeline Demo directory.")

    raw = pd.concat(frames, ignore_index=True)
    print(f"   Total rows: {len(raw):,}")

    # Filter to Missouri
    mo = raw[raw["projectstate"].str.strip().str.upper() == TARGET_STATE].copy()
    print(f"   Missouri rows: {len(mo):,}")

    # Normalize county
    mo["_county"] = mo["projectcounty"].apply(normalize_county)
    market = mo[mo["_county"].notna()].copy()
    print(f"   Target market rows: {len(market):,}")

    # Parse dates
    market["approvaldate"] = pd.to_datetime(market["approvaldate"], errors="coerce")
    market = market[market["approvaldate"].notna()].copy()

    # Calculate months since approval
    market["_months_since"] = market["approvaldate"].apply(
        lambda d: (today.year - d.year) * 12 + (today.month - d.month)
    )

    # Filter active statuses — exclude paid in full and charged off
    excluded_statuses = {"PIF", "CHGOFF", "CANCLD"}
    market["_status_clean"] = market["loanstatus"].str.strip().str.upper().fillna("")
    market = market[~market["_status_clean"].isin(excluded_statuses)].copy()
    print(f"   After status filter: {len(market):,}")

    # Filter loan amount
    market["grossapproval"] = pd.to_numeric(market["grossapproval"], errors="coerce").fillna(0)
    market = market[market["grossapproval"] >= MIN_LOAN_AMOUNT].copy()
    print(f"   After amount filter (>= ${MIN_LOAN_AMOUNT:,}): {len(market):,}")

    # Split into refi candidates and watch list
    refi = market[
        (market["_months_since"] >= REFI_WINDOW_MIN) &
        (market["_months_since"] <= REFI_WINDOW_MAX)
    ].copy()

    watch = market[market["_months_since"] < REFI_WINDOW_MIN].copy()

    print(f"\n   Refi candidates (18–36 months): {len(refi):,}")
    print(f"   New loan watch list (<18 months): {len(watch):,}")

    # ── BUILD PROSPECT RECORDS ────────────────────────────────────────────────
    print("\n3. Scoring and building prospect records...")

    def build_record(row, record_type: str) -> dict:
        gross = float(row["grossapproval"])
        months_since = int(row["_months_since"])
        county = row["_county"]
        approval_dt = row["approvaldate"]

        # Interest rate
        orig_rate = None
        raw_rate = row.get("initialinterestrate")
        if raw_rate is not None and not (isinstance(raw_rate, float) and pd.isnull(raw_rate)):
            try:
                orig_rate = round(float(raw_rate), 2)
            except (ValueError, TypeError):
                pass

        # Rate type
        rt_raw = str(row.get("fixedorvariableinterestind", "")).strip().upper()
        rate_type = "VARIABLE" if rt_raw in ("V", "VARIABLE") else ("FIXED" if rt_raw in ("F", "FIXED") else None)

        # Term and maturity
        term_raw = row.get("terminmonths")
        term_months = None
        maturity_date = None
        if term_raw is not None and not (isinstance(term_raw, float) and pd.isnull(term_raw)):
            try:
                term_months = int(term_raw)
                maturity_date = (approval_dt + relativedelta(months=term_months)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Months remaining
        months_remaining = None
        if maturity_date:
            mat = datetime.strptime(maturity_date, "%Y-%m-%d").date()
            diff = (mat.year - today.year) * 12 + (mat.month - today.month)
            months_remaining = max(0, diff)

        # Rate delta vs current market
        benchmark = sba_benchmark_rate(gross, prime_rate)
        rate_delta = None
        rate_delta_label = "rate unavailable"
        annual_savings = None

        if orig_rate and orig_rate > 0:
            rate_delta = round(orig_rate - benchmark, 2)
            caveat = " (variable — origination rate only)" if rate_type == "VARIABLE" else ""
            if rate_delta > 0:
                rate_delta_label = f"+{rate_delta}% above market{caveat}"
            elif rate_delta < 0:
                rate_delta_label = f"{rate_delta}% below market{caveat}"
            else:
                rate_delta_label = f"at market rate{caveat}"

            # Annual savings if refinanced at benchmark
            if rate_delta > 0:
                annual_savings = round(gross * (rate_delta / 100), 2)

        # Lender classification
        lender_state = str(row.get("bankstate", "")).strip().upper()
        lender_type = classify_lender(lender_state, TARGET_STATE)

        # Scoring
        scoring = score_prospect(gross, months_since, county)

        # NAICS
        naics_code = str(row.get("naicscode", "")).strip()
        naics_desc = str(row.get("naicsdescription", "")).strip().title()

        # Clean borrower name
        borrower_name = str(row.get("borrname", "")).strip().title()
        if not borrower_name or borrower_name.lower() in ("nan", ""):
            borrower_name = "Unknown Borrower"

        # Lender name — prefer bankname
        lender_name = str(row.get("bankname", "")).strip().title()
        if not lender_name or lender_name.lower() in ("nan", "none", ""):
            lender_name = "Unknown Lender"

        return {
            "id": f"{row.get('borrname', '')}_{row.get('approvaldate', '')}_{row.get('grossapproval', '')}".replace(" ", "_"),
            "type": record_type,
            "borrower_name": borrower_name,
            "borrower_street": str(row.get("borrstreet", "")).strip().title(),
            "borrower_city": str(row.get("borrcity", "")).strip().title(),
            "borrower_zip": str(row.get("borrzip", "")).strip(),
            "county": county,
            "lender_name": lender_name,
            "lender_state": lender_state,
            "lender_type": lender_type,
            "program": str(row.get("program", "7A")).strip().upper(),
            "gross_approval": gross,
            "approval_date": approval_dt.strftime("%Y-%m-%d"),
            "months_since_approval": months_since,
            "months_remaining": months_remaining,
            "term_months": term_months,
            "maturity_date": maturity_date,
            "naics_code": naics_code,
            "naics_description": naics_desc,
            "jobs_supported": int(row["jobssupported"]) if not pd.isnull(row.get("jobssupported", None)) else None,
            "loan_status": str(row.get("loanstatus", "")).strip().upper(),
            "initial_interest_rate": orig_rate,
            "rate_type": rate_type,
            "benchmark_rate": benchmark,
            "rate_delta": rate_delta,
            "rate_delta_label": rate_delta_label,
            "annual_savings": annual_savings,
            "priority_score": scoring["score"],
            "priority": scoring["priority"],
            "score_breakdown": scoring["score_breakdown"],
        }

    refi_records = [build_record(row, "refi") for _, row in refi.iterrows()]
    watch_records = [build_record(row, "watch") for _, row in watch.iterrows()]

    # Sort refi by priority score desc
    refi_records.sort(key=lambda x: x["priority_score"], reverse=True)
    watch_records.sort(key=lambda x: x["priority_score"], reverse=True)

    # Deduplicate by borrower + approval date + lender
    def dedup(records):
        seen = set()
        out = []
        for r in records:
            key = (r["borrower_name"], r["approval_date"], r["lender_name"])
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    refi_records = dedup(refi_records)
    watch_records = dedup(watch_records)

    # Summary stats
    total_volume = sum(r["gross_approval"] for r in refi_records)
    high_priority = [r for r in refi_records if r["priority"] == "HIGH"]
    out_of_state = [r for r in refi_records if r["lender_type"] == "out_of_state"]
    avg_rate_delta = None
    deltas = [r["rate_delta"] for r in refi_records if r["rate_delta"] is not None]
    if deltas:
        avg_rate_delta = round(sum(deltas) / len(deltas), 2)

    total_annual_savings = sum(r["annual_savings"] for r in refi_records if r["annual_savings"])

    # County breakdown
    county_counts = {}
    for r in refi_records:
        county_counts[r["county"]] = county_counts.get(r["county"], 0) + 1

    # Competitor lender breakdown
    lender_counts = {}
    for r in refi_records:
        name = r["lender_name"]
        if name not in lender_counts:
            lender_counts[name] = {"count": 0, "volume": 0}
        lender_counts[name]["count"] += 1
        lender_counts[name]["volume"] += r["gross_approval"]

    top_lenders = sorted(
        [{"lender": k, **v} for k, v in lender_counts.items()],
        key=lambda x: x["volume"],
        reverse=True
    )[:10]

    # Build final output
    output = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "data_as_of": "2025-12-31",
            "target_counties": list(TARGET_COUNTIES.keys()),
            "refi_window_months": f"{REFI_WINDOW_MIN}–{REFI_WINDOW_MAX}",
            "min_loan_amount": MIN_LOAN_AMOUNT,
            "prime_rate": prime_info,
            "total_refi_candidates": len(refi_records),
            "total_watch_list": len(watch_records),
        },
        "summary": {
            "total_refi_candidates": len(refi_records),
            "total_refi_volume": total_volume,
            "high_priority_count": len(high_priority),
            "out_of_state_lender_count": len(out_of_state),
            "avg_rate_delta": avg_rate_delta,
            "total_annual_savings_available": total_annual_savings,
            "county_breakdown": county_counts,
            "top_competitors": top_lenders,
        },
        "refi_candidates": refi_records,
        "watch_list": watch_records,
    }

    # Write output
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n4. Output written to {OUTPUT_PATH}")
    print(f"\n{'=' * 50}")
    print(f"   Refi candidates:        {len(refi_records):,}")
    print(f"   Watch list:             {len(watch_records):,}")
    print(f"   High priority:          {len(high_priority):,}")
    print(f"   Out-of-state lenders:   {len(out_of_state):,}")
    print(f"   Total refi volume:      ${total_volume:,.0f}")
    print(f"   Total annual savings:   ${total_annual_savings:,.0f}")
    print(f"   Avg rate delta:         {avg_rate_delta}%")
    print(f"   Prime rate used:        {prime_rate}% ({prime_info['source']})")
    print(f"\nDone. Updated demo/data/prospects.json.")


if __name__ == "__main__":
    build_prospects()
