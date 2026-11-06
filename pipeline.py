import os
import json
import re
import pandas as pd
import requests
import openai
from io import StringIO
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()

# ── OPENROUTER CLIENT ─────────────────────────────────────────────────────────
_or_key = os.getenv("OPENROUTER")
if not _or_key:
    raise SystemExit("ERROR: OPENROUTER key not found in environment. Check your .env file.")

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=_or_key,
)

# ── MODEL CONFIGURATION ───────────────────────────────────────────────────────
# Each agent can run a different model. Swap any of these independently.
#
# Model identifiers below are inherited defaults, not verified availability or pricing.
# Set the three PIPELINE_*_MODEL variables to models available in your account.

MODELS = {
    "ingest":    os.getenv("PIPELINE_INGEST_MODEL",    "google/gemma-4-31b-it"),
    "analysis":  os.getenv("PIPELINE_ANALYSIS_MODEL",  "deepseek/deepseek-v4-flash"),
    "synthesis": os.getenv("PIPELINE_SYNTHESIS_MODEL", "anthropic/claude-sonnet-4-5"),
}
# Model overrides — add any of these to your .env to swap models per agent:
# PIPELINE_INGEST_MODEL=deepseek/deepseek-chat
# PIPELINE_ANALYSIS_MODEL=deepseek/deepseek-chat
# PIPELINE_SYNTHESIS_MODEL=anthropic/claude-haiku-4-5

# ── TARGET GEOGRAPHY ──────────────────────────────────────────────────────────
TARGET_STATE    = "MO"
TARGET_COUNTIES = ["FRANKLIN", "ST. LOUIS", "ST LOUIS", "SAINT LOUIS"]

# ── SBA DATA URLS ─────────────────────────────────────────────────────────────
SBA_URLS = {
    "7a_2020_present": "https://data.sba.gov/dataset/0ff8e8e9-b967-4f4e-987c-6ac78c575087/resource/d67d3ccb-2002-4134-a288-481b51cd3479/download/foia-7a-fy2020-present-as-of-251231.csv",
    "7a_2010_2019":    "https://data.sba.gov/dataset/0ff8e8e9-b967-4f4e-987c-6ac78c575087/resource/3f838176-6060-44db-9c91-b4acafbcb28c/download/foia-7a-fy2010-fy2019-as-of-251231.csv",
}

# ── AGENT PROMPTS ─────────────────────────────────────────────────────────────

INGEST_PROMPT = """
You are the Ingest & Filter Agent for Project Pipeline, a commercial lending
intelligence system built for community banks. Your sole responsibility is
validating and structuring pre-filtered loan data passed to you by the
Python data layer.

You will receive a JSON object containing raw filtered loan records.
Your job is to clean, normalize, and structure them into Contract 1 JSON.

Rules:
- Standardize all text fields to UPPER CASE
- Set missing address fields to "ADDRESS UNAVAILABLE"
- Remove duplicate rows (same borrower name + approval date + lender)
- Calculate months_since_approval as whole months from ApprovalDate to today
- Classify each record as new_loan (approved in last 90 days) or
  refi_candidate (approved 18-36 months ago, status not PIF or CHGOFF)
- County must be one of: FRANKLIN, ST. LOUIS, ST. LOUIS CITY
- Pass through rate and maturity fields unchanged: initial_interest_rate, rate_type,
  estimated_maturity_date, rate_delta, rate_delta_label. Do not recalculate them.
- Respond with ONLY valid raw JSON. No explanation, no markdown, no code fences.

Output schema:
{
  "meta": {
    "run_date": "<ISO date>",
    "data_as_of": "2025-12-31",
    "counties": ["FRANKLIN", "ST. LOUIS", "ST. LOUIS CITY"],
    "state": "MO",
    "new_loans_period": "<YYYY-MM-DD to YYYY-MM-DD>",
    "total_new_loans": <integer>,
    "total_refi_candidates": <integer>,
    "records_processed": <integer>,
    "records_dropped": <integer>,
    "data_warnings": []
  },
  "new_loans": [
    {
      "borrower_name": "<string>",
      "borrower_street": "<string>",
      "borrower_city": "<string>",
      "borrower_zip": "<string>",
      "county": "<FRANKLIN | ST. LOUIS | ST. LOUIS CITY>",
      "lender_name": "<string>",
      "program": "<7A | 504>",
      "gross_approval": <number>,
      "approval_date": "<YYYY-MM-DD>",
      "naics_code": "<string>",
      "naics_description": "<string>",
      "term_months": <integer>,
      "jobs_supported": <integer or null>,
      "loan_status": "<string>",
      "months_since_approval": <integer>,
      "initial_interest_rate": <float or null>,
      "rate_type": "<FIXED | VARIABLE | null>",
      "estimated_maturity_date": "<YYYY-MM-DD or null>",
      "rate_delta": <float or null>,
      "rate_delta_label": "<string>"
    }
  ],
  "refi_candidates": [
    {
      "borrower_name": "<string>",
      "borrower_street": "<string>",
      "borrower_city": "<string>",
      "borrower_zip": "<string>",
      "county": "<FRANKLIN | ST. LOUIS | ST. LOUIS CITY>",
      "lender_name": "<string>",
      "program": "<7A | 504>",
      "gross_approval": <number>,
      "approval_date": "<YYYY-MM-DD>",
      "months_since_approval": <integer>,
      "naics_code": "<string>",
      "naics_description": "<string>",
      "term_months": <integer or null>,
      "loan_status": "<string>",
      "initial_interest_rate": <float or null>,
      "rate_type": "<FIXED | VARIABLE | null>",
      "estimated_maturity_date": "<YYYY-MM-DD or null>",
      "rate_delta": <float or null>,
      "rate_delta_label": "<string>"
    }
  ]
}
"""

