"""authtrace's CLI skeleton, and the rule that its core is stdlib-only."""

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "authtrace.py"
PACKAGE = ROOT / "lib" / "authtrace"


def run(*args):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True, text=True)


class DoctorTests(unittest.TestCase):
    def test_json(self):
        result = run("doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"version", "environment", "modes"})
        self.assertEqual(set(data["environment"]),
                         {"python", "python_ok", "uv", "chrome", "playwright_chromium"})
        self.assertEqual([m["id"] for m in data["modes"]], ["render", "core", "hooks", "browser", "proxy", "live"])
        for mode in data["modes"]:
            self.assertEqual(set(mode), {"id", "name", "milestone", "built", "missing", "note"})

    def test_text(self):
        result = run("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("authtrace", result.stdout)
        self.assertIn("ready (M2)", result.stdout)
        self.assertIn("planned (M5)", result.stdout)


class PlannedCommandTests(unittest.TestCase):
    def test_planned_commands_exit_2_and_name_their_milestone(self):
        for args, milestone in ((["live"], "M6"),):
            result = run(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(milestone, result.stderr)
            self.assertIn("PLAN.md", result.stderr)

    def test_launcher_refuses_import(self):
        # Other test modules import the real package from lib/; set it aside so this import
        # resolves against scripts/ and hits the launcher's guard.
        saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "authtrace" or k.startswith("authtrace.")}
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            with self.assertRaises(ImportError):
                __import__("authtrace")
        finally:
            sys.path.remove(str(ROOT / "scripts"))
            sys.modules.pop("authtrace", None)
            sys.modules.update(saved)


class StdlibOnlyTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 10), "sys.stdlib_module_names needs Python 3.10")
    def test_core_imports_only_stdlib(self):
        # viz is the repo's own stdlib-only script; authtrace borrows its secret patterns (_viz.py).
        allowed = set(sys.stdlib_module_names) | {"__future__", "authtrace", "viz"}
        for path in [*PACKAGE.rglob("*.py"), LAUNCHER]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{path.relative_to(ROOT)} imports {name}")

    def test_viz_does_not_use_authtrace(self):
        self.assertNotIn("authtrace", (ROOT / "scripts" / "viz.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
