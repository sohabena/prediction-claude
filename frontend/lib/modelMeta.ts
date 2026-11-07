export type ModelSectionId = "green_up" | "directional" | "dynamics" | "controls";

export interface ModelSection {
  id: ModelSectionId;
  title: string;
  badge: string;
  badgeClass: string;
  borderClass: string;
  headerBg: string;
  description: string;
  order: number;
}

export interface ModelMeta {
  code: string;
  displayName: string;
  shortTag: string;
  section: ModelSectionId;
  badge?: string;
  badgeClass?: string;
  description: string;
  mechanics: string;
  keyStats: {
    strategyType: string;
    targetOdds: string;
    minEdge: string;
    exitRule: string;
    lossProtection: string;
  };
}

export const MODEL_SECTIONS: Record<ModelSectionId, ModelSection> = {
  green_up: {
    id: "green_up",
    title: "Guaranteed Green-Up Specialists",
    badge: "Zero Downside Risk",
    badgeClass: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
    borderClass: "border-emerald-500/30",
    headerBg: "bg-emerald-950/20",
    description: "Single-mandate institutional models with automated green-up that equalize profits across both outcomes as soon as an edge materializes.",
    order: 1,
  },
  directional: {
    id: "directional",
    title: "Directional Kelly Models",
    badge: "Edge Accumulators",
    badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
    borderClass: "border-sky-500/30",
    headerBg: "bg-sky-950/20",
    description: "Traditional predictive models that hold unhedged directional positions sized with fractional Kelly based on model conviction vs market odds.",
    order: 2,
  },
  dynamics: {
    id: "dynamics",
    title: "Market Dynamics (Order Flow)",
    badge: "Tick Momentum",
    badgeClass: "bg-amber-500/10 text-amber-400 border-amber-500/30",
    borderClass: "border-amber-500/30",
    headerBg: "bg-amber-950/20",
    description: "Trained purely on exchange order book depth, tick velocity, and liquidity imbalances without match scorecard dependencies.",
    order: 3,
  },
  controls: {
    id: "controls",
    title: "Benchmarks & Controls",
    badge: "Scientific Null Baseline",
    badgeClass: "bg-zinc-700/40 text-zinc-400 border-zinc-600/40",
    borderClass: "border-zinc-800/80",
    headerBg: "bg-zinc-950/40",
    description: "Baseline controls, tail comeback models, and placebo benchmarks used to prove statistical significance and alpha excess.",
    order: 4,
  },
};

