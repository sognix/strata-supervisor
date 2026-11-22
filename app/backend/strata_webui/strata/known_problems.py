"""Problems in Strata that the UI explains in plain words, until Strata fixes them.

Everything here is temporary. Remove an entry (and its form warning in app.js) as soon as Strata fixes the
problem, or as soon as it is shown not to be one (IMPLEMENTATION.md, section 15).

Inkling-2-Mini with two or more NVIDIA cards (Strata 0.2.19): setup.py, install_inkling2(), reads
`g.get("vendor")` from each entry of `multi`, but its caller passes card numbers there (choose_gpus() returns
ints). Found by reading the sources only, not run, so the texts say "may".
"""

from __future__ import annotations

from .model_config import ModelConfig

INKLING_FAMILY = "inkling-2-mini"
INKLING_SETUP_ERROR = "AttributeError: 'int' object has no attribute 'get'"
INKLING_TWO_CARDS = (
    "Strata stopped while setting up Inkling-2-Mini. With two NVIDIA cards there may be a bug in Strata 0.2.19 here "
    "(setup.py, install_inkling2). Try again with one card set to Off. The full error is in the launch output."
)


def cards_in_use(cfg: ModelConfig) -> int | None:
    """How many cards the settings give a role other than off. None when Strata chooses (it may take several)."""
    if not cfg.gpu_roles:
        return None
    return sum(1 for part in cfg.gpu_roles.split(",") if part and not part.endswith("=off"))


def launch_problem(cfg: ModelConfig | None, lines: list[str]) -> str | None:
    """A plain explanation for a failed launch whose output shows a known Strata problem, else None."""
    if cfg is None or cfg.family != INKLING_FAMILY:
        return None
    cards = cards_in_use(cfg)
    if cards is not None and cards < 2:
        return None
    if not any(INKLING_SETUP_ERROR in line for line in lines):
        return None
    return INKLING_TWO_CARDS
