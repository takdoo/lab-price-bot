import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import usd_toman_watcher as usd


class UsdTomanWatcherTests(unittest.TestCase):
    def test_fetches_free_market_usd_in_toman(self):
        response = Mock()
        response.json.return_value = {
            "price": 265000,
            "unit": "IRT",
            "updated_utc": "2026-11-27T11:00:00Z",
            "stale": False,
            "change_pct": 0.4,
            "sources": ["tgju", "bonbast"],
        }
        response.raise_for_status.return_value = None
        with patch.object(usd.requests, "get", return_value=response):
            result = usd.fetch_usd_rate()
        self.assertEqual(result["price"], Decimal("265000"))
        self.assertEqual(result["source_count"], 2)
        self.assertFalse(result["stale"])

    def test_rejects_non_toman_unit(self):
        response = Mock()
        response.json.return_value = {"price": 265000, "unit": "IRR", "updated_utc": "2026-11-27T11:00:00Z"}
        response.raise_for_status.return_value = None
        with patch.object(usd.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                usd.fetch_usd_rate()

    def test_report_shows_price_source_and_both_times(self):
        rate = {
            "price": Decimal("265000"),
            "updated": "2026-11-27T11:00:00Z",
            "stale": False,
            "change_pct": Decimal("0.4"),
            "source_count": 2,
        }
        report = usd.build_report(rate)
        self.assertIn("۲۶۵٬۰۰۰ تومان", report)
        self.assertIn("NerkhAra", report)
        self.assertIn("زمان آخرین به‌روزرسانی منبع", report)
        self.assertIn("زمان ارسال (ایران)", report)

    def test_publish_sends_new_before_deleting_previous(self):
        with patch.object(usd, "BOT_TOKEN", "token"), \\
             patch.object(usd, "CHAT_ID", "@labcoinss"), \\
             patch.object(usd, "load_message_id", return_value=111), \\
             patch.object(usd, "telegram_call", side_effect=[{"message_id": 222}, {}]) as telegram, \\
             patch.object(usd, "save_message_id") as save:
            self.assertEqual(usd.publish_report("test"), 222)
        self.assertEqual(telegram.call_args_list[0].args[0], "sendMessage")
        self.assertEqual(telegram.call_args_list[1].args[0], "deleteMessage")
        save.assert_called_once_with(222)


if __name__ == "__main__":
    unittest.main()
