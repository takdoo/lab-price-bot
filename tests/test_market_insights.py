import unittest
from decimal import Decimal
from unittest.mock import patch
from market_insights import build_report, dec, fetch_spot_markets, send_reports

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

    @patch("market_insights.send_report")
    def test_sends_to_both_unique_groups(self, send):
        send.side_effect=[101,202]
        with patch.dict("os.environ", {"CHAT_IDS":"@lab_chats,@labcoinss,@lab_chats"}):
            result=send_reports("report")
        self.assertEqual([x[0] for x in result],["@lab_chats","@labcoinss"])
        self.assertEqual(send.call_count,2)

if __name__=="__main__":
    unittest.main()
