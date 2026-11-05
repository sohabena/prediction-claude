// Response shapes for the Today page. Field names match the backend
// (GET /api/paper/live, /api/paper/evidence, /api/data/matches) and the
// samples in frontend/fixtures/.

export interface LatestScore {
  at: string;
  is_live: boolean;
  score: number | null;
  wickets: number | null;
  overs: number | null;
  innings: number | null;
  batting_team: string | null;
  bowling_team: string | null;
  status: string | null;
  match_format: string | null;
}

export interface Book {
  back_home: number | null;
  lay_home: number | null;
  back_away: number | null;
  lay_away: number | null;
}

export interface Trade {
  decided_at: string;
  variant: string;
  placebo: boolean;
  side: "BACK" | "LAY" | string;
  selection: "home" | "away" | string;
  team: string;
  in_play: boolean;
  price_limit: number | null;
  price_filled: number | null;
  stake: number;
  /** filled | unfilled | pending (the live feed does not send a reason yet). */
  status: string;
  reason?: string | null;
  p_used: number | null;
  pnl: number | null;
  result: string | null;
}

export interface VariantLive {
  variant: string;
  placebo: boolean;
  pnl_if_home: number | null;
  pnl_if_away: number | null;
  worst_case: number;
  budget: number;
  hard_limit: number;
  worst_share_of_budget: number;
  fills: number;
  pending: number;
  tickets_used: number;
  ticket_budget: number;
  last_trades: Trade[];
}

export interface LiveMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string | null;
  in_play: boolean;
  start: string | null;
  match_start_time: string | null;
  actual_start_time: string | null;
  latest_score: LatestScore | null;
  book: Book;
  p_market: number | null;
  p_model: number | null;
  variants: VariantLive[];
  last_trades: Trade[];
}

export interface TraderCycle {
  cycle_at: string | null;
  age_seconds: number | null;
  stale: boolean;
  reference: string | null;
  candidates: number;
  considered: number;
  intents: number;
  guard_refusals: Record<string, number>;
  suspended: unknown[];
}

export interface PaperLive {
  generated_at: string;
  virtual_only: boolean;
  budget: number;
  hard_limit: number;
  cycle: TraderCycle | null;
  matches_logged: number;
  matches: LiveMatch[];
}

/** Only the fields the Today page reads from /api/paper/evidence. */
export interface PaperEvidence {
  generated_at: string;
  r3_passed: boolean;
}

export interface DataMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string | null;
  simulated: boolean;
  /** live | pre-match | finished */
  state: string;
  start: string | null;
}

/** Only the fields the Today page reads from /api/data/matches. */
export interface DataMatchesResponse {
  generated_at: string;
  matches: DataMatch[];
}
