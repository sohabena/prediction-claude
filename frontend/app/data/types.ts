// Types for the Data page (S94). Shapes follow backend/routers/data.py and frontend/fixtures/api_data_*.json.

export type CheckStatus = "ok" | "warn" | "fail" | string;

export interface QualityCheck {
  name: string;
  status: CheckStatus;
  value: number | string | boolean | null;
  detail: string | null;
}

// GET /api/data/health
export interface HealthResponse {
  available: boolean;
  ts: string | null;
  age_seconds: number | null;
  window_hours: number | null;
  overall: CheckStatus | null;
  exit_code: number | null;
  checks: QualityCheck[];
}

// GET /api/data/missing-results
export interface MissingResultItem {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  match_start_time: string | null;
  last_tick: string;
  age_hours: number;
  result_type: string | null;
  has_result: boolean;
}

export interface MissingResultsResponse {
  generated_at: string;
  count: number;
  lookback_days: number;
  stale_after_hours: number;
  items: MissingResultItem[];
}

// GET /api/data/matches?days=3&include_simulated=true|false
export type MatchState = "live" | "pre-match" | "stumps" | "finished";
export type ResultStatus = "decided" | "not_started" | "missing" | "awaiting";

export interface MatchResultInfo {
  result_type: string | null;
  winner: string | null;
  completed_at: string | null;
  decided: boolean;
}

export interface RecordedMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  simulated: boolean;
  state: MatchState;
  start: string | null;
  actual_start: string | null;
  actual_start_source: string | null;
  first_live_tick: string | null;
  last_tick: string | null;
  odds_rows: number | null;
  match_page_seen: boolean;
  score_pipeline: string | null;
  result: MatchResultInfo;
  result_status: ResultStatus;
}

export interface MatchesSummary {
  live: number;
  "pre-match": number;
  stumps?: number;
  finished: number;
  missing_result: number;
}

export interface MatchesResponse {
  generated_at: string;
  days: number;
  include_simulated: boolean;
  count: number;
  summary: MatchesSummary;
  matches: RecordedMatch[];
}

// POST /api/data/results
export type ResultOutcome = "winner" | "tie" | "no_result";

export interface ResultRequest {
  match_id: string;
  outcome: ResultOutcome;
  winner?: string;
  margin?: string;
  url: string;
}

export interface ResultWritten {
  written: boolean;
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  is_simulated: boolean;
  result: {
    result_type: string;
    winner: string;
    loser: string;
    margin: string | null;
    source: string;
  };
  replaced: string | null;
  paper_bets_settled: number;
  bets_settled: Record<string, number>;
  url: string;
  checked_at: string;
}
