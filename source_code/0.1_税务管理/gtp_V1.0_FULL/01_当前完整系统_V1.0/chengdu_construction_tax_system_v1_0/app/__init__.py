# Register psycopg string loaders for cross-platform string decoding on Windows
try:
    import psycopg
    from psycopg.adapt import Loader

    class _UniversalStrLoader(Loader):
        def load(self, data):
            if isinstance(data, (bytes, bytearray, memoryview)):
                return bytes(data).decode("utf-8", errors="ignore")
            return str(data)

    for _oid in [19, 25, 705, 1042, 1043, 2275]:
        psycopg.adapters.register_loader(_oid, _UniversalStrLoader)
except Exception:
    pass


from . import models as models  # noqa: F401
from . import v3_party_models as v3_party_models  # noqa: F401
from . import v3_party_conflict_models as v3_party_conflict_models  # noqa: F401
from . import v3_fact_models as v3_fact_models  # noqa: F401
from . import v3_tax_models as v3_tax_models  # noqa: F401
from . import v3_contract_models as v3_contract_models  # noqa: F401
from . import v3_project_tax_models as v3_project_tax_models  # noqa: F401
from . import v3_period_models as v3_period_models  # noqa: F401
from . import v3_vat_ledger_models as v3_vat_ledger_models  # noqa: F401
from . import v3_vat_review_models as v3_vat_review_models  # noqa: F401
from . import v3_project_analysis_models as v3_project_analysis_models  # noqa: F401
from . import v3_group_models as v3_group_models  # noqa: F401
from . import v3_payment_models as v3_payment_models  # noqa: F401
from . import v3_transaction_models as v3_transaction_models  # noqa: F401
from . import v3_cutover_models as v3_cutover_models  # noqa: F401

__all__ = [
    "models",
    "v3_party_models",
    "v3_party_conflict_models",
    "v3_fact_models",
    "v3_tax_models",
    "v3_contract_models",
    "v3_project_tax_models",
    "v3_period_models",
    "v3_vat_ledger_models",
    "v3_vat_review_models",
    "v3_project_analysis_models",
    "v3_group_models",
    "v3_payment_models",
    "v3_transaction_models",
    "v3_cutover_models",
]
