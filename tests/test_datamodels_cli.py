"""The datamodels CLI and its invariants: stdlib-only core, one-way viz reuse, URLs read by name and masked."""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _dm_helpers import ROOT, cli, run

LAUNCHER = ROOT / "scripts" / "datamodels.py"
PACKAGE = ROOT / "lib" / "datamodels"


def launch(*args, env=None):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


class DoctorTests(unittest.TestCase):
    def test_json(self):
        result = launch("doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([s["engine"] for s in data["sources"]], ["postgres", "sqlite", "mongodb", "redis"])
        sqlite = data["sources"][1]
        self.assertEqual((sqlite["ready"], sqlite["requires"]), (True, []))


class ConnectionTests(unittest.TestCase):
    def test_env_file_yields_one_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OTHER=nope\nexport DATABASE_URL=\"postgres://u:pw@h/db\"\n")
            self.assertEqual(cli.read_env_file(path, "DATABASE_URL"), "postgres://u:pw@h/db")
            self.assertIsNone(cli.read_env_file(path, "MISSING"))

    def test_env_file_expands_references_like_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("POSTGRES_USER=app\nPOSTGRES_PASSWORD='p$w'\n"
                            "DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${DB_HOST:-localhost}"
                            ":$DM_TEST_PORT/${POSTGRES_DB:-shop}  # comment\n"
                            "LITERAL='${POSTGRES_USER}'\n")
            os.environ["DM_TEST_PORT"] = "5433"
            try:
                self.assertEqual(cli.read_env_file(path, "DATABASE_URL"), "postgresql://app:p$w@localhost:5433/shop")
                self.assertEqual(cli.read_env_file(path, "LITERAL"), "${POSTGRES_USER}")
            finally:
                del os.environ["DM_TEST_PORT"]

    def test_redact(self):
        url = "postgresql://app:s3cret-pw@db:5432/shop"
        self.assertEqual(cli.redact(f"failed for {url}", url), "failed for <connection url>")
        self.assertEqual(cli.redact("auth s3cret-pw rejected", url), "auth *** rejected")
        self.assertEqual(cli.redact("dsn redis://u:other@x:1", None), "dsn redis://u:***@x:1")

    def test_missing_variable_names_it_and_exits_2(self):
        code, out = run("introspect", "redis", "--store", "cache", "--url-env", "DM_TEST_UNSET_URL",
                        "--out", "x.json")
        self.assertEqual(code, 2)
        self.assertIn("DM_TEST_UNSET_URL is not set in the environment", out)

    def test_driver_sources_rerun_under_uv(self):
        result = launch("introspect", "redis", "--store", "cache", "--url-env", "DM_TEST_URL", "--out", "x.json",
                        env={"DM_TEST_URL": "redis://127.0.0.1:1/0", "PATH": "/nonexistent"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs uv to fetch its driver", result.stderr)
        self.assertNotIn("127.0.0.1", result.stdout + result.stderr)

    def test_bad_store_id(self):
        code, out = run("introspect", "sqlite", "--store", "App DB", "--path", "x.db", "--out", "x.json")
        self.assertEqual(code, 2)
        self.assertIn("kebab-case", out)


class InvariantTests(unittest.TestCase):
    def test_launcher_refuses_import(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        saved = sys.modules.pop("datamodels", None)
        try:
            sys.path.remove(str(ROOT / "lib"))
            try:
                with self.assertRaises(ImportError):
                    __import__("datamodels")
            finally:
                sys.path.insert(0, str(ROOT / "lib"))
        finally:
            sys.path.remove(str(ROOT / "scripts"))
            sys.modules.pop("datamodels", None)
            if saved is not None:
                sys.modules["datamodels"] = saved

    @unittest.skipIf(sys.version_info < (3, 10), "sys.stdlib_module_names needs Python 3.10")
    def test_module_level_imports_are_stdlib(self):
        """Drivers may only be imported inside a source's functions, where uv provides them."""
        allowed = set(sys.stdlib_module_names) | {"__future__", "datamodels", "viz"}
        for path in [*PACKAGE.rglob("*.py"), LAUNCHER]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{path.relative_to(ROOT)} imports {name}")

    def test_drivers_only_in_sources(self):
        drivers = {"psycopg", "pymongo", "redis", "bson"}
        for path in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import) and any(a.name.split(".")[0] in drivers for a in node.names):
                    self.assertEqual(path.parent.name, "sources", f"{path.relative_to(ROOT)} imports a driver")

    def test_viz_does_not_use_datamodels(self):
        self.assertNotIn("import datamodels", (ROOT / "scripts" / "viz.py").read_text(encoding="utf-8"))
        self.assertNotIn("from datamodels", (ROOT / "scripts" / "viz.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
