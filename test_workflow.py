import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine

CHANNEL = "UC" + "A" * 22
OLD = "A" * 11
NEW = "B" * 11


class ArchiveWorkflow(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = engine.Archive(Path(self.directory.name))

    def add(self, future=False):
        with patch("engine.flat", return_value={"channel_id": CHANNEL, "title": "Example"}):
            return self.archive.add("@example", future)

    def scan(self, *ids):
        def tab(url):
            return {"entries": [{"id": ident, "title": ident} for ident in ids]} if url.endswith("/videos") else {}
        with patch("engine.flat", side_effect=tab):
            self.archive.scan(CHANNEL)

    def states(self):
        return {v["id"]: v["state"] for v in self.archive.videos(CHANNEL)}

    def test_interrupted_baseline_and_new_upload(self):
        self.add(True)
        with patch("engine.flat", side_effect=[{"entries": [{"id": OLD}]}, RuntimeError("Offline")]):
            with self.assertRaises(RuntimeError):
                self.archive.scan(CHANNEL)
        self.assertEqual(self.states(), {})
        self.scan(OLD)
        self.scan(OLD, NEW)
        self.assertEqual(self.states(), {OLD: "Baseline", NEW: "Queued"})

    def test_review_preserves_download_and_detects_missing(self):
        self.archive.save({"mode": "Review"})
        self.add()
        self.scan(OLD)
        self.assertEqual(self.states(), {OLD: "Pending"})
        self.archive.queue([OLD])
        target = Path(self.directory.name) / "media.mp4"
        target.write_bytes(b"mock media")
        with patch.object(self.archive, "download", return_value=(target, {})):
            self.assertTrue(self.archive.next_download())
        self.scan()
        self.assertEqual(self.states(), {OLD: "Archived"})
        self.archive.remove(CHANNEL)
        self.assertTrue(target.exists())
        self.add()
        self.assertEqual(len(self.archive.creators()), 1)
        target.unlink()
        self.archive.verify()
        self.assertEqual(self.states(), {OLD: "Missing"})

    def test_categories_deduplicate(self):
        self.add()
        with patch("engine.flat", return_value={"entries": [{"id": OLD}]}):
            self.archive.scan(CHANNEL)
        videos = self.archive.videos(CHANNEL)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["category"], "Shorts")

    def test_url_validation(self):
        self.assertEqual(engine.channel_url("@example"), "https://www.youtube.com/@example")
        for value in ("https://youtube.com@other.example/@a", "https://youtu.be/example",
                      "https://youtube.com/watch?v=example"):
            with self.assertRaises(ValueError):
                engine.channel_url(value)


if __name__ == "__main__":
    unittest.main()