ANALYSIS_PROMPT = """
You are the Analysis Agent for Project Pipeline, a commercial lending
intelligence system built for community banks. You receive Contract 1 JSON
and produce scored, ranked intelligence as Contract 2 JSON.

The end client is SYNTHETIC COMMUNITY BANK in Washington MO.
Franklin County records are highest priority.

Priority scoring for refi candidates (0-100):

LOAN AMOUNT (40pts max):
  >= $500,000: 40pts | >= $300,000: 30pts | >= $150,000: 20pts
  >= $50,000: 10pts  | < $50,000: 5pts

REFI WINDOW (30pts max):
  24-30 months: 30pts (peak) | 18-23 months: 20pts | 31-36 months: 15pts

COUNTY (20pts max):
  FRANKLIN: 20pts | ST. LOUIS: 12pts | ST. LOUIS CITY: 10pts

SECTOR ACTIVITY (10pts max):
  Top 3 sectors this quarter: 10pts | Top 6: 5pts | Others: 2pts

Watch scoring for new loans (0-100):
  Loan amount (50pts max, same scale)
  County (30pts max, same scale)
  Sector activity (20pts max, same scale)

suggested_action for refi candidates:
  >= 75: "High priority — initiate contact this week"
  >= 50: "Medium priority — add to next outreach cycle"
  <  50: "Monitor — revisit next quarter"

suggested_action for new loans:
  >= 75: "Monitor closely — revisit in 18 months for refi conversation"
  >= 50: "Add to watch list — note sector and lender"
  <  50: "Low priority watch"

NAICS sector mapping (first 2 digits):
  62=Healthcare | 72=Accommodation/Food | 44-45=Retail | 23=Construction
  54=Professional Services | 81=Other Services | 52=Finance/Insurance
  48-49=Transportation | 42=Wholesale | 31-33=Manufacturing
  53=Real Estate | 61=Education | 56=Administrative | 51=Information
  71=Arts/Entertainment | 11=Agriculture | 92=Public Administration

Pass through rate and maturity fields unchanged from Contract 1 into every prospect record:
initial_interest_rate, rate_type, estimated_maturity_date, rate_delta, rate_delta_label.
Do not write prose. Do not fetch data.
Respond with ONLY valid raw JSON. No explanation, no markdown, no code fences.

Output schema:
{
  "meta": {
    "run_date": "<ISO date>",
    "data_as_of": "<string>",
    "period_label": "<e.g. Q1 2026>"
  },
  "executive_summary": {
    "total_new_loans": <integer>,
    "total_new_volume": <number>,
    "top_county_by_volume": "<string>",
    "dominant_sector": "<string>",
    "most_active_competitor": "<string>",
    "refi_candidates_total": <integer>,
    "refi_candidates_high_priority": <integer>
  },
  "competitor_intelligence": {
    "lender_rankings": [
      {
        "lender_name": "<string>",
        "loan_count": <integer>,
        "total_volume": <number>,
        "dominant_sector": "<string>",
        "trend": "<up | down | stable>",
        "notable": "<string or null>"
      }
    ],
    "market_share_by_county": {
      "FRANKLIN": [{"lender_name": "<string>", "pct_of_loans": <float>}],
      "ST. LOUIS": [],
      "ST. LOUIS CITY": []
    }
  },
  "sector_breakdown": [
    {
      "naics_sector": "<string>",
      "loan_count": <integer>,
      "total_volume": <number>,
      "avg_loan_size": <number>,
      "pct_of_quarter": <float>,
      "quarter_over_quarter_change": null,
      "top_county": "<string>"
    }
  ],
  "priority_prospects": [
    {
      "rank": <integer>,
      "borrower_name": "<string>",
      "borrower_city": "<string>",
      "borrower_street": "<string>",
      "borrower_zip": "<string>",
      "county": "<string>",
      "list_type": "refi_window",
      "lender_name": "<string>",
      "loan_amount": <number>,
      "months_since_approval": <integer>,
      "naics_description": "<string>",
      "priority_score": <integer>,
      "priority_rationale": {
        "loan_amount_pts": <integer>,
        "refi_window_pts": <integer>,
        "county_pts": <integer>,
        "sector_pts": <integer>,
        "total": <integer>
      },
      "suggested_action": "<string>",
      "initial_interest_rate": <float or null>,
      "rate_type": "<FIXED | VARIABLE | null>",
      "estimated_maturity_date": "<YYYY-MM-DD or null>",
      "rate_delta": <float or null>,
      "rate_delta_label": "<string>"
    }
  ],
  "new_loans_watch_list": [
    {
      "rank": <integer>,
      "borrower_name": "<string>",
      "borrower_city": "<string>",
      "borrower_street": "<string>",
      "borrower_zip": "<string>",
      "county": "<string>",
      "list_type": "new_loan",
      "lender_name": "<string>",
      "loan_amount": <number>,
      "approval_date": "<YYYY-MM-DD>",
      "naics_description": "<string>",
      "watch_score": <integer>,
      "watch_rationale": {
        "loan_amount_pts": <integer>,
        "county_pts": <integer>,
        "sector_pts": <integer>,
        "total": <integer>
      },
      "suggested_action": "<string>",
      "initial_interest_rate": <float or null>,
      "rate_type": "<FIXED | VARIABLE | null>",
      "estimated_maturity_date": "<YYYY-MM-DD or null>",
      "rate_delta": <float or null>,
      "rate_delta_label": "<string>"
    }
  ]
}
"""

