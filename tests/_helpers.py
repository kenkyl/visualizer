"""Shared test setup: import scripts/viz.py and build small models."""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import viz  # noqa: E402

EXAMPLE = ROOT / "skills" / "architecture" / "examples" / "arcade-test.architecture.json"
GOLDEN = ROOT / "tests" / "fixtures" / "golden" / "arcade-test"


def example_model() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def minimal_model(**overrides) -> dict:
    """A small valid model: a CLI calling an API and a database, in two boundaries."""
    model = {
        "meta": {"generator": "visualizer", "schema_version": 1, "model_sha": None, "model_date": None},
        "system": {"name": "demo", "summary": "A demo system."},
        "boundaries": [{"id": "local", "name": "Laptop", "kind": "local"},
                       {"id": "cloud", "name": "Cloud", "kind": "cloud"}],
        "components": [
            {"id": "cli", "name": "CLI", "kind": "entrypoint", "boundary": "local",
             "responsibility": "Runs commands.", "evidence": ["cli.py"]},
            {"id": "api", "name": "API", "kind": "service", "boundary": "cloud",
             "responsibility": "Serves requests.", "evidence": ["api/"]},
            {"id": "db", "name": "Database", "kind": "datastore", "boundary": "cloud",
             "responsibility": "Stores rows.", "evidence": ["doc:README.md#storage"],
             "holds": [{"data": "Rows", "lifetime": "Forever"}]},
        ],
        "edges": [
            {"from": "cli", "to": "api", "label": "calls", "kind": "sync", "step": 1},
            {"from": "api", "to": "db", "label": "reads and writes", "kind": "sync", "step": 2},
        ],
    }
    model = copy.deepcopy(model)
    model.update(overrides)
    return model


def check(model, root=None):
    rep = viz.Report()
    viz.validate(model, rep)
    if root is not None and rep.ok:
        viz.check_repo(model, Path(root), rep)
    return rep


def component(model, cid):
    return next(c for c in model["components"] if c["id"] == cid)
