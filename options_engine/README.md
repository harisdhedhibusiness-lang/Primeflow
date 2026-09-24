# PrimeFlow Options Engine

Trades SPY option spreads with a fixed maximum loss, behind the PrimeFlow Regime Gate, and
sizes every trade from that maximum loss. It runs on your own computer with plain Python.
There's nothing to install, no paid data and no GitHub Actions.

> Hypothetical, model-priced results. Trading options involves substantial risk of loss and
> is not suitable for every investor. Past performance does not indicate future results.
> Educational software, not investment advice.

## Read this first

**No options system guarantees a profit.** The ones that look closest to guaranteed are the
ones that wipe out small accounts. Selling far out-of-the-money options wins 80–95% of
trades, then one bad week takes back a year of gains. This engine is built to find that out
*before* real money does.

The first backtest ran on real S&P 500 and VIX history, 2014–2018, with fees and slippage:

| | Trend Put Credit Spread | Pullback Call Debit Spread |
|---|---|---|
| $5,000 account | −6.0%, 75 trades, 63% winners, profit factor 0.62 | −14.3%, 41 trades, 59% winners, profit factor 0.66 |
| $100, safeguards on | 0 trades: every trade would risk over 60% of the account | 0 trades, same reason |
| $100, safeguards off | 1 trade, −17%, then too small to trade again | −65%, worst drop 70% |
| Go/no-go gate | **Fail** | **Fail** |

SPY itself rose 37% over the same window. Both textbook strategies won most of their trades
and still lost money, because the average loss was 2–3x the average win and costs took the
rest. That result is the point of building the engine: it said "no" on paper, for free.

**What $100 can and can't do.** SPY spreads at a sensible width risk several hundred dollars
per contract. The only spread $100 can hold is $1 wide. At that width, fees and slippage eat
a third to a half of each credit, and one max loss takes about 90% of the account. At Schwab,
spreads also require a margin account, and margin accounts have a $2,000 legal minimum. With a cash account
you can only buy options outright. Treat $100 as money for testing that orders work, not as
money to grow.

## How it works

```
 SPY + VIX daily history ──► Regime Gate ──► Strategy ──► Risk Manager ──► Backtest / Today's signal
   (free: Yahoo, Cboe,       daily bias +     setup +      size by max
    FRED, Stooq, Nasdaq)     volatility       contracts    loss, breakers
```

