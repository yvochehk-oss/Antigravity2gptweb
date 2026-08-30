"""Tax application package.

Import the legacy and V3 model registries once so every consumer of
``app.db.Base`` (Alembic, schema audit, tests) sees one complete metadata graph.
"""

from . import models as models  # noqa: F401
from . import v3_party_models as v3_party_models  # noqa: F401
from . import v3_party_conflict_models as v3_party_conflict_models  # noqa: F401
from . import v3_fact_models as v3_fact_models  # noqa: F401
from . import v3_tax_models as v3_tax_models  # noqa: F401
from . import v3_contract_models as v3_contract_models  # noqa: F401
from . import v3_project_tax_models as v3_project_tax_models  # noqa: F401
from . import v3_period_models as v3_period_models  # noqa: F401

__all__ = [
    "models",
    "v3_party_models",
    "v3_party_conflict_models",
    "v3_fact_models",
    "v3_tax_models",
    "v3_contract_models",
    "v3_project_tax_models",
    "v3_period_models",
]
