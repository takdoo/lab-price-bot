import unittest
from decimal import Decimal
from unittest.mock import patch

from wallet_watcher import compare, fetch_holders


class CompareTopHoldersTests(unittest.TestCase):
    def setUp(self):
        self.a = "0x" + "a" * 40
        self.b = "0x" + "b" * 40
        self.c = "0x" + "c" * 40

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
    def test_holder_fetch_uses_bnb_slug_and_parses_top_ten(self, mocked_post):
        rows = [
            {"walletAddress": "0x" + format(i, "040x"), "actualBalance": str(1000 - i)}
            for i in range(10)
        ]

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {"status": {"error_code": "0"}, "data": {"holders": rows}}

        mocked_post.return_value = FakeResponse()
        holders = fetch_holders()

        self.assertEqual(len(holders), 10)
        self.assertEqual(holders[0]["balance"], "1000")
        self.assertEqual(mocked_post.call_args.kwargs["json"]["platform"], "bsc")

    @patch("wallet_watcher.requests.post")
    def test_holder_fetch_falls_back_after_parameter_error(self, mocked_post):
        rows = [
            {"walletAddress": "0x" + format(i, "040x"), "balance": str(1000 - i)}
            for i in range(10)
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
        holders = fetch_holders()

        self.assertEqual(len(holders), 10)
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(mocked_post.call_args.kwargs["json"]["platform"], "BNB Smart Chain")


if __name__ == "__main__":
    unittest.main()
