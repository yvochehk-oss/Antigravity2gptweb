"""RAG service package.

Importing the package installs the PostgreSQL indexed lexical retrieval path.
The exported ``retrieval`` name preserves existing router imports.
"""
from . import indexed_retrieval as retrieval

__all__ = ["retrieval"]
