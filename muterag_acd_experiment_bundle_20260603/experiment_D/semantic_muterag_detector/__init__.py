"""Standalone semantic MutedRAG detector.

This package is intentionally separate from the legacy detector package.
It scores query-conditioned semantic structure rather than keyword patterns.
"""

from .classifier import classify_features
from .config import DetectorConfig, Thresholds
from .features import score_chunk

__all__ = [
    "DetectorConfig",
    "Thresholds",
    "classify_features",
    "score_chunk",
]
