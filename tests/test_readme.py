import unittest

from _helpers import viz

INNER = "## Architecture\n\n```mermaid\nflowchart TB\n  c_a[\"A\"]\n```\n\nDetails: [ARCHITECTURE.md](ARCHITECTURE.md)"
README = "# Title\n\nIntro paragraph.\n\n## Install\n\npip install x\n\n## Usage\n\nRun it.\n\n### Flags\n\n-v\n\n## License\n\nMIT\n"


def upsert(text, **kw):
    kw.setdefault("insert", True)
    return viz.upsert_readme(text, INNER, "architecture.json", **kw)


class InsertTests(unittest.TestCase):
    def block_start(self, text):
        return text.index("<!-- visualizer:start")

    def test_after_intro(self):
        new, status = upsert(README, placement="after-intro")
        self.assertEqual(status, "inserted")
        self.assertLess(new.index("Intro paragraph."), self.block_start(new))
        self.assertLess(new.index("<!-- visualizer:end -->"), new.index("## Install"))
        self.assertIn("Intro paragraph.\n\n<!-- visualizer:start", new)
        self.assertIn("<!-- visualizer:end -->\n\n## Install", new)

    def test_end(self):
        new, _ = upsert(README, placement="end")
        self.assertTrue(new.endswith("MIT\n\n" + new[self.block_start(new):]))
        self.assertTrue(new.endswith("<!-- visualizer:end -->\n"))

    def test_before_and_after_heading(self):
        new, _ = upsert(README, placement="before:usage")
        self.assertIn("<!-- visualizer:end -->\n\n## Usage", new)
        new, _ = upsert(README, placement="after:Usage")  # after the whole section, including ### Flags
        self.assertLess(new.index("-v"), self.block_start(new))
        self.assertIn("<!-- visualizer:end -->\n\n## License", new)

    def test_placement_errors(self):
        with self.assertRaisesRegex(viz.ReadmeError, "no heading 'Deploy'"):
            upsert(README, placement="before:Deploy")
        with self.assertRaisesRegex(viz.ReadmeError, "2 headings match"):
            upsert(README + "\n## Usage\n", placement="before:Usage")

    def test_no_trailing_newline(self):
        new, _ = upsert("# T\n\ntext", placement="end")
        self.assertTrue(new.startswith("# T\n\ntext\n\n<!-- visualizer:start"))

    def test_create(self):
        new, status = viz.upsert_readme(None, INNER, "architecture.json", create_title="demo")
        self.assertEqual(status, "created")
        self.assertTrue(new.startswith("# demo\n\n<!-- visualizer:start path=architecture.json hash="))
        self.assertEqual(viz.upsert_readme(None, INNER, "architecture.json"), (None, "no-readme"))

    def test_missing_markers_without_insert(self):
        self.assertEqual(upsert(README, insert=False), (README, "missing"))


class UpdateTests(unittest.TestCase):
    def test_idempotent(self):
        once, _ = upsert(README)
        twice, status = upsert(once)
        self.assertEqual(status, "unchanged")
        self.assertEqual(once, twice)

    def test_update_replaces_only_the_block(self):
        once, _ = upsert(README)
        new_inner = INNER.replace('c_a["A"]', 'c_b["B"]')
        updated, status = viz.upsert_readme(once, new_inner, "architecture.json")
        self.assertEqual(status, "updated")
        self.assertIn('c_b["B"]', updated)
        self.assertNotIn('c_a["A"]', updated)
        self.assertEqual(updated.replace(new_inner, INNER).split("<!-- visualizer:start")[0],
                         once.split("<!-- visualizer:start")[0])

    def test_crlf_is_preserved(self):
        crlf = README.replace("\n", "\r\n")
        once, _ = upsert(crlf)
        self.assertNotIn("\n", once.replace("\r\n", ""))
        twice, status = upsert(once)
        self.assertEqual((twice, status), (once, "unchanged"))

    def test_mixed_line_endings_outside_the_block_are_untouched(self):
        text = "# T\r\n\r\nintro\n\n## A\r\n"
        new, _ = upsert(text)
        self.assertTrue(new.startswith("# T\r\n\r\nintro\n"))
        self.assertTrue(new.endswith("## A\r\n"))

    def test_bom_is_preserved(self):
        once, _ = upsert("﻿" + README)
        self.assertTrue(once.startswith("﻿# Title"))
        self.assertEqual(upsert(once)[1], "unchanged")

    def test_markers_inside_code_fences_are_ignored(self):
        doc = README + "\n```md\n<!-- visualizer:start path=x hash=000000000000 -->\n<!-- visualizer:end -->\n```\n"
        once, status = upsert(doc)
        self.assertEqual(status, "inserted")
        self.assertEqual(once.count("<!-- visualizer:start"), 2)
        self.assertEqual(upsert(once)[1], "unchanged")

    def test_mismatched_markers_raise(self):
        once, _ = upsert(README)
        for broken in (once + "\n<!-- visualizer:end -->\n",
                       once.replace("<!-- visualizer:end -->", ""),
                       "<!-- visualizer:end -->\n" + once.replace("<!-- visualizer:end -->", "")):
            with self.assertRaisesRegex(viz.ReadmeError, "expected one of each"):
                upsert(broken)

    def test_hand_edited_block_raises_unless_forced(self):
        once, _ = upsert(README)
        edited = once.replace("Details:", "More details:")
        with self.assertRaisesRegex(viz.ReadmeError, "edited by hand"):
            upsert(edited)
        fixed, status = upsert(edited, force=True)
        self.assertEqual((fixed, status), (once, "updated"))

    def test_block_that_already_matches_is_fine_even_without_hash(self):
        once, _ = upsert(README)
        no_hash = once.replace(" hash=" + viz.content_hash(INNER), "")
        self.assertEqual(upsert(no_hash), (once, "updated"))


if __name__ == "__main__":
    unittest.main()
