"""Eightfold canonical candidate profile transformer.

Public API: build canonical profiles and project them through a runtime config.
"""

from .models import CanonicalProfile, FieldSpec, OutputConfig
from .pipeline import build_canonical, run

__version__ = "0.1.0"
__all__ = ["run", "build_canonical", "CanonicalProfile", "OutputConfig", "FieldSpec", "__version__"]