**Regime Gate** (PrimeFlow's rule: the regime decides which setups are legal):
- *Daily bias.* BULL when SPY is above its 200-day average and the 8 EMA is above the 50 EMA.
  BEAR is the mirror image. NEUTRAL means the signals disagree.
- *Volatility.* Calm under VIX 20, elevated from 20 to 30, stressed above 30. It also flags VIX
  spikes (VIX 20% above its 10-day average) and an inverted VIX term structure.

**Strategies.** Every strategy is a spread, so its maximum loss is known before entry:
- *Trend Put Credit Spread.* Sells a ~20-delta put spread about 45 days out, with a width
  about 1% of SPY's price. Needs a BULL bias and no volatility shock. It takes profit at 50%
  of the credit, stops out at a loss of 2x the credit, and closes at 21 days to expiry or when
  the bias turns BEAR.
- *Pullback Call Debit Spread.* Buys a 60/30-delta call spread about 14 days out when the
  bias is BULL and RSI(2) is under 10. It exits when SPY closes above its 5-day average, at 75%
  of max profit, at a 50% loss, or after 7 trading days.

**Risk Manager.** Every trade passes through it:
- Max loss per trade is 5% of the account. A single contract is allowed only if it risks 60%
  of the account or less.
- One position at a time. Total open risk is capped at 60%, and a trade never needs more
  cash than the account has.
- *Drawdown halt.* After a 25% drop from the equity peak, no new trades for 20 trading days.
- *Losing streak.* After 3 losses in a row, no new trades for 10 trading days.
- *Kill switch.* Below 50% of starting equity, no new trades ever again.

**Costs.** Commission is $0.65 per contract per leg (Schwab), plus $0.01 per share of
slippage on every leg, both in and out.

## Run it

1. **Install Python 3.9 or newer** from [python.org](https://www.python.org/downloads/).
   - On Windows, tick **"Add Python to PATH"** in the installer.
   - On a Mac, open your Python folder in Applications after installing and double-click
     **Install Certificates.command**. Without it, downloads fail with an SSL error.
2. **Get this folder** onto your computer: `git clone` the repo, or download it as a ZIP from
   GitHub and unzip it.
3. **Double-click** `run_backtest.bat` (Windows) or `run_backtest.command` (Mac). It downloads
   SPY and VIX history back to 2003, runs the full study from 2005 to today, and opens the report.
   - On a Mac, the first time, right-click the file, choose Open, then confirm.
   - If it won't run, open Terminal, `cd` into this folder and run `chmod +x *.command`.

Or from a terminal, inside this folder:

```
python -m pfo fetch                  # download SPY / VIX / VIX3M history
python -m pfo backtest               # full study, 2005 to today; writes reports/backtest_YYYYMMDD.html
python -m pfo backtest --sample      # offline: the bundled 2014-2018 data
python -m pfo today --equity 2000    # today's regime and the exact trade to paper-trade
python -m unittest discover -s tests # self-tests
```

On Mac and Linux, type `python3` instead of `python`.

### If the download fails

Free sources change without notice. The engine tries several for each series. If all of them
fail, save the files by hand into `data/`:

- `data/SPY.csv`: SPY daily history from any site. It needs a `Date` column and a `Close`
  column. A Yahoo Finance "Download" export works.
- `data/VIX.csv`: [Cboe VIX history](https://www.cboe.com/tradable_products/vix/vix_historical_data/)
  or FRED series VIXCLS.
- `data/VIX3M.csv` (optional): Cboe VIX3M, or FRED series VXVCLS.

Then run `python -m pfo backtest`.

## Reading the report

Each strategy gets:

- **The go/no-go gate.** It was fixed before any results were seen. A strategy must clear
  every line:
  - at least 30 trades
  - a profit factor of 1.3 or more
  - a max drawdown under 25%
  - a profit in both halves of the test period
  - a profit with double slippage
  - a profit with next-day fills

  Passing makes a strategy a paper-trade candidate. Nothing goes live on a backtest alone.
- **An equity chart** against SPY buy-and-hold.
- **Account sizes:** $100 with safeguards on, $100 with them off, $2,000 and $5,000.
- **Stress tests:** zero commission, double slippage, fills a day late, and each half of the
  period on its own.
- **A luck check.** The trades are reshuffled 5,000 times to show the range of outcomes.
- **Every trade, and every risk-manager intervention.**

## What the backtest can and cannot tell you

There's no free history of SPY option quotes. Every option is priced with Black-Scholes, using
an implied volatility of 0.90 × VIX plus put skew. That captures price moves, volatility moves
and skew, which are the things that drive these trades.

It doesn't capture:

- real bid/ask spreads on a given day (slippage is a flat assumption)
- intraday stops (everything is checked at the close)
- early assignment
- the exact expiry calendar (weeklies are assumed from mid-2010, monthlies before that)

Before any live money, the next step is to validate against real option quotes (ThetaData
and Polygon sell them) or three months of paper trading.

The defaults are textbook values, fixed before testing. Changing them until the chart looks
good is how backtests lie. The bundled sample covers 2014–2018. Your full run adds 2005–2013
and 2019–today, which were never used in development. That makes them an honest
out-of-sample test.

## Roadmap

1. **Done:** the data layer, Regime Gate, two strategies, risk manager, backtester, HTML
   report, daily signal and tests.
2. **Research round.** Test a short, written-down list of ideas on 2014–2018, then confirm them
   on the years never touched. Candidates:
   - sell premium only when VIX is high relative to its own past year
   - time stops in place of price stops on the pullback spread
   - bear-regime call spreads
3. **Paper trading.** Run `today` each afternoon and place the trades in thinkorswim
   paperMoney. After 30 or more trades, compare the results with the backtest.
4. **Broker connection: Schwab only.** Schwab's Trader API is official and free with an
   account. Approval of a developer app takes a few days, and it needs a weekly re-login.
   Orders will be limit-only, run in paper mode first, and pass the same risk manager, with a
   hard daily loss limit.

   Robinhood has no official API for options orders. The unofficial libraries break its terms
   and can get an account locked, so it isn't supported.

## Layout

```
pfo/
  data.py        downloads, CSV cache, loading
  indicators.py  SMA / EMA / RSI
  pricing.py     Black-Scholes, VIX-based vol surface, strike and expiry selection
  regime.py      the PrimeFlow Regime Gate
  strategies.py  the two strategies
  risk.py        position sizing and circuit breakers
  backtest.py    the daily event loop
  metrics.py     statistics and the Monte Carlo luck check
  study.py       the standard study and the go/no-go gate
  report.py      console table and HTML report
  today.py       today's signal
  config.py      every tunable number
data/sample/     S&P 500 / 10 (SPY proxy) 2013-2018 and VIX 2014-2018, for offline runs and tests.
                 Source: the datasets bundled with the `arch` Python package.
```
