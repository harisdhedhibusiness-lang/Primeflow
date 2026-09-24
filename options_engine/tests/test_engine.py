"""Run with:  python -m unittest discover -s tests"""

import math
import os
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pfo.backtest import run_backtest  # noqa: E402
from pfo.config import CostConfig, RiskConfig  # noqa: E402
from pfo.data import SAMPLE_DIR, Bar, load_market, parse_csv  # noqa: E402
from pfo.indicators import ema, rsi, sma  # noqa: E402
from pfo.metrics import compute_stats, max_drawdown  # noqa: E402
from pfo.pricing import OptionPricer, bs_delta, bs_price, pick_expiry, third_friday  # noqa: E402
from pfo.regime import MarketFrame  # noqa: E402
from pfo.risk import RiskManager  # noqa: E402
from pfo.strategies import PullbackCallSpread, TrendPutSpread  # noqa: E402
from pfo.study import run_study  # noqa: E402


def synthetic(days=900, drift=0.0004, vol=0.008, vix=14.0, crash_at=None, seed=1):
    rng = random.Random(seed)
    d, px, bars = date(2015, 1, 2), 200.0, []
    while len(bars) < days:
        if d.weekday() < 5:
            shock = -0.07 if crash_at is not None and crash_at <= len(bars) < crash_at + 5 else 0.0
            px *= 1 + drift + vol * rng.gauss(0, 1) + shock
            v = vix * (3.0 if crash_at is not None and len(bars) >= crash_at else 1.0)
            bars.append(Bar(d, px, px * 1.004, px * 0.996, px, v, v * 1.08))
        d += timedelta(days=1)
    return bars


class Pricing(unittest.TestCase):
    def test_put_call_parity(self):
        S, K, T, r, q, s = 450.0, 440.0, 0.12, 0.03, 0.013, 0.18
        c, p = bs_price(S, K, T, r, q, s, True), bs_price(S, K, T, r, q, s, False)
        self.assertAlmostEqual(c - p, S * math.exp(-q * T) - K * math.exp(-r * T), places=8)

    def test_delta_bounds_and_expiry_intrinsic(self):
        self.assertTrue(0 < bs_delta(100, 100, 0.1, 0.03, 0, 0.2, True) < 1)
        self.assertTrue(-1 < bs_delta(100, 100, 0.1, 0.03, 0, 0.2, False) < 0)
        self.assertEqual(bs_price(100, 90, 0, 0.03, 0, 0.2, False), 0.0)
        self.assertEqual(bs_price(100, 90, 0, 0.03, 0, 0.2, True), 10.0)

    def test_skew_makes_otm_puts_richer(self):
        pr = OptionPricer()
        self.assertGreater(pr.iv(500, 470, 0.12, 16), pr.iv(500, 500, 0.12, 16))
        self.assertLess(pr.iv(500, 530, 0.12, 16), pr.iv(500, 500, 0.12, 16))

    def test_strike_for_delta(self):
        pr = OptionPricer()
        k = pr.strike_for_delta(500, 45 / 365, 16, 0.20, is_call=False)
        self.assertLess(k, 500)
        self.assertAlmostEqual(abs(pr.delta(500, k, 45 / 365, False, 16)), 0.20, delta=0.02)

    def test_expiry_calendar(self):
        self.assertEqual(third_friday(2024, 3), date(2024, 3, 15))
        old = pick_expiry(date(2008, 3, 3), 45, 30, 60)
        self.assertEqual(old, third_friday(old.year, old.month))  # monthlies only pre-2010
        new = pick_expiry(date(2024, 3, 4), 45, 30, 60)
        self.assertEqual(new.weekday(), 4)
        self.assertLessEqual(abs((new - date(2024, 3, 4)).days - 45), 3)