export const MODEL_REGISTRY: Record<string, ModelMeta> = {
  // ----------------- GREEN-UP SPECIALISTS -----------------
  bmodel_green_v1: {
    code: "bmodel_green_v1",
    displayName: "Fundamental Value Green-Up",
    shortTag: "Cricket State · 6% Edge",
    section: "green_up",
    badge: "Auto Green-Up",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Analyzes in-play match fundamentals (overs, wickets, required run-rate, target) against live market odds with a balanced 50/50 model/market blend.",
    mechanics: "Enters when a ≥6% mispricing is identified. The instant market odds contract or move in our favor, it executes an equalizing green-up order to lock in guaranteed profit on both teams (Worst Case = 0) and freezes trading.",
    keyStats: {
      strategyType: "Fundamental Value Arbitrage",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Immediate Auto Green-Up",
      lossProtection: "Strict Double-Negative Guard",
    },
  },
  bmodel_scalp_v1: {
    code: "bmodel_scalp_v1",
    displayName: "Momentum Scalper (High Conviction)",
    shortTag: "Order Flow · 6% Edge",
    section: "green_up",
    badge: "Pure Momentum",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Harnesses high-frequency exchange order flow momentum and crowd overreactions based on micro-price action.",
    mechanics: "Triggers on powerful tick velocity moves (w=1.0, ≥6% edge). Takes rapid 1–2 tick profits via automated green-up, shielded by strict ticket budgets and churn guards.",
    keyStats: {
      strategyType: "Order Flow Micro-Scalp",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "1–2 Tick Auto Green-Up",
      lossProtection: "Hard Ticket Cap (15 max)",
    },
  },
  bmodel_scalp_fast: {
    code: "bmodel_scalp_fast",
    displayName: "High-Frequency Micro-Scalper",
    shortTag: "Micro-Swings · 3% Edge",
    section: "green_up",
    badge: "Fast Scalp",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Fast-reacting scalper engineered to extract micro-profits from frequent in-play price vibrations during active overs.",
    mechanics: "Sizes smaller positions (15k risk cap) on 3% momentum edges. Locks fractional green profits on quick price reversals with rapid turnover.",
    keyStats: {
      strategyType: "High-Frequency Liquidity Scalp",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Micro-Rebound Green-Up",
      lossProtection: "15k Trade Cap + Churn Guard",
    },
  },
  bmodel_swing_v1: {
    code: "bmodel_swing_v1",
    displayName: "50-50 Parity Swing Specialist",
    shortTag: "Parity Arbitrage · 4% Edge",
    section: "green_up",
    badge: "Parity Lock",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Capitalizes on cricket's natural back-and-forth oscillations around the 50-50 market equilibrium (odds 1.70–3.00).",
    mechanics: "Takes contrarian value when market odds swing too wide from fair value, and executes a full profit equalization trade the instant the match swings back across 50-50 parity.",
    keyStats: {
      strategyType: "Mean Reversion / Parity Swing",
      targetOdds: "1.70 – 3.00",
      minEdge: "4.0%",
      exitRule: "Parity Crossing Equalization",
      lossProtection: "Double-Negative Block",
    },
  },
  bmodel_deep_v1: {
    code: "bmodel_deep_v1",
    displayName: "Deep Value / Panic Contrarian",
    shortTag: "Outlier Rebound · 8% Edge",
    section: "green_up",
    badge: "High Conviction",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Exploits irrational retail panic following early top-order wicket collapses or boundary bursts.",
    mechanics: "Demands a massive 8% statistical edge in the 2.10–3.80 odds range with conservative shrinkage (w=0.4). Automatically locks in green profit once the innings stabilizes.",
    keyStats: {
      strategyType: "Contrarian Deep Value",
      targetOdds: "2.10 – 3.80",
      minEdge: "8.0%",
      exitRule: "Stabilization Green-Up",
      lossProtection: "Strict 10-Ticket Limit",
    },
  },
  bmodel_fav_lock_v1: {
    code: "bmodel_fav_lock_v1",
    displayName: "Dominant Favourite Lockdown",
    shortTag: "Leader Lockdown · 4% Edge",
    section: "green_up",
    badge: "Leader Lock",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Targets commanding match leaders and runaway run chases in the 1.35–2.50 odds bracket.",
    mechanics: "Backs dominant favorites and greens up immediately when odds compress into the 1.20–1.30 zone, locking in profit before any late-match death-overs chaos.",
    keyStats: {
      strategyType: "Front-Runner Lockdown",
      targetOdds: "1.35 – 2.50",
      minEdge: "4.0%",
      exitRule: "Low-Odds Compression Lock",
      lossProtection: "Strict 12-Ticket Limit",
    },
  },
  w05_e06_green: {
    code: "w05_e06_green",
    displayName: "Balanced Green Benchmark",
    shortTag: "50/50 Blend · 6% Edge",
    section: "green_up",
    badge: "Benchmark",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    description: "Upgraded version of the primary reference model (50/50 blend, 6% edge) retrofitted with automated profit lock-in.",
    mechanics: "Enters on fundamental 6% edges and equalizes returns across both sides as soon as the position is in positive territory.",
    keyStats: {
      strategyType: "Balanced Value Green-Up",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Auto Green-Up on Surplus",
      lossProtection: "Double-Negative Block",
    },
  },

  // ----------------- DIRECTIONAL KELLY MODELS -----------------
  w03_e03: {
    code: "w03_e03",
    displayName: "Market-Anchored Kelly (Conservative · 3% Edge)",
    shortTag: "30% Model / 70% Market",
    section: "directional",
    badge: "Directional",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    description: "Conservative directional model anchored heavily to exchange consensus (70% market weight, 30% model).",
    mechanics: "Trades on small 3% edges with fractional Kelly sizing. Holds directional positions without greening up, riding the directional edge to match conclusion.",
    keyStats: {
      strategyType: "Market-Weighted Directional",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Hold / Reduce on Negative Edge",
      lossProtection: "Double-Negative Block",
    },
  },
  w03_e06: {
    code: "w03_e06",
    displayName: "Market-Anchored Kelly (Selective · 6% Edge)",
    shortTag: "30% Model / 70% Market",
    section: "directional",
    badge: "Directional",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    description: "Market-anchored model (70% market) requiring a stricter 6% edge hurdle before committing capital.",
    mechanics: "Takes selective directional positions only when statistical predictions significantly diverge from exchange prices, holding directional upside.",
    keyStats: {
      strategyType: "Selective Market Directional",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Hold / Reduce on Negative Edge",
      lossProtection: "Double-Negative Block",
    },
  },
  w05_e03: {
    code: "w05_e03",
    displayName: "Balanced Kelly (Standard · 3% Edge)",
    shortTag: "50% Model / 50% Market",
    section: "directional",
    badge: "Directional",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    description: "Balanced baseline giving equal 50/50 weight to our machine learning model and exchange prices.",
    mechanics: "Enters whenever edge exceeds 3%. Accumulates positions on favorable odds and reduces on negative drift without early green-up.",
    keyStats: {
      strategyType: "Balanced Directional Kelly",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Hold / Kelly Drift Reduce",
      lossProtection: "Double-Negative Block",
    },
  },
  w05_e06: {
    code: "w05_e06",
    displayName: "Balanced Kelly (Primary Reference · 6% Edge)",
    shortTag: "50% Model / 50% Market",
    section: "directional",
    badge: "Primary Reference",
    badgeClass: "bg-indigo-500/20 text-indigo-300 border-indigo-500/40",
    description: "The primary reference model against which all placebo controls and CLV excess benchmarks are measured.",
    mechanics: "Enforces a strict 6% edge threshold with 50/50 model/market weighting. Holds directional risk proportionally to Kelly sizing.",
    keyStats: {
      strategyType: "Primary Institutional Reference",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Hold / Kelly Drift Reduce",
      lossProtection: "Double-Negative Block",
    },
  },
  w07_e03: {
    code: "w07_e03",
    displayName: "Model-Conviction Kelly (Standard · 3% Edge)",
    shortTag: "70% Model / 30% Market",
    section: "directional",
    badge: "High Conviction",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    description: "Aggressive model placing 70% weight on our predictive match model over exchange market odds.",
    mechanics: "Maintains large directional positions as long as our model identifies an edge. Does not lock in equal profit at 50-50, aiming for maximum directional payout.",
    keyStats: {
      strategyType: "High-Conviction Directional",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Hold / Aggressive Accumulation",
      lossProtection: "Double-Negative Block",
    },
  },
  w07_e06: {
    code: "w07_e06",
    displayName: "Model-Conviction Kelly (Maximum Conviction · 6% Edge)",
    shortTag: "70% Model / 30% Market",
    section: "directional",
    badge: "Max Conviction",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    description: "Highest-conviction directional model, combining 70% model weight with a high 6% edge bar.",
    mechanics: "Triggers only on major statistical edges, accumulating strong directional exposure to capture large payouts when our model is correct.",
    keyStats: {
      strategyType: "Maximum Conviction Directional",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Hold / Maximum Edge Capture",
      lossProtection: "Double-Negative Block",
    },
  },

  // ----------------- MARKET DYNAMICS MODELS -----------------
  mkt_dyn: {
    code: "mkt_dyn",
    displayName: "Market Dynamics (Standard · 3% Edge)",
    shortTag: "Pure Order Flow · 3% Edge",
    section: "dynamics",
    badge: "Order Flow",
    badgeClass: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    description: "Trained purely on exchange tick order flow, book depth, and price velocity without scorecard data.",
    mechanics: "Takes fast directional positions on 3% momentum signals (w=1.0), protected by double-negative guards and automated green-up.",
    keyStats: {
      strategyType: "Order Flow Directional",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Auto Green-Up on Favorable Move",
      lossProtection: "Double-Negative Block",
    },
  },
  mkt_dyn_e06: {
    code: "mkt_dyn_e06",
    displayName: "Market Dynamics (Selective · 6% Edge)",
    shortTag: "Pure Order Flow · 6% Edge",
    section: "dynamics",
    badge: "Order Flow",
    badgeClass: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    description: "High-threshold market dynamics model requiring a 6% order-flow momentum edge.",
    mechanics: "Triggers only on powerful exchange price trends and order book imbalances, executing with strict ticket budgets and downside loss guards.",
    keyStats: {
      strategyType: "High-Threshold Order Flow",
      targetOdds: "1.35 – 4.00",
      minEdge: "6.0%",
      exitRule: "Auto Green-Up on Favorable Move",
      lossProtection: "Double-Negative Block",
    },
  },

  // ----------------- CONTROLS & BENCHMARKS -----------------
  market_only: {
    code: "market_only",
    displayName: "Market-Only Control (Zero Intelligence)",
    shortTag: "100% Market Consensus",
    section: "controls",
    badge: "Control",
    badgeClass: "bg-zinc-700/50 text-zinc-300 border-zinc-600/50",
    description: "Baseline control using zero predictive model intelligence (w=0.0).",
    mechanics: "Trades solely on exchange over-rounds and market book imbalances to establish a zero-intelligence baseline benchmark.",
    keyStats: {
      strategyType: "Zero-Intelligence Baseline",
      targetOdds: "1.35 – 4.00",
      minEdge: "3.0%",
      exitRule: "Market Spread Scalp",
      lossProtection: "Cap Limit",
    },
  },
  tail: {
    code: "tail",
    displayName: "Chase Model (Tail Risk / Comebacks)",
    shortTag: "Chasing Innings Specialist",
    section: "controls",
    badge: "Tail Risk",
    badgeClass: "bg-purple-500/15 text-purple-300 border-purple-500/40",
    description: "Specialized logistic regression model trained on 2nd-innings run chases.",
    mechanics: "Activates only during run chases to hunt underpriced long-shot comebacks in high-volatility closing overs.",
    keyStats: {
      strategyType: "Chasing Tail Comeback",
      targetOdds: "Dynamic Long-Shot",
      minEdge: "Model Threshold",
      exitRule: "Hold to Match Close",
      lossProtection: "Chase Cap Room",
    },
  },
  placebo_favourite: {
    code: "placebo_favourite",
    displayName: "Placebo Favourite Benchmark",
    shortTag: "Blind Favourite Backing",
    section: "controls",
    badge: "Placebo",
    badgeClass: "bg-zinc-700/50 text-zinc-300 border-zinc-600/50",
    description: "Scientific control that blindly copies the market favourite at each decision timestamp.",
    mechanics: "Measures whether our strategies outperform a naive gambler who simply backs the favourite.",
    keyStats: {
      strategyType: "Naive Benchmark",
      targetOdds: "Market Favourite",
      minEdge: "None (Placebo)",
      exitRule: "Follows Reference",
      lossProtection: "Reference Cap",
    },
  },
  placebo_random: {
    code: "placebo_random",
    displayName: "Placebo Random Benchmark (Null Hypothesis)",
    shortTag: "Random Seeded Decisions",
    section: "controls",
    badge: "Null Placebo",
    badgeClass: "bg-zinc-700/50 text-zinc-300 border-zinc-600/50",
    description: "Rigorous statistical null control placing seeded-random bets with identical stake sizes.",
    mechanics: "Proves that strategy profits and positive CLV are genuine mathematical skill rather than statistical luck.",
    keyStats: {
      strategyType: "Statistical Null Hypothesis",
      targetOdds: "Any",
      minEdge: "None (Random)",
      exitRule: "Follows Reference",
      lossProtection: "Reference Cap",
    },
  },
};

