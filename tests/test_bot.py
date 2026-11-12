import unittest
from unittest.mock import Mock, patch

import bot


class MarketDataTests(unittest.TestCase):
    @patch.object(bot.HTTP, "get")
    def test_market_data_accepts_valid_price(self, mocked_get):
        response = Mock()
        response.json.return_value = {
            "lab": {"usd": 0.041234, "usd_24h_change": -2.5}
        }
        response.raise_for_status.return_value = None
        mocked_get.return_value = response

        self.assertEqual(bot.get_market_data(), (0.041234, -2.5))

    @patch.object(bot.HTTP, "get")
    def test_market_data_rejects_missing_coin(self, mocked_get):
        response = Mock()
        response.json.return_value = {}
        response.raise_for_status.return_value = None
        mocked_get.return_value = response

        with self.assertRaises(ValueError):
            bot.get_market_data()

    @patch.object(bot.HTTP, "get")
    def test_market_data_rejects_zero_price(self, mocked_get):
        response = Mock()
        response.json.return_value = {"lab": {"usd": 0}}
        response.raise_for_status.return_value = None
        mocked_get.return_value = response
        with self.assertRaises(ValueError):
            bot.get_market_data()

    @patch.object(bot.HTTP, "get")
    def test_history_filters_invalid_values(self, mocked_get):
        response = Mock()
        response.json.return_value = {
            "prices": [[1, 0.04], [2, None], [3, 0.05], [4, "bad"]]
        }
        response.raise_for_status.return_value = None
        mocked_get.return_value = response

        self.assertEqual(bot.get_history(), [0.04, 0.05])


class RenderingTests(unittest.TestCase):
    def test_price_color_tracks_market_direction(self):
        self.assertEqual(bot.get_price_color(1.2), (35, 230, 130))
        self.assertEqual(bot.get_price_color(-1.2), (255, 75, 90))
        self.assertEqual(bot.get_price_color(0), (255, 255, 255))
        self.assertEqual(bot.get_price_color(None), (255, 255, 255))

    def test_flat_chart_does_not_crash(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (300, 120), "black")
        draw = ImageDraw.Draw(image)
        bot.draw_chart(draw, [1.0, 1.0, 1.0], 10, 10, 290, 110, "white")

    def test_caption_keeps_five_decimal_price(self):
        caption = bot.make_caption(0.041234, 1.25)
        self.assertIn("$0.04123", caption)
        self.assertIn("هر ۵ دقیقه", caption)

    def test_caption_handles_missing_change(self):
        caption = bot.make_caption(0.041234, None)
        self.assertIn("نامشخص", caption)

    def test_caption_includes_separate_informal_market_comment(self):
        caption = bot.make_caption(0.041234, -4.0, [0.042, 0.041, 0.040, 0.039, 0.042, 0.041, 0.040, 0.038])
        self.assertIn("حرفِ بازار به زبون خودمونی", caption)
        self.assertIn("فشار فروش", caption)
        self.assertNotIn("━━━━━━━━", caption)
        self.assertNotIn("\\n", caption)
        self.assertIn("\n", caption)

    def test_market_comment_handles_insufficient_data(self):
        comment = bot.make_market_comment(-1.0, [0.04, 0.039])
        self.assertIn("داده‌های موجود", comment)


class TelegramTests(unittest.TestCase):
    @patch.object(bot, "BOT_TOKEN", "test-token")
    @patch.object(bot, "CHAT_ID", "@lab_chats")
    @patch.object(bot.HTTP, "post")
    def test_new_photo_message_succeeds(self, mocked_post):
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 67890}}
        mocked_post.return_value = response
        with patch("builtins.open", unittest.mock.mock_open(read_data=b"image")):
            bot.send_to_telegram("image.jpg", "caption")
        self.assertEqual(mocked_post.call_count, 1)
        self.assertIn("/sendPhoto", mocked_post.call_args.args[0])
        self.assertEqual(mocked_post.call_args.kwargs["data"]["chat_id"], "@lab_chats")
        self.assertEqual(mocked_post.call_args.kwargs["data"]["caption"], "caption")

    @patch.object(bot, "BOT_TOKEN", "test-token")
    @patch.object(bot, "CHAT_ID", "")
    def test_missing_chat_id_fails_before_network(self):
        with self.assertRaisesRegex(ValueError, "CHAT_ID"):
            bot.send_to_telegram("image.jpg", "caption")


if __name__ == "__main__":
    unittest.main()
