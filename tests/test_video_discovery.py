import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.routes import scan_all_sources
from app.config import DEFAULT_SOURCES, PROJECT_ROOT


class VideoDiscoveryTests(unittest.TestCase):
    def test_default_source_is_the_recursive_input_root(self):
        expected = PROJECT_ROOT / "downloads" / "input"
        self.assertEqual(DEFAULT_SOURCES, [str(expected)])

    def test_nested_video_folder_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            video_dir = source / "new-course" / "lesson-01"
            video_dir.mkdir(parents=True)
            (video_dir / "Lesson.mp4").touch()
            (video_dir / "Lesson.json").write_text("{}", encoding="utf-8")

            with patch("app.api.routes.load_sources", return_value=[str(source)]):
                videos = scan_all_sources()

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["name"], "Lesson")
        self.assertTrue(videos[0]["has_json"])


if __name__ == "__main__":
    unittest.main()