export function getModelMeta(variantCode: string): ModelMeta {
  if (MODEL_REGISTRY[variantCode]) {
    return MODEL_REGISTRY[variantCode];
  }

  // Graceful fallback for any unknown or dynamically created variant
  const isBmodel = variantCode.startsWith("bmodel_");
  const isGreen = variantCode.includes("green");
  const isDyn = variantCode.startsWith("mkt_dyn");
  const isPlacebo = variantCode.startsWith("placebo_") || variantCode === "market_only";

  let section: ModelSectionId = "directional";
  if (isBmodel || isGreen) section = "green_up";
  else if (isDyn) section = "dynamics";
  else if (isPlacebo) section = "controls";

  return {
    code: variantCode,
    displayName: variantCode.replace(/_/g, " ").toUpperCase(),
    shortTag: "Custom Variant",
    section,
    description: `Variant ${variantCode} trading under institutional risk rules.`,
    mechanics: isGreen || isBmodel
      ? "Executes automated green-up to lock profit across both outcomes with zero worst-case risk."
      : "Trades directional exposure sized by fractional Kelly.",
    keyStats: {
      strategyType: "Trading Model",
      targetOdds: "Exchange Takeable",
      minEdge: "Configured",
      exitRule: isGreen || isBmodel ? "Auto Green-Up" : "Directional",
      lossProtection: "Active Guard",
    },
  };
}
