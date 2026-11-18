import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from datetime import datetime, timezone

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

    def test_gate_futures_trades_split_by_signed_size(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        contract_response = Mock()
        contract_response.json.return_value = {"name": "LAB_USDT", "status": "trading", "type": "direct", "quanto_multiplier": "1"}
        contract_response.raise_for_status.return_value = None
        trades_response = Mock()
        trades_response.json.return_value = [
            {"create_time_ms": now.timestamp() - 0.125, "price": "2", "size": "10"},
            {"create_time_ms": now.timestamp() - 0.250, "price": "3", "size": "-5"},
        ]
        trades_response.raise_for_status.return_value = None
        with patch.object(vw.requests, "get", side_effect=[contract_response, trades_response]):
            data = vw.fetch_futures_trades(now)
        self.assertEqual(data["buy_usd"], Decimal("20"))
        self.assertEqual(data["sell_usd"], Decimal("15"))
        self.assertEqual(data["buy_count"], 1)
        self.assertEqual(data["sell_count"], 1)
        self.assertEqual(data["trades"], 2)

    def test_report_discloses_markets_and_five_minute_window(self):
        report = vw.build_report(vw.empty_market(), vw.empty_market(), datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc))
        self.assertIn("۵ دقیقه", report)
        self.assertIn("Gate.io", report)
        self.assertIn("Futures", report)
        self.assertIn("پیش‌بینی قیمت نیست", report)
        self.assertIn("فقط ۵ دقیقه اخیر", report)
        self.assertIn("اسپات (Spot)", report)
        self.assertIn("فیوچرز (Futures)", report)

    def test_report_shows_net_aggressor_pressure(self):
        spot = vw.empty_market()
        spot.update({"buy_usd": Decimal("120"), "sell_usd": Decimal("80"), "buy_count": 2, "sell_count": 1, "trades": 3})
        section = vw.market_section("Spot", spot)
        self.assertIn("خریدارها", section)
        self.assertIn("بیشتر خرید کردن", section)
        self.assertIn("$40.00", section)
        self.assertIn("جمع کل", section)

    def test_load_message_id_uses_saved_state(self):
        with patch.object(vw, "STATE_FILE") as state_file:
            state_file.read_text.return_value = "999"
            self.assertEqual(vw.load_message_id(), 999)


if __name__ == "__main__":
    unittest.main()