SYNTHESIS_PROMPT = """
You are the Synthesis Agent for Project Pipeline, a commercial lending
intelligence system for community banks. You receive Contract 2 JSON and
write the final intelligence report in markdown.

The end client is SYNTHETIC COMMUNITY BANK, Washington MO.
Write for a loan officer who has 10 minutes on Monday morning.
Tone: direct, professional, action-oriented. No filler. No hedging.
Franklin County is always the primary focus.

Report structure — follow exactly in this order:

# Project Pipeline — Commercial Lending Intelligence
**SYNTHETIC COMMUNITY BANK | {period_label from contract2.meta.period_label — use exact value, do not substitute}**
*Data as of {data_as_of} | Generated {run_date}*

## Executive Summary
3-5 sentences. Lead with the single most actionable insight.
Cover: what the numbers mean for SYNTHETIC COMMUNITY BANK,
the most important competitive dynamic, one forward-looking signal.
This is the ONLY section where you exercise analytical judgment.
Keep under 150 words.

## Priority Refinancing Prospects
*Businesses that borrowed from competitors 18-36 months ago.*
For each prospect with priority_score >= 50:
**{rank}. {borrower_name}** — {borrower_city}, {county} County
**Sector:** {naics_description} | **Loan:** ${loan_amount} from {lender_name} | {months} months ago
**Rate:** {initial_interest_rate}% {rate_type} | **vs. Market:** {rate_delta_label} | **Matures:** {estimated_maturity_date formatted as "Month YYYY"}
> {suggested_action}
After the list: one short paragraph noting patterns in Franklin County.

## New Loans This Quarter — Competitor Watch List
*Businesses that borrowed from competitors this quarter.*
For each new loan with watch_score >= 50:
**{rank}. {borrower_name}** — {borrower_city}, {county} County
**Sector:** {naics_description} | **Loan:** ${loan_amount} from {lender_name} | Approved {approval_date human-readable}
**Rate:** {initial_interest_rate}% {rate_type} | **vs. Market:** {rate_delta_label} | **Matures:** {estimated_maturity_date formatted as "Month YYYY"}
> {suggested_action}
After the list: one sentence on most active competitor this quarter.

## Competitor Lender Activity
3-4 sentence paragraph on competitive landscape.
CRITICAL: The table below must use ONLY numbers from competitor_intelligence.lender_rankings in the Contract 2 JSON. Do not estimate, round, or invent any figures. If a field is null, write "—".
| Lender | Loans | Volume | Primary Sector | Trend |

## Sector Activity
2-3 sentences on dominant sectors and what they signal.
CRITICAL: The table below must use ONLY numbers from sector_breakdown in the Contract 2 JSON. Do not estimate, round, or invent any figures. If a field is null, write "—".
Then top 8 sectors table:
| Sector | Loans | Total Volume | Avg Loan Size | % of Quarter |

## Data Notes
- Source: SBA 7(a) FOIA dataset, data.sba.gov
- Coverage: Franklin County, St. Louis County, St. Louis City MO
- Refi window: 18-36 months from SBA approval date
- Approval date does not equal closing date (30-90 day lag typical)
- No phone/email in SBA data — prospect contact requires independent lookup
- **Rate benchmark:** >$350K → 9.50% | $50K–$350K → 10.00% | <$50K → 10.875% (April 2026 midpoints)
- **Rate delta:** positive = borrower paying above current market rate; negative = locked in below market
- **Variable rate loans:** rate delta based on origination rate only — current rate tracks market and has likely moved
- **Estimated maturity date:** calculated from approval date plus term months — confirm balloon vs. fully amortizing structure with borrower
- CANCLD loans flagged throughout — verify borrower debt status before contact
- Quarter-over-quarter trends available after second run

Rules:
- Dollar amounts with commas, no decimals: $285,000
- Percentages as whole numbers: 34%
- Dates human-readable: January 14, 2026
- Maturity dates as month + year only: November 2028
- Interest rates with two decimals: 9.75%
- If initial_interest_rate is null, write "rate unavailable" for the Rate line
- If rate_delta_label contains "(variable rate — origination rate only)", append *(variable)* after the rate value
- Only include records with score >= 50
- Do not reference scoring methodology
- Do not hallucinate borrower details
- Output clean markdown only — no JSON, no code fences
"""


