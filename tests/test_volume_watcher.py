import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta

import volume_watcher as vw


class VolumeWatcherTests(unittest.TestCase):
    def test_spot_trades_split_buy_and_sell(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        response = Mock()
        response.json.return_value = [
            {"create_time": str(int(now.timestamp())), "price": "2", "amount": "10", "side": "buy"},
            {"create_time": str(int(now.timestamp())), "price": "3", "amount": "5", "side": "sell"},
        ]
        response.raise_for_status.return_value = None
        with patch.object(vw.requests, "get", return_value=response):
            data = vw.fetch_spot_trades(now)
        self.assertEqual(data["buy_usd"], Decimal("20"))
        self.assertEqual(data["sell_usd"], Decimal("15"))
        self.assertEqual(data["trades"], 2)

    def test_futures_aggtrades_classify_aggressor(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        ms = int(now.timestamp() * 1000)
        response = Mock()
        response.json.return_value = [
            {"T": ms, "p": "2", "q": "10", "m": False, "a": 1},
            {"T": ms, "p": "3", "q": "5", "m": True, "a": 2},
        ]
        response.raise_for_status.return_value = None
        with patch.object(vw.requests, "get", return_value=response):
            data = vw.fetch_futures_trades(now)
        self.assertEqual(data["buy_usd"], Decimal("20"))
        self.assertEqual(data["sell_usd"], Decimal("15"))
        self.assertEqual(data["buy_count"], 1)
        self.assertEqual(data["sell_count"], 1)

    def test_report_discloses_markets_and_five_minute_window(self):
        spot = vw.empty_market()
        futures = vw.empty_market()
        report = vw.build_report(spot, futures, datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc))
        self.assertIn("۵ دقیقه", report)
        self.assertIn("Gate.io", report)
        self.assertIn("Binance", report)
        self.assertIn("توصیه مالی نیست", report)

    def test_report_shows_net_aggressor_pressure(self):
        spot = vw.empty_market()
        spot.update({"buy_usd": Decimal("120"), "sell_usd": Decimal("80"), "buy_count": 2, "sell_count": 1, "trades": 3})
        section = vw.market_section("Spot", spot)
        self.assertIn("برتری خریداران", section)
        self.assertIn("$40.00", section)


if __name__ == "__main__":
    unittest.main()
