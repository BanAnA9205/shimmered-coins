"""Search engine: recipes over the item x move table (docs/ALGORITHM.md).

Phase 1 finds and ranks atomic recipes with LPs (HiGHS); phase 2 orders each recipe into a
tour of places and scores it by growth per trip. ``quantize.py`` holds the integer replay.
"""

from .engine import Move, Result, SearchParams, SearchReport, SearchStats, search
from .quantize import IntegerPlan, quantize, simulate

__all__ = [
    "IntegerPlan",
    "Move",
    "Result",
    "SearchParams",
    "SearchReport",
    "SearchStats",
    "quantize",
    "search",
    "simulate",
]
