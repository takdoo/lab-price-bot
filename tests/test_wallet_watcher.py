import unittest
from decimal import Decimal
from unittest.mock import patch

from wallet_watcher import build_report_rows, compare, delete_telegram_message, fetch_holders, fetch_futures_data, futures_caption, TOP_HOLDERS_COUNT


class CompareTopHoldersTests(unittest.TestCase):
    def setUp(self):
        self.a = "0x" + "a" * 40
        self.b = "0x" + "b" * 40
        self.c = "0x" + "c" * 40

    def test_report_rows_show_current_balance_and_delta_for_same_address(self):
        old = {self.a: "100", self.b: "90"}
        current = [
            {"address": self.a, "balance": "125"},
            {"address": self.b, "balance": "90"},
        ]
        rows = build_report_rows(old, current)
        self.assertEqual(rows[0]["balance"], Decimal("125"))
        self.assertEqual(rows[0]["delta"], Decimal("25"))
        self.assertEqual(rows[1]["delta"], Decimal("0"))
        self.assertEqual(TOP_HOLDERS_COUNT, 10)

    def test_no_changes(self):
        old = {self.a: "100", self.b: "90"}
        new = [
            {"address": self.a, "balance": "100"},
            {"address": self.b, "balance": "90"},
        ]
        self.assertEqual(compare(old, new), [])

    def test_balance_increase_and_decrease(self):
        old = {self.a: "100", self.b: "90"}
        new = [
            {"address": self.a, "balance": "125"},
            {"address": self.b, "balance": "80"},
        ]
        changes = compare(old, new)
        by_address = {item["address"]: item for item in changes}
        self.assertEqual(by_address[self.a]["delta"], Decimal("25"))
        self.assertEqual(by_address[self.b]["delta"], Decimal("-10"))

    def test_rank_change_is_reported(self):
        old = {self.a: "100", self.b: "90"}
        new = [
            {"address": self.b, "balance": "90"},
            {"address": self.a, "balance": "100"},
        ]
        changes = compare(old, new)
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(item["delta"] == 0 for item in changes))

    def test_new_holder_enters_top_ten(self):
        old = {self.a: "100"}
        new = [
            {"address": self.c, "balance": "110"},
            {"address": self.a, "balance": "100"},
        ]
        changes = compare(old, new)
        entered = next(item for item in changes if item["address"] == self.c)
        self.assertTrue(entered["entered"])
        self.assertIsNone(entered["delta"])


    @patch("wallet_watcher.requests.post")
    def test_delete_telegram_message_uses_only_tracked_message_id(self, mocked_post):
        class FakeResponse:
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True, "result": True}

        mocked_post.return_value = FakeResponse()
        with patch("wallet_watcher.BOT_TOKEN", "test-token"), patch("wallet_watcher.CHAT_ID", "@lab_chats"):
            self.assertTrue(delete_telegram_message(12345))

        self.assertEqual(
            mocked_post.call_args.args[0],
            "https://api.telegram.org/bottest-token/deleteMessage",
        )
        self.assertEqual(
            mocked_post.call_args.kwargs["data"],
            {"chat_id": "@lab_chats", "message_id": 12345},
        )

    @patch("wallet_watcher.requests.get")
    def test_futures_fetch_uses_public_binance_and_bybit_endpoints(self, mocked_get):
        class FakeResponse:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload

        def response_for(url, **kwargs):
            if url.endswith("/fapi/v1/ticker/24hr"): return FakeResponse({"quoteVolume": "1000000"})
            if url.endswith("/fapi/v1/openInterest"): return FakeResponse({"openInterest": "2000000"})
            if url.endswith("/fapi/v1/premiumIndex"): return FakeResponse({"markPrice": "0.05", "lastFundingRate": "0.0001"})
            if url.endswith("/futures/data/openInterestHist"): return FakeResponse([{"sumOpenInterestValue": "100"}, {"sumOpenInterestValue": "110"}])
            if url.endswith("/v5/market/tickers"): return FakeResponse({"retCode": 0, "result": {"list": [{"turnover24h": "500000", "openInterestValue": "250000", "fundingRate": "0.0002"}]}})
            if url.endswith("/v5/market/open-interest"): return FakeResponse({"result": {"list": [{"openInterest": "110"}, {"openInterest": "100"}]}})
            raise AssertionError("Unexpected URL: " + url)

        mocked_get.side_effect = response_for
        pairs = fetch_futures_data()
        self.assertEqual([item["exchange"] for item in pairs], ["Binance", "Bybit"])
        self.assertEqual(pairs[0]["open_interest_usd"], Decimal("100000.00"))
        self.assertEqual(pairs[0]["oi_change_24h"], Decimal("10"))
        self.assertIn("API عمومی", futures_caption(pairs))
        self.assertIn("لیکوییدیشن", futures_caption(pairs))
    @patch("wallet_watcher.requests.post")
    def test_holder_fetch_uses_bnb_slug_and_returns_top_ten(self, mocked_post):
        rows = [
            {"walletAddress": "0x" + format(i, "040x"), "actualBalance": str(1000 - i)}
            for i in range(20)
        ]

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {"status": {"error_code": "0"}, "data": {"holders": rows}}

        mocked_post.return_value = FakeResponse()
        with patch("wallet_watcher.fetch_onchain_balances", side_effect=lambda addresses: ({row["walletAddress"].lower(): row["actualBalance"] for row in rows}, {"block_number": 123, "ui_multiplier": "1"})):
            holders = fetch_holders()

        self.assertEqual(len(holders), TOP_HOLDERS_COUNT)
        self.assertEqual(holders[0]["balance"], "1000")
        self.assertEqual(mocked_post.call_args.kwargs["json"]["platform"], "bsc")

    @patch("wallet_watcher.requests.post")
    def test_holder_fetch_falls_back_after_parameter_error(self, mocked_post):
        rows = [
            {"walletAddress": "0x" + format(i, "040x"), "balance": str(1000 - i)}
            for i in range(20)
        ]

        class FakeResponse:
            def __init__(self, ok, status_code, text, payload=None):
                self.ok = ok
                self.status_code = status_code
                self.text = text
                self._payload = payload

            def json(self):
                return self._payload

        mocked_post.side_effect = [
            FakeResponse(False, 400, '{"status":{"error_message":"Parameter error"}}'),
            FakeResponse(True, 200, "", {"status": {"error_code": 0}, "data": {"holders": rows}}),
        ]
        with patch("wallet_watcher.fetch_onchain_balances", side_effect=lambda addresses: ({row["walletAddress"].lower(): row["balance"] for row in rows}, {"block_number": 123, "ui_multiplier": "1"})):
            holders = fetch_holders()

        self.assertEqual(len(holders), TOP_HOLDERS_COUNT)
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(mocked_post.call_args.kwargs["json"]["platform"], "BNB Smart Chain")


if __name__ == "__main__":
    unittest.main()
