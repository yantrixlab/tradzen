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
  src?: 'engulf';                                  // an engulfing-candle zone: `base` = the engulfed candle, `dep` = the candle(s) that engulfed it, why = how it ended
  why?: 'used' | 'broken' | 'expired' | null;
  run?: number;
}

/** A swing high / low of the market structure; label = HH / LH (high higher / lower than the swing before) or HL / LL ('' = first one seen). */
export interface ChartSwing { id: string; t: number; kind: 'high' | 'low'; price: number; label: string }

/** An indicator: drawn over the price (`overlays`) or in its own pane under it (`panes`). */
export interface SeriesGroup {
  id: string; label: string; color: string; lines: SeriesLine[];
  levels?: number[];                      // dashed reference lines in a pane (e.g. RSI 30 / 70)
  range?: [number, number] | null;        // fixed value range of a pane (RSI 0-100); otherwise it fits the visible values
}

/** A strategy signal: arrow under (CALL, dir 1) or over (PUT, dir -1) the candle that opens at `t`. */
export interface ChartMarker { t: number; dir: 1 | -1; tip?: string }

/** Shaded columns through every pane from candle time t0 to t1 (a condition that is true, a signal candle ...). */
export interface ChartBand { t0: number; t1: number; color: string; opacity?: number; label?: string }

/** A numbered box round a run of candles (a step of a candle pattern). */
export interface ChartBox { t0: number; t1: number; color: string; label: string }

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
  boxes?: ChartBox[];
  zones?: ChartZone[];
  swings?: ChartSwing[];
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
  footer?: ReactNode;
  emptyText?: string;
}
