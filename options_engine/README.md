# PrimeFlow Options Engine

Backtests and signals for SPY options: a free data layer, the PrimeFlow Regime Gate, option
pricing calibrated to real Cboe quotes, a risk manager, and a search that tests tens of thousands
of SPY call and put rules against 2005–2026 history. It's plain Python on your own computer:
nothing to install, no paid data, no GitHub Actions.

> Hypothetical, model-priced results. Trading options involves substantial risk of loss and
> is not suitable for every investor. Past performance does not indicate future results.
> Educational software, not investment advice.

## The 99% question, for a $500 account

**The test.** `python -m pfo search --account 500` ran 24,360 SPY call and put rules over
2005-01-03 to 2026-09-23. Every trade was replayed at today's SPY price (about $768), with fees
and slippage.

| Family | Rules | 99%+ win rate | Fit $500 and 99%+ and profitable |
|---|---|---|---|
| Buy one call | 4,500 | 0 | 0 |
| Buy one put | 4,495 | 0 | 0 |
| Sell one call or put (uncovered) | 5,666 | 172 | 0 (needs thousands in margin) |
| Sell a call or put spread | 7,352 | 38 | 32 |

- **Buying calls or puts never reached 99%.** The best was 97%, it needed about $2,700 per
  contract, and each loss cost about 19 wins.
- **Only credit spreads qualified.** Every qualifying rule sells a far out-of-the-money option
  and buys one further out to cap the loss. Of the 32, 18 stayed at 99%+ and profitable when
  entered on *every* day their signal fired.
- **Only one was profitable in all seven stress tests** (fees, fills, volatility level, skew):
  the **Ceiling Call Spread**.

**Ceiling Call Spread.** Sell the ~3-delta SPY call about 45 days out (roughly 8–10% above
the market), buy the call $5 higher, and hold to expiry. It loses only if SPY closes above the
sold strike at expiry.

| | Result |
|---|---|
| Backtest trades (one at a time) | 181, all winners |
| Every possible entry day | 99.40% of 5,367 won; worst stress scenario 98.96% |
| Average win / maximum loss | about $4 / about $495 per spread |
| Total, one contract, 2005–2026 | +$812 (Schwab fees); +$1,047 with zero commission |
| $500 account, 5 years, started on every week in history | median $684; 5.8% hit a loss and could no longer afford the trade |
| Checked against real Cboe quotes | model credit $0.06–$0.08 vs real $0.07–$0.08, so the backtest is slightly conservative |

What that means in plain terms:

- **A 99% win rate and fast growth don't come together at $500.** Each spread risks about
  $495 to make about $4–$7, and one loss erases roughly 100 wins.
- **99% is the observed rate, not a proven one.** 181 independent trades aren't enough to prove
  a true 99%. That takes about 299 wins in a row with no losses. The evidence supports roughly
  96.6–98.4% with 95% confidence.
- **You need a margin account with spread approval.** Both Schwab and Robinhood require this for
  spreads. Whether they'll approve spreads for a $500 account is their call.
- **The losses come from sharp rallies.** Entering on every possible day, losing entries fell
  in 2009, 2013, 2017, 2019, 2023, 2024 and 2026. The "stuck" figure above is those losses
  landing early in a $500 account's life.

**Earlier result, now withdrawn.** An earlier run showed a 7-day put spread winning 99.5% and
making +$4,080. That run used a pricing model that overpriced far out-of-the-money spreads
3–10x. Checked against real Cboe quotes, the model was fixed. With the fix, that spread wins 98%
and loses money.

## Today's trade

`python -m pfo today` (or double-click `todays_signal`) prints the regime and, for the Ceiling
Call and Dip Floor Put spreads, the exact order from **live Cboe quotes** (delayed ~15 min):

