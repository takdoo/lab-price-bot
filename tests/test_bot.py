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
    @patch.object(bot, "cleanup_telegram_service_messages", lambda listen_seconds=0: 0)
    @patch.object(bot, "telegram_api")
    @patch.object(bot, "BOT_TOKEN", "test-token")
    @patch.object(bot, "CHAT_ID", "@lab_chats")
    @patch.object(bot.HTTP, "post")
    def test_new_photo_message_is_sent_and_pinned(self, mocked_post, mocked_api):
        mocked_api.side_effect = [
            {"id": 123},  # getMe
            {},           # getChat: no previous pinned message
            {"message_id": 67890},  # pinChatMessage
        ]
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 67890}}
        mocked_post.return_value = response
        with patch("builtins.open", unittest.mock.mock_open(read_data=b"image")):
            bot.send_to_telegram("image.jpg", "🧪 <b>گزارش لحظه‌ای LAB</b>")
        self.assertEqual(mocked_post.call_count, 1)
        self.assertIn("/sendPhoto", mocked_post.call_args.args[0])
        self.assertEqual(mocked_api.call_args_list[-1].args[0], "pinChatMessage")

    @patch.object(bot, "cleanup_telegram_service_messages", lambda: 0)
    @patch.object(bot, "telegram_api")
    @patch.object(bot, "BOT_TOKEN", "test-token")
    @patch.object(bot, "CHAT_ID", "@lab_chats")
    @patch.object(bot.HTTP, "post")
    def test_previous_pinned_lab_post_is_deleted_after_new_pin(self, mocked_post, mocked_api):
        mocked_api.side_effect = [
            {"id": 123},
            {"pinned_message": {
                "message_id": 111,
                "from": {"id": 123},
                "caption": "🧪 <b>گزارش لحظه‌ای LAB</b>",
            }},
            {"message_id": 222},
            {"message_id": 222},
            True,
        ]
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 222}}
        mocked_post.return_value = response
        with patch("builtins.open", unittest.mock.mock_open(read_data=b"image")):
            bot.send_to_telegram("image.jpg", "🧪 <b>گزارش لحظه‌ای LAB</b>")
        self.assertEqual(mocked_api.call_args_list[-1].args[0], "deleteMessage")
        self.assertEqual(mocked_api.call_args_list[-1].args[1]["message_id"], 111)


    def test_service_message_detector_ignores_normal_user_messages(self):
        self.assertTrue(bot.is_telegram_service_message({
            "message_id": 10, "new_chat_members": [{"id": 1}]
        }))
        self.assertTrue(bot.is_telegram_service_message({
            "message_id": 11, "pinned_message": {"message_id": 5}
        }))
        self.assertFalse(bot.is_telegram_service_message({
            "message_id": 12, "text": "سلام بچه‌ها"
        }))

    @patch.object(bot, "telegram_api")
    @patch.object(bot, "BOT_TOKEN", "test-token")
    @patch.object(bot, "CHAT_ID", "@lab_chats")
    def test_cleanup_deletes_service_messages_but_keeps_normal_messages(self, mocked_api):
        mocked_api.side_effect = [
            {"url": ""},
            {"id": 123},
            {"id": -100123, "type": "supergroup"},
            {"status": "administrator", "can_delete_messages": True},
            [
                {"update_id": 20, "message": {
                    "message_id": 501, "chat": {"id": -100123},
                    "new_chat_members": [{"id": 456}],
                }},
                {"update_id": 21, "message": {
                    "message_id": 502, "chat": {"id": -100123},
                    "text": "پیام معمولی گروه",
                }},
            ],
            True,
            [],
        ]
        deleted = bot.cleanup_telegram_service_messages()
        self.assertEqual(deleted, 1)
        delete_calls = [call for call in mocked_api.call_args_list if call.args and call.args[0] == "deleteMessage"]
        self.assertEqual(len(delete_calls), 1)
        self.assertEqual(delete_calls[0].args[1]["message_id"], 501)

    @patch.object(bot, "telegram_api")
    @patch.object(bot, "BOT_TOKEN", "test-token")
    def test_cleanup_skips_polling_when_webhook_is_active(self, mocked_api):
        mocked_api.return_value = {"url": "https://example.test/webhook"}
        self.assertEqual(bot.cleanup_telegram_service_messages(), 0)
        self.assertEqual(mocked_api.call_count, 1)
        self.assertEqual(mocked_api.call_args.args[0], "getWebhookInfo")


class JarvisTests(unittest.TestCase):
    @patch.object(bot, "send_jarvis_text")
    @patch.object(bot, "JARVIS_ADMIN_ID", 5477408842)
    @patch.object(bot, "CHAT_ID", "@lab_chats")
    def test_only_admin_can_call_jarvis(self, mocked_send):
        bot.telegram_chat_id._resolved = -100123
        normal_user = {
            "from": {"id": 111111},
            "chat": {"id": -100123, "type": "supergroup"},
            "text": "جرویس سلام",
        }
        self.assertFalse(bot.handle_jarvis_message(normal_user))
        mocked_send.assert_not_called()

        admin = {
            "from": {"id": 5477408842},
            "chat": {"id": -100123, "type": "supergroup"},
            "text": "جرویس",
        }
        self.assertTrue(bot.handle_jarvis_message(admin))
        mocked_send.assert_called_once_with(
            -100123, "بله قربان، در خدمتم. موضوع یا سؤالتون رو بفرمایید."
        )

    @patch.object(bot, "send_jarvis_text")
    @patch.object(bot, "JARVIS_ADMIN_ID", 5477408842)
    def test_jarvis_ignores_admin_messages_without_trigger(self, mocked_send):
        bot.telegram_chat_id._resolved = -100123
        admin_message = {
            "from": {"id": 5477408842},
            "chat": {"id": -100123, "type": "supergroup"},
            "text": "سلام گروه",
        }
        self.assertFalse(bot.handle_jarvis_message(admin_message))
        mocked_send.assert_not_called()

    @patch.object(bot, "send_jarvis_text")
    def test_creator_identity_is_deterministic(self, mocked_send):
        self.assertTrue(bot.jarvis_admin_command("خالق تو کیه", -100123))
        mocked_send.assert_called_once_with(
            -100123, "قربان، خالق و سازنده من علی حامدی هست."
        )

    def test_persian_command_spacing_is_normalized(self):
        self.assertEqual(
            bot.normalize_jarvis_command("پیام‌های خودتو پاک کن"),
            "پیام های خودتو پاک کن",
        )

    @patch.object(bot, "send_jarvis_text")
    @patch.object(bot, "telegram_api")
    def test_lock_command_refuses_without_restrict_permission(self, mocked_api, mocked_send):
        mocked_api.side_effect = [
            {"id": 999},
            {"status": "administrator", "can_restrict_members": False},
        ]
        self.assertTrue(bot.jarvis_admin_command("قفل گروه", -100123))
        mocked_api.assert_called()
        mocked_send.assert_called_once()
        self.assertIn("Restrict Members", mocked_send.call_args.args[1])

    @patch.object(bot.HTTP, "post")
    @patch.object(bot, "OPENROUTER_API_KEY", "test-key")
    def test_jarvis_uses_free_router_only(self, mocked_post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "پاسخ آزمایشی"}}]
        }
        mocked_post.return_value = response
        answer = bot.ask_openrouter_free("LAB چیست؟", [
            {"title": "منبع", "url": "https://example.com", "snippet": "متن منبع"}
        ])
        self.assertEqual(answer, "پاسخ آزمایشی")
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "openrouter/free")
        self.assertIn("openrouter.ai/api/v1/chat/completions", mocked_post.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
