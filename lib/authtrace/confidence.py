"""Confidence levels, and how a narrative step inherits one from the facts it cites.

PLAN.md §7: every fact carries a level. A narrative step (Claude's prose) has no level of its own:
it inherits the lowest level of the exchanges it cites, unless it cites code evidence, which can
raise it to `declared`. Confidence never rises without evidence.
"""

from __future__ import annotations

LEVELS = ("observed", "decoded", "verified", "declared", "inferred", "unknown")

# Strongest first. `verified` beats `observed` because a checked signature proves who minted a
# value, not just that it crossed the wire. `decoded` sits below `observed`: the bytes were seen,
# but what they claim (a JWT's `sub`, `aud`) is unchecked and could be forged. `declared` is config
# or code saying what should happen, which is weaker than seeing it happen.
RANK = ("verified", "observed", "decoded", "declared", "inferred", "unknown")
assert set(RANK) == set(LEVELS)


def weakest(levels) -> str:
    return max(levels, key=RANK.index)


def step_confidence(step: dict, exchanges: dict) -> str:
    """The confidence a narrative step has earned from its evidence.

    step       a narrative step: `exchanges` (ids it cites) and `code` (file:line evidence),
               either of which may be missing or empty
    exchanges  every exchange in the trace, by id; each has a `confidence` from LEVELS

    A step that cites nothing is Claude's reasoning alone, so it's `inferred`, not `unknown`:
    `unknown` marks an explicit gap in the facts, not prose without a citation.
    """
    cited = [exchanges[xid]["confidence"] for xid in step.get("exchanges") or []]
    level = weakest(cited) if cited else "inferred"
    if step.get("code") and RANK.index(level) > RANK.index("declared"):
        level = "declared"
    return level
