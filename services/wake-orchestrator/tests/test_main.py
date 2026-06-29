import unittest

from app.main import _is_chat_event


class MainTests(unittest.TestCase):
    def test_is_chat_event_accepts_only_chat_inputs(self):
        self.assertTrue(_is_chat_event({"type": "chat.received"}))
        self.assertTrue(_is_chat_event({"event": "chat.sent"}))
        self.assertTrue(_is_chat_event({"type": "chat.messages"}))
        self.assertFalse(_is_chat_event({"type": "transcript"}))
        self.assertFalse(_is_chat_event({"type": "chat.new_message"}))


if __name__ == "__main__":
    unittest.main()
