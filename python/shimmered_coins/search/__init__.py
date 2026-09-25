"""Search engine.

The step-by-step tree search was removed in favour of the recipe-based design in
docs/ALGORITHM.md (phase 1: LP/MILP over recipes with HiGHS; phase 2: ordering). Only the
integer-plan helpers remain; they are reused by phase 2.
"""

from .quantize import IntegerPlan, quantize, simulate

__all__ = ["IntegerPlan", "quantize", "simulate"]