class Indicators(unittest.TestCase):
    def test_sma_ema_rsi(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        e = ema([1.0] * 10, 3)
        self.assertEqual(e[2], 1.0)
        self.assertEqual(e[-1], 1.0)
        self.assertEqual(rsi([float(i) for i in range(10)], 2)[-1], 100.0)
        self.assertLess(rsi([float(10 - i) for i in range(10)], 2)[-1], 1.0)


class Risk(unittest.TestCase):
    def test_sizing(self):
        rm = RiskManager(RiskConfig(starting_equity=5000))
        self.assertEqual(rm.size(5000, 100, 0), 2)  # 5% of 5000 = 250 -> 2 contracts
        tiny = RiskManager(RiskConfig(starting_equity=100))
        self.assertEqual(tiny.size(100, 90, 0), 0)  # 90% of the account: refused
        self.assertEqual(tiny.size(100, 50, 0), 1)  # under the 60% small-account cap
        self.assertEqual(rm.size(5000, 100, 2950), 0)  # total-risk cap reached

    def test_circuit_breakers(self):
        rm = RiskManager(RiskConfig(starting_equity=1000))
        rm.update(0, "d0", 1000)
        rm.update(1, "d1", 740)  # 26% off the peak
        self.assertEqual(rm.can_open(2, 0), (False, "drawdown_halt"))
        rm.update(30, "d30", 480)  # below the 50% floor
        self.assertEqual(rm.can_open(31, 0), (False, "kill_switch"))

    def test_loss_streak(self):
        rm = RiskManager(RiskConfig())
        for i in range(3):
            rm.on_trade_closed(i, "d", -1.0)
        self.assertEqual(rm.can_open(3, 0), (False, "loss_streak_pause"))


class Backtest(unittest.TestCase):
    def test_trades_happen_and_losses_are_capped(self):
        mf = MarketFrame(synthetic())
        for strat in (TrendPutSpread(), PullbackCallSpread()):
            res = run_backtest(mf, strat, RiskConfig(starting_equity=10000))
            self.assertGreater(len(res.trades), 3, strat.name)
            for t in res.trades:
                self.assertGreaterEqual(t.pnl, -t.max_loss - 1e-6)
            self.assertAlmostEqual(res.end_equity, 10000 + sum(t.pnl for t in res.trades), places=6)

    def test_crash_triggers_protection(self):
        mf = MarketFrame(synthetic(days=700, crash_at=450))
        res = run_backtest(mf, TrendPutSpread(), RiskConfig(starting_equity=10000))
        crash_day = mf.dates[450]
        after = [t for t in res.trades if t.entry_date > crash_day + timedelta(days=10)]
        self.assertEqual(after, [])  # the regime gate stops new entries after the crash
        self.assertTrue(any(t.reason in ("stop_loss", "bias_turned_bear") for t in res.trades))

    def test_small_account_is_blocked(self):
        mf = MarketFrame(synthetic())
        res = run_backtest(mf, TrendPutSpread(), RiskConfig(starting_equity=100))
        self.assertEqual(res.trades, [])
        self.assertGreater(res.blocked["too_small_to_afford"], 0)

    def test_costs_reduce_results(self):
        mf = MarketFrame(synthetic())
        free = run_backtest(mf, TrendPutSpread(), RiskConfig(starting_equity=10000), CostConfig(0, 0))
        paid = run_backtest(mf, TrendPutSpread(), RiskConfig(starting_equity=10000))
        self.assertGreater(free.end_equity, paid.end_equity)


class Data(unittest.TestCase):
    def test_parse_formats(self):
        yahoo = "Date,Open,High,Low,Close,Adj Close,Volume\n2024-01-02,1,2,0.5,1.5,1.4,100\n"
        self.assertEqual(parse_csv(yahoo)[0][4], 1.5)
        fred = "observation_date,VIXCLS\n2024-01-01,.\n2024-01-02,13.2\n"
        self.assertEqual(len(parse_csv(fred)), 1)
        cboe = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2024,13,14,12,13.5\n"
        self.assertEqual(parse_csv(cboe)[0][0], date(2024, 1, 2))

    def test_load_forward_fills_short_vix_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "SPY.csv"), "w") as f:
                f.write("Date,Close\n2024-01-02,470\n2024-01-03,468\n2024-01-04,467\n")
            with open(os.path.join(tmp, "VIX.csv"), "w") as f:
                f.write("Date,Close\n2024-01-02,13\n")
            bars = load_market(tmp)
            self.assertEqual([b.vix for b in bars], [13, 13, 13])
            self.assertEqual(load_market(tmp, max_fill_days=1)[-1].vix, None)


