"""Database metadata boundary for AI Review.

The merged application owns the ``projects`` table in ``app.db.Base``.  AI
Review imports that canonical registry directly so its tables always resolve
foreign keys against the application tables.  ``app.db`` keeps the registry
stable across the test harness' intentional ``app.*`` module reloads; this
module must not create a second fallback registry because doing so silently
reintroduces the dual-Base failure this boundary is meant to prevent.
"""

from app.db import Base