# ── CORE FUNCTIONS ────────────────────────────────────────────────────────────

def run_agent(system_prompt: str, user_message: str, agent_name: str = "ingest") -> str:
    """
    Call OpenRouter with a system prompt and user message.

    agent_name must be one of: "ingest", "analysis", "synthesis"
    The model is resolved from the MODELS dict, which can be overridden
    per-agent via environment variables:
      PIPELINE_INGEST_MODEL
      PIPELINE_ANALYSIS_MODEL
      PIPELINE_SYNTHESIS_MODEL
    """
    import time

    model = MODELS.get(agent_name, MODELS["ingest"])
    print(f"   [{agent_name.upper()} AGENT] Using model: {model}")

    for attempt in range(5):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.1,
                max_tokens=65536,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
            )
            if response and response.choices:
                content = response.choices[0].message.content
                # Detect truncation via finish_reason
                finish = getattr(response.choices[0], 'finish_reason', None)
                if finish == 'length':
                    print(f"   Model output truncated (max tokens), retrying in 15s (attempt {attempt + 1}/5)...")
                    time.sleep(15)
                    continue
                if content:
                    return content

            print(f"   Empty response from API, retrying in 15s (attempt {attempt + 1}/5)...")
            time.sleep(15)

        except Exception as e:
            if "503" in str(e) or "429" in str(e):
                wait = (attempt + 1) * 15
                print(f"   API busy, retrying in {wait}s (attempt {attempt + 1}/5)...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"OpenRouter API unavailable after 5 attempts ({agent_name} agent, model: {model})")


def _repair_truncated_json(text):
    """Try to close unterminated strings/objects/arrays in truncated LLM output.
    Strategy: find the last fully-closed object boundary and truncate everything after.
    """
    stack = []
    in_string = False
    escaped = False
    last_array_object_end = None

    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
                if stack and stack[-1] == '[':
                    last_array_object_end = i + 1
            else:
                break
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()
            else:
                break

    if last_array_object_end is not None:
        text = text[:last_array_object_end].rstrip()
        # Recalculate stack for trimmed text
        stack = []
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                stack.append(ch)
            elif ch == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
            elif ch == ']':
                if stack and stack[-1] == '[':
                    stack.pop()

    close_map = {'{': '}', '[': ']'}
    return text + ''.join(close_map.get(s, '') for s in reversed(stack))


def extract_json(text):
    """Strip markdown fences and extract valid JSON from model output."""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    # Attempt 1: straight parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: replace invalid \uXXXX escapes that some models emit incorrectly
    try:
        cleaned = re.sub(r'\\u([0-9a-fA-F]{0,3}[^0-9a-fA-F])', r'\\\\u\1', text)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 3: repair truncated output (LLM hit max tokens mid-response)
    try:
        repaired = _repair_truncated_json(text)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Attempt 3b: more aggressive — drop the last incomplete array element entirely
    # Find the last "}\s*[,]\s*\{" pattern and truncate before the incomplete object
    try:
        # Find last complete "} followed by comma or whitespace then {"
        match = None
        for m in re.finditer(r'\}\s*,\s*\{', text):
            match = m
        if match:
            # Keep everything up to and including the complete object
            cut_pos = match.start() + 1  # after the }
            trimmed = text[:cut_pos].rstrip()
            repaired = _repair_truncated_json(trimmed)
            return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Attempt 4: encode/decode with error replacement
    try:
        return json.loads(text.encode('utf-8', errors='replace').decode('utf-8'))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model did not return valid JSON.\n"
            f"Error: {e}\n"
            f"Raw output (first 500 chars):\n{text[:500]}"
        )


