import unittest
from decimal import Decimal

from wallet_watcher import compare


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


if __name__ == "__main__":
    unittest.main()
