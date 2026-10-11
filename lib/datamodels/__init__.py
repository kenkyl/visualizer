"""datamodels: the deterministic half of the /visualizer:data-models skill.

The sources read a store's metadata (never its values) into a snapshot. `merge` folds
snapshots into data-models.json; Claude annotates it; `check` validates it and `render`
writes DATA_MODELS.md and the README block. Stdlib only, Python 3.9+. Database drivers
are imported only inside lib/datamodels/sources/, under `uv run --with`.
"""

__version__ = "0.1.0"
