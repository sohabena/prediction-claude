// Response shape of GET /api/paper/evidence (backend/routers/paper.py build_evidence).
// Sample: frontend/fixtures/api_paper_evidence.json. Numeric fields the backend can leave uncomputed are nullable.

export interface Progress {
  matches: number | null;
  target: number | null;
}

export interface EvidenceTradable {
  real_matches: number | null;
  settled_real: number | null;
  tradable_settled: number | null;
  tradable_settled_recent: number | null;
  recent_days: number | null;
  r1_matches: number | null;
  r1_target: number | null;
  r2_matches: number | null;
  r2_target: number | null;
  r2_cutoff: string | null;
}

export interface ClvStat {
  mean: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  matches: number;
  bets: number;
  too_few_matches: boolean;
}

export interface PnlStat {
  total: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  matches: number;
  bets: number;
}

export interface SegmentStat {
  matches: number | null;
  bets: number | null;
  clv_mean: number | null;
  clv_ci_lo: number | null;
  clv_ci_hi: number | null;
  clv_matches: number | null;
  pnl: number | null;
}

export interface VariantR3 {
  met: boolean;
  settled_bets: number;
  settled_bets_target: number;
  settled_matches: number;
  settled_matches_target: number;
  clv_ci_lo_above_zero: boolean;
}

export interface VariantEvidence {
  variant: string;
  placebo: boolean;
  bets: number;
  filled: number;
  unfilled: number;
  pending: number;
  fill_rate: number | null;
  matches: number;
  settled_bets: number;
  settled_matches: number;
  void_bets: number;
  clv: ClvStat;
  pnl: PnlStat;
  segments: {
    prematch: SegmentStat;
    inplay: SegmentStat;
  };
  r3: VariantR3;
  max_worst_exposure: number | null;
}

// One variant's excess CLV over the control (placebo_random), for one segment.
export interface ExcessCell {
  matches: number;
  bets: number;
  control_bets: number;
  estimate: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  too_few_matches: boolean;
  above_zero: boolean;
}

export interface ExcessOverPlacebo {
  control: string;
  pre_match: Record<string, ExcessCell>;
  in_play: Record<string, ExcessCell>;
  sentence_pre_match: string | null;
  sentence_in_play: string | null;
  sentence_all_bets: string | null;
}

export interface InPlayGateRow {
  format: string;
  open: boolean;
  matches: number;
  logloss_diff: number | null;
}

export interface FillVariant {
  variant: string;
  fill_rate: number | null;
  stake_fill_rate: number | null;
  size_capped_share: number | null;
  size_unknown_share: number | null;
  mean_adverse_slippage_pct: number | null;
}

export interface EvidenceResponse {
  generated_at: string;
  virtual_only: boolean;
  review_day: string | null;
  first_decided_at: string | null;
  last_decided_at: string | null;
  bets_seen: number | null;
  r3_passed: boolean;
  r3_target: {
    settled_bets: number;
    matches: number;
  };
  tradable: EvidenceTradable;
  progress: {
    r1: Progress;
    r2: Progress & { cutoff: string | null };
    r3: {
      target_matches: number;
      target_settled_bets: number;
    };
  };
  variants: VariantEvidence[];
  excess_over_placebo: ExcessOverPlacebo;
  in_play_gate: InPlayGateRow[];
  fill_realism: {
    live_size_cap: boolean;
    variants: FillVariant[];
  };
  missing_results: number;
  daily_report: {
    file: string;
    date: string;
    bytes: number;
    modified_at: string;
  } | null;
}
