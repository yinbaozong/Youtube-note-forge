import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_transcript import load_browser_transcript, reprobe_subtitle_choice, RunResult
from video_common import PipelineError


class BrowserTranscriptTests(unittest.TestCase):
    def test_video_binding_and_completeness(self):
        payload = {"status": "ok", "video_id": "P2zRQ3BUu30", "language": "en",
                   "metadata": {"id": "P2zRQ3BUu30", "duration": 100, "title": "Test"},
                   "entries": [{"start": 0, "text": "Start"}, {"start": 95, "text": "End"}]}
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "captions.json"
            source.write_text(json.dumps(payload))
            metadata, entries, kind, language = load_browser_transcript(str(source), "https://www.youtube.com/watch?v=P2zRQ3BUu30")
            self.assertEqual(len(entries), 2)
            self.assertEqual(kind, "subtitle:browser")
            with self.assertRaises(PipelineError):
                load_browser_transcript(str(source), "https://www.youtube.com/watch?v=wrong")
            payload["entries"] = payload["entries"][:1]
            source.write_text(json.dumps(payload))
            with self.assertRaisesRegex(PipelineError, "不完整"):
                load_browser_transcript(str(source), "https://www.youtube.com/watch?v=P2zRQ3BUu30")

    def test_failed_probe_is_not_absent_subtitles(self):
        class Runner:
            def run(self, args, **kwargs):
                return RunResult(args, 1, "", "ERROR: HTTP 429", "")
        with self.assertRaisesRegex(PipelineError, "429"):
            reprobe_subtitle_choice(Runner(), "https://www.youtube.com/watch?v=P2zRQ3BUu30", "en")

    def test_duplicate_panels_are_sorted_and_deduplicated_without_losing_words(self):
        rows = [{"start": 0, "text": "Opening"}, {"start": 95, "text": "End"}]
        payload = {"status": "ok", "video_id": "video", "metadata": {"id": "video", "duration": 100},
                   "entries": rows + rows + [{"start": 95, "text": "Another sentence"}]}
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "captions.json"
            source.write_text(json.dumps(payload))
            _, entries, _, _ = load_browser_transcript(str(source), "https://www.youtube.com/watch?v=video")
        self.assertEqual([(e.start, e.text) for e in entries], [(0, "Opening"), (95, "End"), (95, "Another sentence")])