def fetch_and_filter():
    """Fetch SBA CSVs and filter to target geography. Returns a DataFrame."""
    frames = []

    for label, url in SBA_URLS.items():
        print(f"   Fetching {label}...")
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text), low_memory=False)
            df["_source"] = label
            frames.append(df)
            print(f"   {label}: {len(df):,} rows")
        except Exception as e:
            print(f"   WARNING: Could not fetch {label}: {e}")

    if not frames:
        raise SystemExit("ERROR: No SBA data files could be fetched.")

    raw = pd.concat(frames, ignore_index=True)
    print(f"   Total rows fetched: {len(raw):,}")

    raw.columns = [c.strip() for c in raw.columns]

    state_col  = next((c for c in raw.columns
                       if "state"  in c.lower() and "project" in c.lower()), None)
    county_col = next((c for c in raw.columns
                       if "county" in c.lower() and "project" in c.lower()), None)

    if not state_col or not county_col:
        print(f"   Available columns: {list(raw.columns)}")
        raise SystemExit("ERROR: Could not find ProjectState or ProjectCounty columns.")

    print(f"   State column:  {state_col}")
    print(f"   County column: {county_col}")

    mo = raw[raw[state_col].str.strip().str.upper() == TARGET_STATE].copy()
    print(f"   Missouri rows: {len(mo):,}")

    mo["_county_clean"] = (
        mo[county_col].fillna("").str.strip().str.upper()
        .str.replace(r"\bCOUNTY\b", "", regex=True)
        .str.replace(r"\bCO\.\b",   "", regex=True)
        .str.strip()
    )

    market = mo[mo["_county_clean"].isin(TARGET_COUNTIES)].copy()
    print(f"   Target market rows: {len(market):,}")

    return market


def _sba_benchmark_rate(gross_approval: float) -> float:
    """Current SBA 7(a) market rate benchmark by loan size tier (April 2026).
    Source: SBA rate caps applied to WSJ Prime ~7.5%
      > $350K  → 9.25–9.75%  (midpoint 9.50%)
      $50K–$350K → 9.75–10.25% (midpoint 10.00%)
      < $50K   → 10.25–11.50% (midpoint 10.875%)
    Update this function each quarter as rates change.
    """
    if gross_approval >= 350_000:
        return 9.50
    if gross_approval >= 50_000:
        return 10.00
    return 10.875


def _sanitize(val: str) -> str:
    """Remove or replace characters that cause JSON encoding issues in model output."""
    if not val:
        return val
    # Replace common problem characters with safe ASCII equivalents
    return (val
            .replace('\u0026', '&')
            .replace('\u0027', "'")
            .replace('\u003c', '<')
            .replace('\u003e', '>')
            .encode('ascii', errors='replace')
            .decode('ascii')
            .replace('?', ' ')
            .strip())


