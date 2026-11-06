export interface ScoreBreakdown {
  amount_pts: number;
  window_pts: number;
  county_pts: number;
  base_pts: number;
}

export interface Prospect {
  id: string;
  type: "refi" | "watch";
  borrower_name: string;
  borrower_street: string;
  borrower_city: string;
  borrower_zip: string;
  county: string;
  lender_name: string;
  lender_state: string;
  lender_type: "in_state" | "out_of_state" | "unknown";
  program: string;
  gross_approval: number;
  approval_date: string;
  months_since_approval: number;
  months_remaining: number | null;
  term_months: number | null;
  maturity_date: string | null;
  naics_code: string;
  naics_description: string;
  jobs_supported: number | null;
  loan_status: string;
  initial_interest_rate: number | null;
  rate_type: "FIXED" | "VARIABLE" | null;
  benchmark_rate: number;
  rate_delta: number | null;
  rate_delta_label: string;
  annual_savings: number | null;
  priority_score: number;
  priority: "HIGH" | "MEDIUM" | "MONITOR";
  score_breakdown: ScoreBreakdown;
}

export interface TopCompetitor {
  lender: string;
  count: number;
  volume: number;
}

export interface PrimeRateInfo {
  rate: number;
  source: string;
  date: string;
}

export interface PipelineData {
  meta: {
    generated_at: string;
    data_as_of: string;
    target_counties: string[];
    refi_window_months: string;
    min_loan_amount: number;
    prime_rate: PrimeRateInfo;
    total_refi_candidates: number;
    total_watch_list: number;
  };
  summary: {
    total_refi_candidates: number;
    total_watch_list: number;
    total_refi_volume: number;
    high_priority_count: number;
    out_of_state_lender_count: number;
    avg_rate_delta: number | null;
    total_annual_savings_available: number;
    county_breakdown: Record<string, number>;
    top_competitors: TopCompetitor[];
  };
  refi_candidates: Prospect[];
  watch_list: Prospect[];
}