```
Ceiling Call Spread
  SIGNAL from live Cboe quotes (delayed ~15 min, as of 2026-09-23 03:54:59, SPY 773.38)
  Expiry 2026-10-30 (37 days)
    SELL 835 call   bid 0.33 / ask 0.34   delta +0.031
    BUY  840 call   bid 0.26 / ask 0.27
  Credit: mid $0.07, natural $0.06 ($7 per spread before fees)
  Place ONE spread limit order for a $0.07 credit. Do not accept less than $0.05.
  Then do nothing until expiry. It expires worthless (full win) unless SPY closes ABOVE 835 on 2026-10-30.
  Max loss per spread: $495.
  ACCOUNT CHECK: one spread ties up $495 = 99% of a $500 account. A loss costs about that much.
```

Paper-trade it first, for example in thinkorswim paperMoney, and only hold one spread at a time.

## How it works

```
 SPY + VIX9D/VIX/VIX3M ──► Regime Gate ──► Strategy ──► Risk Manager ──► Backtest / Search / Today
   (free: Yahoo, Cboe,       daily bias +    setup +      size by max
    FRED, Stooq, Nasdaq)     volatility      contracts    loss, breakers
```

**Regime Gate** (PrimeFlow's rule: the regime decides which setups are legal):
- *Daily bias.* BULL when SPY is above its 200-day average and the 8 EMA is above the 50 EMA.
  BEAR is the mirror image. NEUTRAL means the signals disagree.
- *Volatility.* Calm under VIX 20, elevated from 20 to 30, stressed above 30. It also flags VIX
  spikes (VIX 20% above its 10-day average) and an inverted VIX term structure.

**Pricing.** There's no free history of SPY option quotes, so options are priced with
Black-Scholes on a volatility surface built from Cboe's indexes:

- *Volatility level.* VIX9D, VIX and VIX3M are interpolated to each option's own expiry.
  At-the-money volatility is 0.80x that value.
- *Skew.* The skew curve was measured per expiry range from a real Cboe SPY chain.
- *Accuracy on that chain.* For 2–16 delta puts, the model's strikes land within a median of
  0.5 points of the real ones, and $5 put spreads price at 1.04–1.08x the real mid.
- *Early years.* Before VIX9D (2011) and VIX3M (2009) existed, they're estimated from VIX.

**Strategies.** All four are spreads, so the maximum loss is known before entry:
- *Ceiling Call Spread* and *Dip Floor Put Spread:* the two finalists from the $500 search
  (see above). The Dip Floor Put sells the ~5-delta put 14 days out after a sharp dip in a
  bullish bias. It won 114 of 114 trades. But under the "skew 20% flatter"
  stress test, entered on every possible day, it won only 96.5% and lost money.
- *Trend Put Credit Spread* and *Pullback Call Debit Spread:* the original textbook strategies
  in the `backtest` study. Neither passes its go/no-go gate on 2005–2026.

**Risk Manager** (for the `backtest` study):
- Max loss per trade is 5% of the account. A single contract is allowed only if it risks 60%
  or less.
- One position at a time.
- *Drawdown halt:* after a 25% drop from the peak, no new trades for 20 trading days.
- *Losing streak:* after 3 losses in a row, no new trades for 10 trading days.
- *Kill switch:* below 50% of starting equity, no new trades ever again.

**Costs.** Commission is $0.65 per contract per leg (Schwab), plus $0.01 per share of slippage
on every leg, both in and out.

## Run it

1. **Install Python 3.9 or newer** from [python.org](https://www.python.org/downloads/).
   - On Windows, tick **"Add Python to PATH"** in the installer.
   - On a Mac, open your Python folder in Applications after installing and double-click
     **Install Certificates.command**. Without it, downloads fail with an SSL error.
2. **Get this folder** onto your computer: `git clone` the repo, or download it as a ZIP from
   GitHub and unzip it.
3. **Double-click** `todays_signal.bat` / `todays_signal.command` for today's trade, or
   `run_backtest.bat` / `run_backtest.command` for the study.
   - On a Mac, the first time, right-click the file, choose Open, then confirm.
   - If it won't run, open Terminal, `cd` into this folder and run `chmod +x *.command`.

Or from a terminal, inside this folder:

```
python -m pfo fetch                        # download SPY / VIX / VIX9D / VIX3M history
python -m pfo today --equity 500           # today's regime and exact trades, from live Cboe quotes
python -m pfo search --account 500         # the 24,360-rule win-rate search (10-20 minutes)
python -m pfo backtest                     # the original study, 2005 to today
python -m pfo backtest --sample            # offline: the bundled 2014-2018 data
python -m unittest discover -s tests       # self-tests
```

On Mac and Linux, type `python3` instead of `python`.

### If the download fails

Free sources change without notice. The engine tries several for each series. If all of them
fail, save the files by hand into `data/`:

- `data/SPY.csv`: SPY daily history with a `Date` column and a `Close` column. A Yahoo Finance
  export works.
- `data/VIX.csv`: [Cboe VIX history](https://www.cboe.com/tradable_products/vix/vix_historical_data/)
  or FRED series VIXCLS.
- `data/VIX9D.csv` and `data/VIX3M.csv` (optional): Cboe history.

## Reading the reports

**Search report** (`reports/search_YYYYMMDD.html`):
- the win-rate ceiling for each family
- every rule that is 99%+, profitable and affordable, re-tested from every possible entry day
- a 95% lower bound on each rule's true win rate
- a before/after split of the data
- for the top finalists, seven stress tests, plus a small account started on every week in
  history and traded for 1, 3 and 5 years

**Backtest report** (`reports/backtest_YYYYMMDD.html`): each strategy gets a go/no-go gate fixed
in advance. The gate requires:
- at least 30 trades
- a profit factor of 1.3 or more
- a max drawdown under 25%
- a profit in both halves of the period
- a profit with double slippage
- a profit with next-day fills

The report also has an equity chart against SPY, account sizes, stress tests, a Monte Carlo
luck check, and every trade.

## What the backtest can and cannot tell you

- **Prices.** Option prices are modelled, not historical quotes. The model is calibrated to
  one day of real Cboe quotes. The relationship between skew and volatility shifts over time,
  which is why every finalist is stress-tested with volatility ±6% and skew ±20%.
- **History.** The data includes 2008, 2011, 2015, 2018, 2020, 2022 and 2025. A rule that never
  lost here can still meet a crash it hasn't seen.
- **What isn't modelled:**
  - intraday stops (everything is checked at the close; take-profit orders may fill intraday)
  - early assignment
  - the exact listed-expiry calendar

## Roadmap

1. **Done:**
   - the data layer and Regime Gate
   - calibrated pricing and live Cboe quotes
   - the risk manager, backtester and 24,360-rule search
   - reports, the daily signal and tests
2. **Forward test.** Log the Ceiling Call signal and its real Cboe credit every day. That
   builds a record of real prices, not model prices.
3. **Paper trading.** Place the signals in paperMoney. Compare fills and outcomes with the
   backtest.
4. **Broker connection: Schwab only.** Schwab's Trader API is official and free with an
   account. Approval of a developer app takes a few days, and it needs a weekly re-login.
   Orders will be limit-only, run in paper mode first, and pass the same risk checks.
   - Robinhood has no official API for options orders. The unofficial libraries break its
     terms and can get an account locked, so it isn't supported.

## Layout

```
pfo/
  data.py           downloads, CSV cache, loading
  indicators.py     SMA / EMA / RSI
  pricing.py        Black-Scholes, calibrated vol surface, strike and expiry selection
  regime.py         the PrimeFlow Regime Gate and the VIX9D/VIX/VIX3M term structure
  strategies.py     the four strategies
  chain.py          live SPY quotes from Cboe's delayed chain
  risk.py           position sizing and circuit breakers
  backtest.py       the daily event loop
  metrics.py        statistics and the Monte Carlo luck check
  study.py          the backtest study and its go/no-go gate
  search.py         the win-rate search, every-day retest and account paths
  search_report.py  search findings, stress tests and the HTML report
  report.py         backtest console table and HTML report
  today.py          today's signal
  config.py         every tunable number
data/sample/        S&P 500 / 10 (SPY proxy) 2013-2018 and VIX 2014-2018, for offline runs and tests.
                    Source: the datasets bundled with the `arch` Python package.
```