def build_ingest_payload(df):
    """Convert filtered DataFrame to a JSON payload for the Ingest agent."""
    today = date.today()

    date_col    = next((c for c in df.columns if "approvaldate" in c.lower().replace(" ", "").replace("_", "")), None)
    amount_col  = next((c for c in df.columns if "grossapproval" in c.lower().replace(" ", "").replace("_", "")), None)
    lender_col  = next((c for c in df.columns if c.lower() in ["bank", "bankname", "lendername", "lender_name"]), None)
    status_col  = next((c for c in df.columns if "loanstatus" in c.lower().replace(" ", "").replace("_", "")), None)
    naics_col   = next((c for c in df.columns if "naicscode" in c.lower().replace(" ", "").replace("_", "")), None)
    naics_desc  = next((c for c in df.columns if "naicsdescription" in c.lower().replace(" ", "").replace("_", "")), None)
    borr_name   = next((c for c in df.columns if "borrname" in c.lower().replace(" ", "").replace("_", "") or c.lower() == "borrowername"), None)
    borr_street = next((c for c in df.columns if "borrstreet" in c.lower().replace(" ", "").replace("_", "")), None)
    borr_city   = next((c for c in df.columns if "borrcity" in c.lower().replace(" ", "").replace("_", "")), None)
    borr_zip    = next((c for c in df.columns if "borrzip" in c.lower().replace(" ", "").replace("_", "")), None)
    term_col    = next((c for c in df.columns if "terminmonths" in c.lower().replace(" ", "").replace("_", "")), None)
    jobs_col    = next((c for c in df.columns if "jobssupported" in c.lower().replace(" ", "").replace("_", "")), None)
    program_col = next((c for c in df.columns if c.lower() in ["program", "loanprogram"]), None)
    rate_col    = next((c for c in df.columns if "initialinterestrate" in c.lower().replace(" ", "").replace("_", "")), None)
    rate_type_col = next((c for c in df.columns if "fixedorvariable" in c.lower().replace(" ", "").replace("_", "")), None)

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    records = []
    for _, row in df.iterrows():
        approval = row[date_col]
        if pd.isnull(approval):
            continue

        months_ago = (today.year - approval.year) * 12 + (today.month - approval.month)

        county_raw = str(row.get("_county_clean", "")).strip().upper()
        if county_raw in ["ST LOUIS", "SAINT LOUIS"]:
            county = "ST. LOUIS"
        elif county_raw == "FRANKLIN":
            county = "FRANKLIN"
        else:
            county = county_raw

        status = str(row.get(status_col, "")).strip().upper() if status_col else ""

        orig_rate = None
        if rate_col:
            raw_rate = row.get(rate_col)
            if raw_rate is not None and not (isinstance(raw_rate, float) and pd.isnull(raw_rate)):
                try:
                    orig_rate = round(float(raw_rate), 2)
                except (ValueError, TypeError):
                    pass

        rate_type_val = None
        if rate_type_col:
            rt = str(row.get(rate_type_col, "")).strip().upper()
            if rt in ("V", "VARIABLE"):
                rate_type_val = "VARIABLE"
            elif rt in ("F", "FIXED"):
                rate_type_val = "FIXED"

        gross = float(row.get(amount_col, 0)) if amount_col else 0
        term_raw = row.get(term_col) if term_col else None
        term_int = None
        if term_raw is not None and not (isinstance(term_raw, float) and pd.isnull(term_raw)):
            try:
                term_int = int(term_raw)
            except (ValueError, TypeError):
                pass

        maturity_date = None
        if term_int:
            maturity_date = (approval + relativedelta(months=term_int)).strftime("%Y-%m-%d")

        rate_delta = None
        rate_delta_label = "rate data unavailable"
        if orig_rate and orig_rate > 0:
            bench = _sba_benchmark_rate(gross)
            rate_delta = round(orig_rate - bench, 2)
            caveat = " (variable rate — origination rate only)" if rate_type_val == "VARIABLE" else ""
            if rate_delta > 0:
                rate_delta_label = f"+{rate_delta}% above current market{caveat}"
            elif rate_delta < 0:
                rate_delta_label = f"{rate_delta}% below current market{caveat}"
            else:
                rate_delta_label = f"at current market rate{caveat}"

        rec = {
            "borrower_name":           _sanitize(str(row.get(borr_name,   "")).strip().upper()) if borr_name   else "UNKNOWN",
            "borrower_street":         _sanitize(str(row.get(borr_street, "")).strip().upper()) if borr_street else "ADDRESS UNAVAILABLE",
            "borrower_city":           _sanitize(str(row.get(borr_city,   "")).strip().upper()) if borr_city   else "",
            "borrower_zip":            str(row.get(borr_zip,    "")).strip()         if borr_zip    else "",
            "county":                  county,
            "lender_name":             _sanitize(str(row.get(lender_col,  "")).strip().upper()) if lender_col  else "UNKNOWN",
            "program":                 str(row.get(program_col, "7A")).strip().upper() if program_col else "7A",
            "gross_approval":          gross,
            "approval_date":           approval.strftime("%Y-%m-%d"),
            "naics_code":              str(row.get(naics_col,  "")).strip() if naics_col  else "",
            "naics_description":       str(row.get(naics_desc, "")).strip().upper() if naics_desc else "",
            "term_months":             term_int,
            "jobs_supported":          int(row.get(jobs_col, 0)) if jobs_col and not pd.isnull(row.get(jobs_col, None)) else None,
            "loan_status":             status,
            "months_since_approval":   months_ago,
            "initial_interest_rate":   orig_rate,
            "rate_type":               rate_type_val,
            "estimated_maturity_date": maturity_date,
            "rate_delta":              rate_delta,
            "rate_delta_label":        rate_delta_label,
        }
        records.append(rec)

    MIN_LOAN_AMOUNT = 500_000  # SYNTHETIC COMMUNITY BANK minimum threshold

    new_loans = [r for r in records
                 if r["months_since_approval"] <= 6
                 and r["gross_approval"] >= MIN_LOAN_AMOUNT]

    refi_candidates = [r for r in records
                       if 18 <= r["months_since_approval"] <= 36
                       and r["gross_approval"] >= MIN_LOAN_AMOUNT
                       and "PIF"    not in r["loan_status"]
                       and "CHGOFF" not in r["loan_status"]]

    print(f"   Pre-filtered: {len(new_loans)} new loans, "
          f"{len(refi_candidates)} refi candidates (>= ${MIN_LOAN_AMOUNT:,})")
    print(f"   (Dropped {len(records) - len(new_loans) - len(refi_candidates)} "
          f"records below threshold or outside window)")

    return {
        "total_records":        len(records),
        "new_loans_count":      len(new_loans),
        "refi_candidates_count": len(refi_candidates),
        "today":                today.isoformat(),
        "new_loans":            new_loans,
        "refi_candidates":      refi_candidates,
    }