class Search(unittest.TestCase):
    def test_lower_bound(self):
        from pfo.search_report import PROOF_TRADES, win_rate_lower_bound

        self.assertAlmostEqual(win_rate_lower_bound(30, 30), 0.05 ** (1 / 30))
        self.assertLess(win_rate_lower_bound(95, 100), 0.95)
        self.assertGreater(win_rate_lower_bound(95, 100), 0.88)
        self.assertEqual(PROOF_TRADES, 299)

    def test_single_leg_rules(self):
        from pfo import search
        from pfo.config import PricingConfig

        search._init(SAMPLE_DIR, PricingConfig(), CostConfig())
        mf = search._S["mf"]
        lo = next(i for i, r in enumerate(mf.regime) if r is not None)
        buy = search.Rule("buy", "call", "bull_trend", 30, 0.5, 0.25, 0.5, 10)
        sell = search.Rule("sell", "put", "bull_trend", 30, 0.10, 1.0, None, 999)
        for rule in (buy, sell):
            trades = search.simulate(rule, lo, len(mf) - 1)
            self.assertGreater(len(trades), 10)
            for a, b in zip(trades, trades[1:]):
                self.assertLessEqual(a.exit, b.entry)  # never overlapping
        for t in search.simulate(buy, lo, len(mf) - 1):
            self.assertGreaterEqual(t.pnl, -(t.cost + 1.3 + 1e-6))  # can't lose more than paid
        every = search.every_entry_day(sell, lo, len(mf) - 1)
        self.assertGreater(len(every), len(search.simulate(sell, lo, len(mf) - 1)))


class Calibration(unittest.TestCase):
    def test_put_spreads_never_negative(self):
        pr = OptionPricer()
        for days in (6, 14, 30, 60):
            T = days / 365
            for k in range(600, 770, 5):
                self.assertGreater(pr.price(773, k, T, False, 14) - pr.price(773, k - 5, T, False, 14), -1e-9)

    def test_term_vol_uses_the_right_index(self):
        bars = synthetic(days=260)
        for b in bars:
            b.vix, b.vix9d, b.vix3m = 20.0, 15.0, 25.0
        mf = MarketFrame(bars)
        i = len(mf) - 1
        self.assertEqual(mf.term_vol(i, 7), 15.0)
        self.assertEqual(mf.term_vol(i, 120), 25.0)
        self.assertTrue(15.0 < mf.term_vol(i, 20) < 20.0 < mf.term_vol(i, 60) < 25.0)
        bars[-1].vix9d = None  # missing index: estimated from VIX
        self.assertAlmostEqual(MarketFrame(bars).term_vol(i, 7), 20.0 * 0.946 + (20.0 - 18.0) / 4.5 * 20.0 * 0.028, places=6)


class LiveChain(unittest.TestCase):
    PAYLOAD = {"timestamp": "2026-09-22 16:15:00", "data": {"current_price": 773.38, "options": [
        {"option": "SPY261030C00835000", "bid": 0.33, "ask": 0.34, "delta": 0.031, "iv": 0.2},
        {"option": "SPY261030C00840000", "bid": 0.26, "ask": 0.27, "delta": 0.025, "iv": 0.21},
        {"option": "SPY261030C00800000", "bid": 1.5, "ask": 1.6, "delta": 0.12, "iv": 0.15},
    ]}}

    def test_parse_and_pick(self):
        from pfo.chain import parse
        from pfo.strategies import CeilingCallSpread

        chain = parse(self.PAYLOAD)
        plan = CeilingCallSpread().live_plan(chain, date(2026, 9, 22))
        self.assertEqual((plan["short"].strike, plan["long"].strike), (835.0, 840.0))
        self.assertAlmostEqual(plan["mid"], 0.07)
        self.assertAlmostEqual(plan["natural"], 0.06)

    def test_today_prints_live_trade(self):
        from pfo.chain import parse
        from pfo.strategies import CeilingCallSpread
        from pfo.today import todays_signal

        mf = MarketFrame(synthetic(days=300))
        mf.dates[-1] = date(2026, 9, 22)
        text = todays_signal(mf, [CeilingCallSpread()], 500, chain_loader=lambda: parse(self.PAYLOAD))
        self.assertIn("SELL 835 call", text)
        self.assertIn("99% of a $500 account", text)


class Integration(unittest.TestCase):
    def test_sample_study(self):
        mf = MarketFrame(load_market(SAMPLE_DIR))
        study = run_study(mf, ["put_spread", "dip_call"], source="sample")
        self.assertEqual(len(study.strategies), 2)
        for st in study.strategies:
            self.assertEqual(st.main[0].stats["trades"], 0)  # $100 with safeguards: never affordable
            self.assertGreater(st.headline.stats["trades"], 30)
            self.assertTrue(0 <= compute_stats(st.headline.result)["max_drawdown"] < 1)
        self.assertAlmostEqual(max_drawdown([100, 50, 100]), 0.5)


if __name__ == "__main__":
    unittest.main()
