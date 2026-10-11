"""step_confidence: a narrative step inherits the lowest confidence of what it cites (PLAN.md §7)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from authtrace.confidence import LEVELS, step_confidence  # noqa: E402

EXCHANGES = {
    "x1": {"id": "x1", "confidence": "observed"},
    "x2": {"id": "x2", "confidence": "observed"},
    "x3": {"id": "x3", "confidence": "inferred"},
    "x4": {"id": "x4", "confidence": "unknown"},
    "x5": {"id": "x5", "confidence": "decoded"},
    "x6": {"id": "x6", "confidence": "verified"},
}


class StepConfidenceTests(unittest.TestCase):
    def test_all_observed_stays_observed(self):
        self.assertEqual(step_confidence({"exchanges": ["x1", "x2"]}, EXCHANGES), "observed")

    def test_lowest_cited_exchange_wins(self):
        self.assertEqual(step_confidence({"exchanges": ["x1", "x3"]}, EXCHANGES), "inferred")
        self.assertEqual(step_confidence({"exchanges": ["x3", "x4", "x1"]}, EXCHANGES), "unknown")

    def test_code_evidence_alone_is_declared(self):
        self.assertEqual(step_confidence({"code": ["server.py:29"]}, EXCHANGES), "declared")

    def test_code_raises_a_weak_step_to_declared(self):
        self.assertEqual(step_confidence({"exchanges": ["x3"], "code": ["server.py:29"]}, EXCHANGES), "declared")

    def test_code_never_lowers_an_observed_step(self):
        self.assertEqual(step_confidence({"exchanges": ["x1"], "code": ["tools.py:38"]}, EXCHANGES), "observed")

    def test_ranking(self):
        self.assertEqual(step_confidence({"exchanges": ["x6", "x1"]}, EXCHANGES), "observed")
        self.assertEqual(step_confidence({"exchanges": ["x1", "x5"]}, EXCHANGES), "decoded")
        self.assertEqual(step_confidence({"exchanges": ["x6"]}, EXCHANGES), "verified")

    def test_code_never_lowers_a_decoded_step(self):
        self.assertEqual(step_confidence({"exchanges": ["x5"], "code": ["a.py:1"]}, EXCHANGES), "decoded")

    def test_no_evidence_is_inferred(self):
        for step in ({}, {"exchanges": [], "code": []}):
            self.assertEqual(step_confidence(step, EXCHANGES), "inferred", step)

    def test_always_returns_a_level(self):
        for step in ({}, {"exchanges": []}, {"code": []}, {"exchanges": ["x2"]}):
            self.assertIn(step_confidence(step, EXCHANGES), LEVELS, step)


if __name__ == "__main__":
    unittest.main()
