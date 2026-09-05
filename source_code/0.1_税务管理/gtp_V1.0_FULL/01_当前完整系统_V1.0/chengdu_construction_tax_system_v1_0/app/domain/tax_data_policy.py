"""Authoritative Tax data-boundary policy.

Tax never establishes business truth from source files. RAG PostgreSQL is the
only business/tax fact source consumed by Tax. Tax deterministic engines may
derive advisory values, but advisory values never overwrite RAG facts.
"""
from __future__ import annotations

DATA_CLASS_FACT = "FACT"
DATA_CLASS_ADVISORY = "ADVISORY"
DATA_CLASS_FORMAL_STATUTORY = "FORMAL_STATUTORY"

FACT_SOURCE_RAG_POSTGRESQL = "RAG_POSTGRESQL"
ADVISORY_SOURCE_TAX_ENGINE = "TAX_ENGINE"
FORMAL_SOURCE_TAX_STATUTORY = "TAX_FORMAL_STATUTORY"


def fact_metadata() -> dict[str, object]:
    return {
        "data_class": DATA_CLASS_FACT,
        "source": FACT_SOURCE_RAG_POSTGRESQL,
        "actual_occurred": True,
        "is_filing_basis": False,
    }


def advisory_metadata() -> dict[str, object]:
    return {
        "data_class": DATA_CLASS_ADVISORY,
        "source": ADVISORY_SOURCE_TAX_ENGINE,
        "actual_occurred": False,
        "is_filing_basis": False,
    }


__all__ = [
    "DATA_CLASS_FACT",
    "DATA_CLASS_ADVISORY",
    "DATA_CLASS_FORMAL_STATUTORY",
    "FACT_SOURCE_RAG_POSTGRESQL",
    "ADVISORY_SOURCE_TAX_ENGINE",
    "FORMAL_SOURCE_TAX_STATUTORY",
    "fact_metadata",
    "advisory_metadata",
]
