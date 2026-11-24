import type { ReactNode } from 'react';

/** One closed (or forming) candle. `syn` = a flat stand-in the backend inserted to bridge a tiny data gap (drawn faded). */
export interface ChartCandle { t: number; o: number; h: number; l: number; c: number; syn?: boolean }

/** One line of an indicator. values[i] belongs to candles[i]; null = nothing to draw there. */
export interface SeriesLine { values: (number | null)[]; style?: 'line' | 'dash' | 'dots' | 'bars'; color?: string }

/**
 * A supply / demand zone drawn as a rectangle from t0 (its first base candle) to t1 (where it was broken / expired, or the newest candle when `alive`),
 * with the candles that formed it: the BASE (the tight candles) and the DEPARTURE (the strong move away), and each visit price paid to it (`touches`).
 */
export interface ChartZone {
  id: string; kind: 'demand' | 'supply'; lo: number; hi: number; t0: number; t1: number; alive: boolean; broken: boolean; flip: boolean;
  move: number; tests: number; base: [number, number] | null; dep: [number, number] | null; touches: number[];
  // where the zone comes from. engulf / swing_engulf: `base` = the engulfed candle(s) (a whole swing of one colour for swing_engulf), `dep` = the candle(s) that engulfed it;
  // swing: the candle of a HH / LH / HL / LL swing that price then left fast (tag = which); base: a strong base-and-departure zone
  src?: 'engulf' | 'swing' | 'swing_engulf' | 'base';
  why?: 'used' | 'broken' | 'expired' | null;
  run?: number;
  tag?: string;                                    // the zone's reason in a word: HL, LH ..., SWING ×3, ENGULF, BASE (several joined with " · " when more than one confirms it)
  score?: number;                                  // 0-100: how strong it looked when it formed (size of the move away, with the trend, origin of a break of structure ...)
  origin_of?: 'BOS' | 'CHoCH' | null;              // the zone the market left in a break of structure
}

/** A swing high / low of the market structure; label = HH / LH (high higher / lower than the swing before) or HL / LL ('' = first one seen). `major` = also the extreme of 3 candles on each side (absent = not told). */
export interface ChartSwing { id: string; t: number; kind: 'high' | 'low'; price: number; label: string; major?: boolean }

/** A break of structure: a candle CLOSED beyond `price` (the level of the swing at t0) at t1. BOS = the trend continues, CHoCH = it changes; `strong` = confirmed by a real body / a clear close. */
export interface ChartBreak { t0: number; t1: number; price: number; type: 'BOS' | 'CHoCH'; dir: 1 | -1; strong: boolean }

/** Who is in control, read from the newest candles (server: marketread.summary). */
export interface FlowReading {
  t: number; score: number; buyers: number; sellers: number; tier: string; headline: string; side: 1 | 0 | -1; trend: 1 | 0 | -1; pullback: boolean; stretched: boolean; since: number;
  protected: number | null;
  last_break: { type: 'BOS' | 'CHoCH'; dir: 1 | -1; strong: boolean; t: number; ago: number; level: number } | null;
  components: { key: string; name: string; value: number; weight: number }[];
  reasons: string[];
  accuracy: { sample: string; date: string; next_candle: number; after_5: number; after_10: number; strong_break_follow: number; weak_break_follow: number; stretched_follow: number } | null;
}

/** An indicator: drawn over the price (`overlays`) or in its own pane under it (`panes`). */
export interface SeriesGroup {
  id: string; label: string; color: string; lines: SeriesLine[];
  levels?: number[];                      // dashed reference lines in a pane (e.g. RSI 30 / 70)
  range?: [number, number] | null;        // fixed value range of a pane (RSI 0-100); otherwise it fits the visible values
}

/** A strategy signal: arrow under (CALL, dir 1) or over (PUT, dir -1) the candle that opens at `t`. */
export interface ChartMarker { t: number; dir: 1 | -1; tip?: string; label?: string; color?: string }

/** A price area (lo..hi) from candle time t0 to t1, drawn as a labelled box: the area a live-session signal is taken from. `open` = it runs on to the right edge (the trade has not ended yet). */
export interface ChartArea { t0: number; t1: number; lo: number; hi: number; color: string; label?: string; open?: boolean; dashed?: boolean }

/** Shaded columns through every pane from candle time t0 to t1 (a condition that is true, a signal candle ...). */
export interface ChartBand { t0: number; t1: number; color: string; opacity?: number; label?: string }

/** A numbered box round a run of candles (a step of a candle pattern). */
export interface ChartBox { t0: number; t1: number; color: string; label: string }

/** A candlestick or chart pattern found on the candles (lib/chartPatterns.ts): a box round the candles it covers, with its short name, and (chart patterns) the lines that draw its shape. */
export interface ChartHighlight {
  key: string; id: string; name: string; label: string; bias: 'bull' | 'bear' | 'neutral'; color: string; chart: boolean; t0: number; t1: number; lo: number; hi: number;
  lines?: { t0: number; p0: number; t1: number; p1: number; dash?: boolean }[];
}

export interface MtgStep { k: number; t: number; result: string; stake: number }
export interface MtgLadder { outcome: string; steps: MtgStep[]; pl: number; stake_max: number }

/** A backtested trade: signalled when candle `t` closed, entered at the next open, settled at its close; `mtg` = the re-entry ladder after a loss. */
export interface BtTrade {
  t: number; dir: number; tier: string; strength: number; entry: number; exit: number; result: string; profit: number; mtg?: MtgLadder; mtg_skip?: boolean;
}

export interface MtgSummary {
  steps: number; mult: number; signals: number; taken: number; skipped: number; first_wins: number; first_losses: number; first_draws: number;
  rescued: Record<string, number>; lost_ladders: number; open: number; ladder_draws: number; ladder_pl: number; flat_pl: number; rescue_rate: number | null;
  worst_ladder: number; stake_max: number; max_drawdown: number; longest_lost_run: number; units_at_risk: number;
}

export interface PriceChartProps {
  candles: ChartCandle[];
  forming?: ChartCandle | null;           // the candle still being built (drawn translucent, with the live price line)
  lastPrice?: number | null;
  tf?: number;                            // seconds per candle (inferred when omitted)
  overlays?: SeriesGroup[];
  panes?: SeriesGroup[];
  markers?: ChartMarker[];
  bands?: ChartBand[];
  areas?: ChartArea[];
  boxes?: ChartBox[];
  highlights?: ChartHighlight[];          // the patterns picked in the Patterns dialog
  zones?: ChartZone[];
  swings?: ChartSwing[];
  breaks?: ChartBreak[];
  trades?: BtTrade[];
  payout?: number | null;
  splitT?: number | null;                 // start of a backtest's validation period
  mtg?: MtgSummary | null;
  height?: number;                        // height of the price pane in px
  initialCount?: number;                  // candles shown at first
  compact?: boolean;                      // small embedded use: shorter toolbar, no overview strip
  resetKey?: string;                      // change it (pair, timeframe ...) to put the view back to the newest candles
  toolbarLeft?: ReactNode;                // page controls placed in the toolbar (e.g. the strategy picker)
  toolbarRight?: ReactNode;
  tooltipExtra?: (i: number) => ReactNode;
  chipsExtra?: (i: number) => { text: string; color: string }[];     // short chips shown next to the readout while the pointer is on a candle
  footer?: ReactNode;
  emptyText?: string;
}
