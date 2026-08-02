import json
import tempfile
import unittest
from pathlib import Path

from app.api.routes import resegment_sentences


class ResegmentSentencesTests(unittest.TestCase):
    def run_resegment(self, data, text=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "transcript.json"
            json_path.write_text(json.dumps(data), encoding="utf-8")
            txt_path = None
            if text is not None:
                txt_path = root / "transcript.txt"
                txt_path.write_text(text, encoding="utf-8")
            return resegment_sentences(json_path, txt_path)

    def test_word_timestamps_define_exact_sentence_boundaries(self):
        data = {
            "words": [
                {"text": "Hello", "start": 0.10, "end": 0.40},
                {"text": "world.", "start": 0.42, "end": 0.80},
                {"text": "Next", "start": 1.00, "end": 1.25},
                {"text": "sentence.", "start": 1.27, "end": 1.80},
            ]
        }

        result = self.run_resegment(data, "Hello world. Next sentence.")

        self.assertEqual(len(result), 2)
        self.assertEqual((result[0]["start"], result[0]["end"]), (0.1, 0.8))
        self.assertEqual((result[1]["start"], result[1]["end"]), (1.0, 1.8))
        self.assertEqual(result[0]["boundary_source"], "word_timestamps")

    def test_segment_timestamps_interpolate_shared_sentence_boundary(self):
        data = {
            "transcription": [
                {
                    "text": "First sentence. Second sentence.",
                    "offsets": {"from": 0, "to": 3000},
                },
                {
                    "text": "Third sentence.",
                    "offsets": {"from": 3000, "to": 4500},
                },
            ]
        }

        result = self.run_resegment(
            data, "First sentence. Second sentence. Third sentence."
        )

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["text"], "First sentence.")
        self.assertEqual(result[1]["text"], "Second sentence.")
        self.assertEqual(result[0]["start"], 0.0)
        self.assertEqual(result[0]["end"], result[1]["start"])
        self.assertEqual(result[1]["end"], 3.0)
        self.assertEqual(result[0]["boundary_source"], "interpolated_segment")
        self.assertEqual(result[1]["boundary_source"], "interpolated_segment")
        self.assertEqual(result[2]["boundary_source"], "segment_timestamps")

    def test_chain_shared_segments_remain_separate_sentences(self):
        data = {
            "transcription": [
                {"text": "First sentence ends. Second", "offsets": {"from": 0, "to": 2000}},
                {"text": "sentence ends. Third", "offsets": {"from": 2000, "to": 4000}},
                {"text": "sentence ends. Fourth", "offsets": {"from": 4000, "to": 6000}},
                {"text": "sentence ends.", "offsets": {"from": 6000, "to": 8000}},
            ]
        }
        text = (
            "First sentence ends. Second sentence ends. "
            "Third sentence ends. Fourth sentence ends."
        )

        result = self.run_resegment(data, text)

        self.assertEqual(len(result), 4)
        self.assertEqual([item["text"] for item in result], [
            "First sentence ends.",
            "Second sentence ends.",
            "Third sentence ends.",
            "Fourth sentence ends.",
        ])
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(result, result[1:])))
        self.assertTrue(all(item["end"] - item["start"] < 3 for item in result))

    def test_unrelated_txt_falls_back_to_json_text(self):
        data = {
            "transcription": [
                {
                    "text": "The audio source is authoritative.",
                    "offsets": {"from": 1000, "to": 4000},
                }
            ]
        }

        result = self.run_resegment(data, "Completely unrelated replacement words.")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "The audio source is authoritative.")
        self.assertEqual((result[0]["start"], result[0]["end"]), (1.0, 4.0))

    def test_impossible_speech_rate_is_rejected_and_later_text_recovers(self):
        data = {
            "transcription": [
                {
                    "text": "This sentence has far too many words for its tiny duration.",
                    "offsets": {"from": 0, "to": 100},
                },
                {
                    "text": "Normal timing recovers correctly.",
                    "offsets": {"from": 1000, "to": 3000},
                },
            ]
        }
        text = (
            "This sentence has far too many words for its tiny duration. "
            "Normal timing recovers correctly."
        )

        result = self.run_resegment(data, text)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Normal timing recovers correctly.")
        self.assertEqual((result[0]["start"], result[0]["end"]), (1.0, 3.0))

    def test_repeated_words_remain_in_order(self):
        data = {
            "transcription": [
                {
                    "text": "Go go go, then stop.",
                    "offsets": {"from": 0, "to": 2000},
                },
                {
                    "text": "Go again.",
                    "offsets": {"from": 2000, "to": 3000},
                },
            ]
        }

        result = self.run_resegment(data, "Go go go, then stop. Go again.")

        self.assertEqual([item["text"] for item in result], [
            "Go go go, then stop.",
            "Go again.",
        ])
        self.assertEqual(result[1]["start"], 2.0)

    def test_sentence_split_preserves_closing_quotes(self):
        data = {
            "words": [
                {"text": 'He', "start": 0.0, "end": 0.2},
                {"text": 'said', "start": 0.2, "end": 0.4},
                {"text": '"hello."', "start": 0.4, "end": 0.9},
                {"text": "Then", "start": 1.0, "end": 1.2},
                {"text": "left.", "start": 1.2, "end": 1.6},
            ]
        }

        result = self.run_resegment(data, 'He said "hello." Then left.')

        self.assertEqual([item["text"] for item in result], [
            'He said "hello."',
            "Then left.",
        ])


if __name__ == "__main__":
    unittest.main()
