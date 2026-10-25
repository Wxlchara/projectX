"""Package 6: deterministic checks of confirmed, case-scoped document facts."""

from .engine import ALGORITHM_VERSION, DocumentChecker, check_case

__all__ = ["ALGORITHM_VERSION", "DocumentChecker", "check_case"]
