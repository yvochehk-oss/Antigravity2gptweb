"""Install the shared deterministic management EAC formula.

Revision ID: 66_canonical_eac_function
Revises: 65_ext_party_code_kind_len

Tax and ProjectRAG share one PostgreSQL database in the supported deployment.
The function below is deliberately owned by the Tax migration chain (which is
run first) so every downstream reader uses the same runtime formula instead of
copying EAC arithmetic in Python and SQL.
"""
from __future__ import annotations

from alembic import op

revision = "66_canonical_eac_function"
down_revision = "65_ext_party_code_kind_len"
branch_labels = None
depends_on = None

_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION canonical_management_eac_cost(
    p_contract_amount numeric,
    p_recognized_revenue numeric,
    p_actual_cost numeric
)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN p_contract_amount > 0
         AND p_recognized_revenue > 0
         AND p_actual_cost IS NOT NULL
        THEN GREATEST(
            p_actual_cost,
            p_actual_cost / (p_recognized_revenue / p_contract_amount)
        )
        ELSE NULL
    END
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute(_FUNCTION_SQL)


def downgrade() -> None:
    # ProjectRAG analytics views may depend on this shared function. Dropping
    # it from the Tax chain alone would invalidate those views, so a downgrade
    # intentionally leaves the immutable compatibility function in place.
    pass
