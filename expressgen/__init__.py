"""Public package interface for ExpressGen."""

from .generator import ExpressGenerator, GenerationResult, load_schema
from .prisma import SchemaError

__all__ = [
    "ExpressGenerator",
    "GenerationResult",
    "SchemaError",
    "load_schema",
]

__version__ = "0.1.0"
