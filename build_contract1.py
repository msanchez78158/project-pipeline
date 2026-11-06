"""
build_contract1.py
Runs the Python data layer only (no Gemini) and writes contract1.json.
Imports all logic from pipeline.py so nothing is duplicated.
"""
import sys
sys.path.insert(0, ".")

import json
import os
from datetime import date
from pipeline import fetch_and_filter, build_ingest_payload

QUARTER = "2026-Q1"
OUT_DIR = f"pipeline_runs/{QUARTER}"
os.makedirs(OUT_DIR, exist_ok=True)

print(f"\nBuilding Contract 1 — {QUARTER}")
print("=" * 50)

# Step 1 — fetch & filter
print("\nStep 1: Fetching and filtering SBA data...")
df = fetch_and_filter()

# Step 2 — build structured payload (pure Python, no API)
print("\nStep 2: Building ingest payload...")
payload = build_ingest_payload(df)

# Convert payload directly to Contract 1 schema
# (mirrors what the Ingest Agent produces, but without the API call)
today = date.today().isoformat()

contract1 = {
    "meta": {
        "run_date": today,
        "data_as_of": "2025-12-31",
        "counties": ["FRANKLIN", "ST. LOUIS", "ST. LOUIS CITY"],
        "state": "MO",
        "new_loans_period": f"{today[:7]}-01 to {today}",
        "total_new_loans": len(payload["new_loans"]),
        "total_refi_candidates": len(payload["refi_candidates"]),
        "records_processed": payload["total_records"],
        "records_dropped": payload["total_records"] - len(payload["new_loans"]) - len(payload["refi_candidates"]),
        "data_warnings": []
    },
    "new_loans": payload["new_loans"],
    "refi_candidates": payload["refi_candidates"]
}

out_path = f"{OUT_DIR}/contract1.json"
with open(out_path, "w") as f:
    json.dump(contract1, f, indent=2)

print(f"\nContract 1 saved to {out_path}")
print(f"  New loans:        {contract1['meta']['total_new_loans']}")
print(f"  Refi candidates:  {contract1['meta']['total_refi_candidates']}")
print(f"  Records dropped:  {contract1['meta']['records_dropped']}")

# Sanity check Franklin County
franklin_new  = [r for r in contract1["new_loans"]   if r.get("county") == "FRANKLIN"]
franklin_refi = [r for r in contract1["refi_candidates"] if r.get("county") == "FRANKLIN"]
print(f"\n  Franklin County:  {len(franklin_new)} new loans, {len(franklin_refi)} refi candidates")

if not franklin_new and not franklin_refi:
    print("\nWARNING: No Franklin County records. Check data source.")
    sys.exit(1)

print("\nDone. Run Analysis + Synthesis agents next.")
