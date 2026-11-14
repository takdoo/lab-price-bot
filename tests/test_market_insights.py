import unittest
from decimal import Decimal
from unittest.mock import patch
from market_insights import build_report, dec, fetch_spot_markets

class Tests(unittest.TestCase):
    def test_decimal(self):
        self.assertIsNone(dec("bad"))
        self.assertEqual(dec("1.25"),Decimal("1.25"))
    def test_report(self):
        report=build_report([{"exchange":"Binance","price":Decimal(".05"),"volume":Decimal(1000),"change":Decimal("3.2")}],
          {"latest":Decimal(2000),"average":Decimal(900),"ratio":Decimal("2.22"),"spike":True},
          {"support":Decimal(".04"),"resistance":Decimal(".06")})
        self.assertIn("جهش حجم",report)
        self.assertIn("حمایت محاسباتی",report)
        self.assertIn("نه سیگنال خرید یا فروش",report)
    @patch("market_insights.get")
    def test_unlisted_pair_omitted(self,m):
        m.side_effect=RuntimeError("not listed")
        self.assertEqual(fetch_spot_markets(),[])

if __name__=="__main__":
    unittest.main()