def halt_if_empty(contract1):
    """Zero-volume alert — halt if no Franklin County records found."""
    franklin_new  = [r for r in contract1.get("new_loans", [])
                     if r.get("county") == "FRANKLIN"]
    franklin_refi = [r for r in contract1.get("refi_candidates", [])
                     if r.get("county") == "FRANKLIN"]

    if not franklin_new and not franklin_refi:
        print("\nHALT: No Franklin County records in Contract 1.")
        print("Check SBA data URL and verify the FOIA file has been updated.")
        print("See runbook troubleshooting before proceeding.")
        raise SystemExit(1)

    print(f"   Franklin County: {len(franklin_new)} new loans, "
          f"{len(franklin_refi)} refi candidates")


def run_ingest_batched(slim_payload, batch_size=15):
    """Run the Ingest agent on batches of records to avoid output token limits.
    Splits new_loans and refi_candidates into chunks, processes each, then merges.
    """
    new_loans = slim_payload["new_loans"]
    refi_candidates = slim_payload["refi_candidates"]
    today = slim_payload["today"]

    new_batches = [new_loans[i:i+batch_size] for i in range(0, len(new_loans), batch_size)]
    refi_batches = [refi_candidates[i:i+batch_size] for i in range(0, len(refi_candidates), batch_size)]

    total_batches = len(new_batches) + len(refi_batches)
    if total_batches <= 1:
        # Small enough to send in one go
        return None

    print(f"   Batching: {len(new_batches)} new-loan batches + {len(refi_batches)} refi batches = {total_batches} total")

    all_new_loans = []
    all_refi = []
    total_dropped = 0

    batch_prompt_suffix = (
        "\n\nStructure these pre-classified SBA loan records into JSON.\n"
        "new_loans approved in last 90 days. refi_candidates are 18-36 months old.\n"
        "Return ONLY a JSON object with two keys: \"new_loans\" and \"refi_candidates\", "
        "each containing an array of cleaned records matching the Contract 1 schema.\n"
        "No meta section needed. No explanation, no markdown, no code fences."
    )

    for i, batch in enumerate(new_batches):
        print(f"   Processing new-loan batch {i+1}/{len(new_batches)} ({len(batch)} records)...")
        payload_chunk = {"today": today, "new_loans": batch, "refi_candidates": []}
        raw = run_agent(
            INGEST_PROMPT,
            f"{json.dumps(payload_chunk)}{batch_prompt_suffix}",
            agent_name="ingest",
        )
        result = extract_json(raw)
        # Handle both {"new_loans": [...], "refi_candidates": [...]} and raw arrays
        if isinstance(result, list):
            for rec in result:
                if rec.get("months_since_approval", 999) <= 6:
                    all_new_loans.append(rec)
                else:
                    all_refi.append(rec)
        else:
            all_new_loans.extend(result.get("new_loans", []))
            all_refi.extend(result.get("refi_candidates", []))

    for i, batch in enumerate(refi_batches):
        print(f"   Processing refi batch {i+1}/{len(refi_batches)} ({len(batch)} records)...")
        payload_chunk = {"today": today, "new_loans": [], "refi_candidates": batch}
        raw = run_agent(
            INGEST_PROMPT,
            f"{json.dumps(payload_chunk)}{batch_prompt_suffix}",
            agent_name="ingest",
        )
        result = extract_json(raw)
        # Handle both {"new_loans": [...], "refi_candidates": [...]} and raw arrays
        if isinstance(result, list):
            for rec in result:
                if rec.get("months_since_approval", 999) <= 6:
                    all_new_loans.append(rec)
                else:
                    all_refi.append(rec)
        else:
            all_new_loans.extend(result.get("new_loans", []))
            all_refi.extend(result.get("refi_candidates", []))

    # Build the merged Contract 1
    total_processed = len(slim_payload["new_loans"]) + len(slim_payload["refi_candidates"])
    # Deduplicate by (borrower_name, approval_date, lender_name)
    seen = set()
    deduped_new = []
    for rec in all_new_loans:
        key = (rec.get("borrower_name"), rec.get("approval_date"), rec.get("lender_name"))
        if key not in seen:
            seen.add(key)
            deduped_new.append(rec)

    seen = set()
    deduped_refi = []
    for rec in all_refi:
        key = (rec.get("borrower_name"), rec.get("approval_date"), rec.get("lender_name"))
        if key not in seen:
            seen.add(key)
            deduped_refi.append(rec)

    total_output = len(deduped_new) + len(deduped_refi)
    total_dropped = total_processed - total_output

    return {
        "meta": {
            "run_date": today,
            "data_as_of": "2025-12-31",
            "counties": ["FRANKLIN", "ST. LOUIS", "ST. LOUIS CITY"],
            "state": "MO",
            "new_loans_period": f"{(datetime.now() - relativedelta(months=3)).strftime('%Y-%m-%d')} to {today}",
            "total_new_loans": len(deduped_new),
            "total_refi_candidates": len(deduped_refi),
            "records_processed": total_processed,
            "records_dropped": total_dropped,
            "data_warnings": [],
        },
        "new_loans": deduped_new,
        "refi_candidates": deduped_refi,
    }


