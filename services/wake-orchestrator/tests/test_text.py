import unittest

from app.text import clean_for_tts, detect_wake, is_echo_of_bot, is_wake_only_text, normalize_ja


WAKE_WORDS = ["カボス", "ねえカボス", "カボスさん", "かぼす", "カボちゃん", "カーブス"]
NEGATIVE = ["カボスって", "カボスの", "カボスを使って"]


class TextTests(unittest.TestCase):
    def test_normalize_maps_kabosu_variants(self):
        self.assertEqual(normalize_ja("ねえ、かぼす！"), "ねえkabosu")
        self.assertEqual(normalize_ja("かばす？"), "kabosu")


    def test_detects_kabosu_wake_word(self):
        result = detect_wake("カボス、今の論点まとめて", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.wake, "カボス")
        self.assertEqual(result.seed, "今の論点まとめて")


    def test_detects_short_direct_kabosu_command(self):
        result = detect_wake("カボス、要約して", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "要約して")


    def test_detects_joined_kabosu_command_from_transcript(self):
        result = detect_wake("カボスこのアイデアどう思いますか？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "このアイデアどう思いますか？")


    def test_detects_asr_variant_with_leading_filler(self):
        result = detect_wake("はい、Koposこのアイデアどう思いますか？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "このアイデアどう思いますか？")


    def test_detects_natural_filler_before_wake_word(self):
        result = detect_wake("あ、カボス、短めに自己紹介して。", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "短めに自己紹介して。")


    def test_detects_fuzzy_japanese_asr_variants(self):
        cases = [
            "カーボス、短めに自己紹介して。",
            "カボズ、短めに自己紹介して。",
            "カボ酢、短めに自己紹介して。",
            "かばす、短めに自己紹介して。",
            "カーブス、短めに自己紹介して。",
            "コポス、短めに自己紹介して。",
            "OK、カボス、短めに自己紹介して。",
        ]
        for text in cases:
            with self.subTest(text=text):
                result = detect_wake(text, WAKE_WORDS, NEGATIVE)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.seed, "短めに自己紹介して。")


    def test_detects_wake_after_sentence_boundary_in_joined_asr_segment(self):
        result = detect_wake("えぇーめっちゃ喋ったのに？かばす？この会議の内容の感想を教えて？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "この会議の内容の感想を教えて？")


    def test_detects_wake_after_newline_in_joined_asr_segment(self):
        result = detect_wake("メールが来たんだけど\nかばす？この内容どう思う？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "この内容どう思う？")

        result = detect_wake("ずーっとカボスのテストしてるんだよ\nカボス？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "")


    def test_detects_wake_in_middle_or_end_of_utterance(self):
        result = detect_wake("この件カボスどう思う？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "この件 どう思う？")

        result = detect_wake("今の論点まとめて、カボス", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "今の論点まとめて")

        result = detect_wake("資料の最後、かばすにも聞いて", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "資料の最後 にも聞いて")


    def test_repeated_wake_only_stays_ack_flow(self):
        result = detect_wake("かばす？かばす？", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "")

        result = detect_wake("カボスカボスカボスカボスちゃん", WAKE_WORDS, NEGATIVE)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.seed, "")


    def test_wake_only_text_recognizes_repeated_variants(self):
        self.assertTrue(is_wake_only_text("カボスカボスカボスカボスちゃん"))
        self.assertTrue(is_wake_only_text("かばす？かばす？"))
        self.assertFalse(is_wake_only_text("カボスこの会議の内容感想教えて"))


    def test_detects_non_direct_kabosu_mentions_by_design(self):
        self.assertIsNotNone(detect_wake("このカボスの仕組みを確認します", WAKE_WORDS, NEGATIVE))


    def test_does_not_default_to_vexa_wake_word(self):
        self.assertIsNone(detect_wake("ねえVexa、今の論点まとめて", WAKE_WORDS, NEGATIVE))


    def test_detects_configured_negative_phrases_by_design(self):
        self.assertIsNotNone(detect_wake("カボスってどういう仕組み？", WAKE_WORDS, NEGATIVE))
        self.assertIsNotNone(detect_wake("カボスの仕組みを確認して", WAKE_WORDS, NEGATIVE))


    def test_detects_descriptive_kabosu_mentions_by_design(self):
        self.assertIsNotNone(detect_wake("カボスE2Eが入りました", WAKE_WORDS, NEGATIVE))
        self.assertIsNotNone(detect_wake("カボスさんがいらっしゃいました", WAKE_WORDS, NEGATIVE))
        self.assertIsNotNone(detect_wake("テスト用のカボスですね", WAKE_WORDS, NEGATIVE))
        self.assertIsNotNone(detect_wake("カボスですね", WAKE_WORDS, NEGATIVE))


    def test_clean_for_tts_strips_markdown_and_limits_text(self):
        cleaned = clean_for_tts("**結論**: [資料](https://example.com) を見て。 https://x.test/a", 12)
        self.assertEqual(cleaned, "結論: 資料 を見て。")


    def test_clean_for_tts_prefers_sentence_boundary_when_limiting(self):
        cleaned = clean_for_tts("第一です。第二の説明が途中で長く続きます。", 18)
        self.assertEqual(cleaned, "第一です。")


    def test_clean_for_tts_adds_period_to_hard_clip(self):
        cleaned = clean_for_tts("句点なしの長い説明がずっと続いて途中で制限に当たる", 12)
        self.assertTrue(cleaned.endswith("。"))
        self.assertLessEqual(len(cleaned), 13)


    def test_echo_detection_uses_normalized_prefix(self):
        self.assertTrue(
            is_echo_of_bot("現時点では、論点は三つです。", ["現時点では、論点は三つです。第一に範囲です。"])
        )


if __name__ == "__main__":
    unittest.main()
