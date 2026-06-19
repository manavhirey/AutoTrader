# Opening Range Breakout (ORB) — Formal Rule Spec

A mechanical multi-timeframe opening-range strategy. This spec is written to be
implementation-agnostic but maps cleanly onto a state machine driving an order
engine, with a human approval gate before any order is sent.

> **Note on subjectivity.** The source strategy uses qualitative terms ("strong
> close", "displacement", "retest hold"). Every one of those is reified below
> into a tunable numeric parameter so the logic is deterministic. Defaults are
> reasonable starting points, **not** validated values — backtest before trusting.

---

## 1. Configuration

```text
CONFIG {
  # --- session / timing ---
  timezone:                 "America/New_York"
  session_open:             "09:30"        # NY open
  range_timeframe_min:      15             # opening-range candle size
  confirm_timeframe_min:    5              # breakout-confirmation candle size
  entry_timeframe_min:      1              # entry-trigger candle size
  trading_window_min:       120            # only trade within N min of open
  flatten_at:              "15:55"         # force-close before EOD (optional)

  # --- risk / sizing ---
  risk_reward_ratio:        2.0            # fixed 1:R target
  risk_per_trade_pct:       0.5            # % of account risked per trade
  max_trades_per_day:       1              # video implies ~1 clean setup/day
  allow_long:               true
  allow_short:              true

  # --- entry-model toggles ---
  enable_breakout:          true
  enable_retest:            true           # presenter's preferred model
  enable_reversal:          false          # off by default; on for ranging instruments

  # --- quantified "strong close" (replaces "strong candle close") ---
  strong_close_body_ratio:  0.60           # |close-open| / (high-low) >= this
  strong_close_location:    0.70           # close in top/bottom 30% of candle range

  # --- quantified "displacement / bullish gap" ---
  # Choose ONE displacement model:
  displacement_model:       "FVG"          # "FVG" | "IMPULSE" | "TRUE_GAP"
  fvg_min_size_ticks:       2              # 3-candle imbalance min size (FVG)
  impulse_atr_mult:         1.5            # breakout candle range >= ATR * mult (IMPULSE)

  # --- quantified "retest" ---
  retest_tolerance_atr:     0.25           # price must come within ATR*x of level
  retest_max_wait_candles:  15             # abandon retest if not filled in N candles
  retest_confirm_body_ratio:0.50           # confirmation candle min body ratio

  # --- day-type filter ---
  range_day_sweep_both:     true           # if both range sides taken -> range day
  range_day_disables:       ["breakout","retest"]   # what to suppress on a range day
  range_day_enables:        ["reversal"]

  # --- misc ---
  atr_period:               14
  require_higher_tf_bias:   false          # optional confluence (see §7)
}
```

---

## 2. Data structures

```text
Candle {
  ts_open, ts_close       # timestamps
  open, high, low, close
  timeframe_min
}

OpeningRange {
  high, low
  established_at          # ts of the close of the first range candle
  width = high - low
}

Direction = LONG | SHORT

Setup {
  direction
  model                   # BREAKOUT | RETEST | REVERSAL
  entry, stop, target
  rr
  reason[]                # human-readable confluence list for the approval gate
}
```

---

## 3. State machine (per instrument, per day)

```text
States:
  IDLE                  -> before session open
  BUILDING_RANGE        -> accumulating the first range candle
  RANGE_SET             -> opening range fixed; waiting for confirmation
  WAIT_CONFIRMATION     -> watching confirm-TF candles for a close beyond range
  WAIT_ENTRY            -> confirmed direction; hunting entry on entry-TF
  IN_TRADE              -> position open (post-approval)
  DONE                  -> trade closed OR window expired OR max_trades hit
```

Transition overview:

```text
IDLE
  on session_open                         -> BUILDING_RANGE
BUILDING_RANGE
  on first range-TF candle closes         -> set OpeningRange; RANGE_SET
RANGE_SET
  immediately                             -> WAIT_CONFIRMATION
WAIT_CONFIRMATION
  on confirm-TF close beyond range        -> set direction; WAIT_ENTRY
  on both sides swept (range day)         -> apply day-type filter
  on window expired                       -> DONE
WAIT_ENTRY
  on valid entry trigger                  -> build Setup -> approval gate -> IN_TRADE
  on window expired                       -> DONE
IN_TRADE
  on stop/target/flatten                  -> record result -> DONE (or re-arm if trades remain)
```

---

## 4. Main loop (pseudocode)

```text
function on_new_candle(candle, ctx):       # ctx holds state, config, range, etc.
  if now() < session_open():               return    # IDLE
  if past_window(ctx):                      ctx.state = DONE; return

  switch ctx.state:

    case IDLE:
      ctx.state = BUILDING_RANGE

    case BUILDING_RANGE:
      # first range-TF candle of the session
      if candle.timeframe == range_timeframe and is_first_session_candle(candle):
        ctx.range = OpeningRange(high=candle.high, low=candle.low,
                                 established_at=candle.ts_close)
        ctx.state = RANGE_SET

    case RANGE_SET:
      ctx.state = WAIT_CONFIRMATION
      track_sweeps(ctx, candle)            # begin day-type tracking

    case WAIT_CONFIRMATION:
      track_sweeps(ctx, candle)
      if is_range_day(ctx):                # both sides taken, no trend
        apply_day_type_filter(ctx)         # toggles models per CONFIG
      if candle.timeframe == confirm_timeframe:
        dir = confirmed_breakout(candle, ctx.range)
        if dir != NONE and direction_allowed(dir):
          ctx.direction = dir
          ctx.break_level = (dir==LONG ? ctx.range.high : ctx.range.low)
          ctx.state = WAIT_ENTRY

    case WAIT_ENTRY:
      if candle.timeframe == entry_timeframe:
        setup = try_build_entry(candle, ctx)
        if setup != NULL:
          submit_for_approval(setup)       # HUMAN GATE — see §8
          # on approval -> place orders -> ctx.state = IN_TRADE

    case IN_TRADE:
      manage_position(candle, ctx)         # stop/target handled by broker OCO
```

---

## 5. Confirmation logic (Step 2)

```text
function confirmed_breakout(confirm_candle, range) -> Direction|NONE:
  if confirm_candle.close > range.high and is_strong_close(confirm_candle, LONG):
      return LONG
  if confirm_candle.close < range.low  and is_strong_close(confirm_candle, SHORT):
      return SHORT
  return NONE

function is_strong_close(c, dir) -> bool:
  body  = abs(c.close - c.open)
  rng   = max(c.high - c.low, EPS)
  body_ok = (body / rng) >= strong_close_body_ratio
  if dir == LONG:
      loc_ok = (c.close - c.low) / rng >= strong_close_location
  else:
      loc_ok = (c.high - c.close) / rng >= strong_close_location
  return body_ok and loc_ok
```

---

## 6. Entry models (Step 3)

```text
function try_build_entry(candle, ctx) -> Setup|NULL:
  if enable_breakout and ctx.breakout_armed:
      s = entry_breakout(candle, ctx);   if s: return s
  if enable_retest:
      s = entry_retest(candle, ctx);     if s: return s
  if enable_reversal and ctx.range_day:
      s = entry_reversal(candle, ctx);   if s: return s
  return NULL
```

### 6a. Breakout

```text
function entry_breakout(candle, ctx) -> Setup|NULL:
  disp = detect_displacement(ctx.entry_window)   # FVG | IMPULSE | TRUE_GAP
  if disp == NULL: return NULL                   # no breakout entry without displacement

  if ctx.direction == LONG:
      entry = candle.close
      stop  = disp.lower_candle.low - buffer      # below the gap-forming candle
  else:
      entry = candle.close
      stop  = disp.upper_candle.high + buffer

  target = project_target(entry, stop, ctx.direction)
  return Setup(ctx.direction, BREAKOUT, entry, stop, target,
               reason=["confirmed close beyond range", "displacement: "+disp.type])
```

### 6b. Retest (preferred)

```text
function entry_retest(candle, ctx) -> Setup|NULL:
  level = ctx.break_level
  if not within(candle, level, retest_tolerance_atr * atr(ctx)): return NULL
  if ctx.retest_wait > retest_max_wait_candles: ctx.retest_dead = true; return NULL

  # require a hold: confirmation candle rejecting back in breakout direction
  if not is_confirmation_candle(candle, ctx.direction, retest_confirm_body_ratio):
      ctx.retest_wait += 1; return NULL

  if ctx.direction == LONG:
      entry = candle.close
      stop  = recent_swing_low(ctx) - buffer       # break-of-structure low
  else:
      entry = candle.close
      stop  = recent_swing_high(ctx) + buffer

  target = project_target(entry, stop, ctx.direction)
  return Setup(ctx.direction, RETEST, entry, stop, target,
               reason=["break + retest hold of "+level, "no gap -> retest model"])
```

### 6c. Reversal (range days / mean reversion)

```text
function entry_reversal(candle, ctx) -> Setup|NULL:
  # Triggered after a failed breakout: price re-enters the range and breaks
  # a lower-high (for longs) / higher-low (for shorts) with displacement.
  if not failed_breakout(ctx): return NULL
  if not broke_structure_back(candle, ctx): return NULL

  rev_dir = opposite(ctx.failed_direction)
  if rev_dir == LONG:
      entry = candle.close
      stop  = swing_low(ctx) - buffer
  else:
      entry = candle.close
      stop  = swing_high(ctx) + buffer

  target = high_of_day(ctx) if rev_dir==LONG else low_of_day(ctx)  # or RR-projected
  return Setup(rev_dir, REVERSAL, entry, stop, target,
               reason=["range day", "failed ORB", "structure break w/ displacement"])
```

---

## 7. Stops, targets, displacement, day-type

```text
function project_target(entry, stop, dir):
  risk = abs(entry - stop)
  return dir==LONG ? entry + risk*risk_reward_ratio
                   : entry - risk*risk_reward_ratio

function detect_displacement(window):
  switch displacement_model:
    case "FVG":      return find_fair_value_gap(window, fvg_min_size_ticks)
    case "IMPULSE":  return find_impulse_candle(window, impulse_atr_mult)
    case "TRUE_GAP": return find_price_gap(window)

# Day-type detection: both range extremes traded through without follow-through
function is_range_day(ctx) -> bool:
  return range_day_sweep_both
         and ctx.swept_high and ctx.swept_low
         and not ctx.clean_trend          # optional higher-TF check

function apply_day_type_filter(ctx):
  for m in range_day_disables: disable(m)
  for m in range_day_enables:  enable(m)

# Optional confluence (require_higher_tf_bias)
function direction_allowed(dir) -> bool:
  if not require_higher_tf_bias: return (dir==LONG ? allow_long : allow_short)
  return aligned_with_higher_tf_draw(dir)    # e.g. prev-day levels, HTF trend
```

---

## 8. Human approval gate

```text
function submit_for_approval(setup):
  payload = {
    instrument, setup.direction, setup.model,
    setup.entry, setup.stop, setup.target, setup.rr,
    risk_amount = account * risk_per_trade_pct,
    reason = setup.reason,                 # confluence list, human-readable
    snapshot = chart_context()             # range, levels, candle data
  }
  notify_approver(payload)
  # On APPROVE -> place bracket/OCO order (entry + stop + limit target)
  # On REJECT  -> log + return to WAIT_ENTRY (respect max_trades_per_day)
```

---

## 9. Invariants & guardrails

- Never act before `session_open` or after `trading_window_min`.
- At most `max_trades_per_day`; decrement on every filled (approved) setup.
- One opening range per instrument per day; never recompute after `RANGE_SET`.
- All orders go through the approval gate — no auto-fire.
- Confirmation requires a **close**, never an intrabar touch/wick.
- Stop must always sit on the structurally correct side; reject any setup where
  `rr < risk_reward_ratio` or `entry`/`stop` are inverted.

---

## 10. Parameters to calibrate first (highest leverage)

1. `strong_close_body_ratio` / `strong_close_location` — gate quality vs. frequency.
2. `displacement_model` and its threshold — defines what counts as a tradable break.
3. `retest_tolerance_atr` / `retest_confirm_body_ratio` — the preferred model's core.
4. `range_day_sweep_both` logic — biggest driver of avoiding chop.
5. `range_timeframe_min` — 15 is the documented default; 5/1 change everything downstream.