def run_pipeline(quarter_id):
    """Run the full three-agent pipeline for a given quarter."""
    path = f"pipeline_runs/{quarter_id}"
    os.makedirs(path, exist_ok=True)

    print(f"\nProject Pipeline — {quarter_id}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    print(f"\nModel configuration:")
    for agent, model in MODELS.items():
        print(f"   {agent.capitalize():<12} → {model}")

    # ── STEP 1: FETCH AND FILTER ──────────────────────────────────────────────
    print("\nStep 1: Fetching and filtering SBA data...")
    df = fetch_and_filter()
    payload = build_ingest_payload(df)
    print(f"   Payload ready: {payload['total_records']} records")

    # ── STEP 2: INGEST AGENT ──────────────────────────────────────────────────
    print("\nStep 2: Running Ingest & Filter Agent...")
    slim_payload = {
        "today":           payload["today"],
        "new_loans":       payload["new_loans"],
        "refi_candidates": payload["refi_candidates"],
    }
    print(f"   Sending {len(slim_payload['new_loans'])} new loans + "
          f"{len(slim_payload['refi_candidates'])} refi candidates to Ingest agent")

    # Try batched ingest first if payload is large
    contract1 = run_ingest_batched(slim_payload)

    if contract1 is None:
        # Payload small enough for single call
        c1_raw = run_agent(
            INGEST_PROMPT,
            f"Structure these pre-classified SBA loan records into Contract 1 JSON.\n"
            f"new_loans approved in last 90 days. refi_candidates are 18-36 months old.\n\n"
            f"{json.dumps(slim_payload)}",
            agent_name="ingest",
        )
        contract1 = extract_json(c1_raw)
    halt_if_empty(contract1)

    with open(f"{path}/contract1.json", "w") as f:
        json.dump(contract1, f, indent=2)
    print(f"   Contract 1 saved — "
          f"{contract1['meta']['total_new_loans']} new loans, "
          f"{contract1['meta']['total_refi_candidates']} refi candidates")

    # ── STEP 3: ANALYSIS AGENT ────────────────────────────────────────────────
    print("\nStep 3: Running Analysis Agent...")
    c2_raw = run_agent(
        ANALYSIS_PROMPT,
        f"Analyze this Contract 1 JSON and return Contract 2 JSON.\n\n"
        f"{json.dumps(contract1)}",
        agent_name="analysis",
    )
    contract2 = extract_json(c2_raw)

    with open(f"{path}/contract2.json", "w") as f:
        json.dump(contract2, f, indent=2)
    print(f"   Contract 2 saved — "
          f"{contract2['executive_summary']['refi_candidates_high_priority']} "
          f"high priority prospects")

    # ── STEP 4: SYNTHESIS AGENT ───────────────────────────────────────────────
    print("\nStep 4: Running Synthesis Agent...")
    report = run_agent(
        SYNTHESIS_PROMPT,
        f"Generate the Pipeline intelligence report in markdown "
        f"from this Contract 2 JSON.\n\n{json.dumps(contract2)}",
        agent_name="synthesis",
    )

    report_path = f"{path}/report.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"   Report saved to {report_path}")

    print(f"\nPipeline complete — {datetime.now().strftime('%H:%M')}")
    print(f"Files saved to {path}/")
    # Report contents stay in the output file rather than process logs.


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("\nProject Pipeline — connection test")
        print("=" * 40)
        print(f"\nModel configuration:")
        for agent, model in MODELS.items():
            print(f"   {agent.capitalize():<12} → {model}")

        print("\n1. Testing OpenRouter connection (ingest model)...")
        result = run_agent(
            "You return raw JSON only. No explanation.",
            'Return this exact JSON: {"status": "connected"}',
            agent_name="ingest",
        )
        parsed = extract_json(result)
        print(f"   Parsed OK: {parsed}")

        print("\n2. Testing synthesis model...")
        result2 = run_agent(
            "You return raw JSON only. No explanation.",
            'Return this exact JSON: {"status": "connected"}',
            agent_name="synthesis",
        )
        parsed2 = extract_json(result2)
        print(f"   Parsed OK: {parsed2}")

        print("\nAll systems go. Run the pipeline with:")
        print("   python3 pipeline.py 2026-Q2")

    elif len(sys.argv) > 1:
        run_pipeline(sys.argv[1])

    else:
        print("Usage:")
        print("  python3 pipeline.py test        — test all model connections")
        print("  python3 pipeline.py 2026-Q2     — run full pipeline")
        print()
        print("Model overrides (set in .env or shell):")
        print("  PIPELINE_INGEST_MODEL=google/gemini-2.0-flash-001")
        print("  PIPELINE_ANALYSIS_MODEL=deepseek/deepseek-chat")
        print("  PIPELINE_SYNTHESIS_MODEL=anthropic/claude-sonnet-4-5")