"""Tax application package.

Import the legacy and V3 model registries once so every consumer of
``app.db.Base`` (Alembic, schema audit, tests) sees one complete metadata graph.
"""

from . import models as models  # noqa: F401
from . import v3_party_models as v3_party_models  # noqa: F401
from . import v3_party_conflict_models as v3_party_conflict_models  # noqa: F401
from . import v3_fact_models as v3_fact_models  # noqa: F401

__all__ = [
    "models",
    "v3_party_models",
    "v3_party_conflict_models",
    "v3_fact_models",
]
