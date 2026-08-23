"""Utilities for running Alembic against copied Tax migration trees.

``alembic.env`` temporarily puts the migration tree on ``sys.path`` so it can
import the application metadata.  A migration command can therefore leave a
temporary ``app`` package in ``sys.modules`` and its path in ``sys.path``.
That is harmless in production, where one tree is used by one process, but it
can make isolated migration tests accidentally execute against a previous
temporary tree.  Keep the command boundary explicit and restore both pieces
of interpreter state after every command, including failed commands.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

T = TypeVar("T")


def _dispose_new_app_engines(
    original_app_modules: dict[str, object],
) -> None:
    """Dispose engines owned by application modules imported in this scope.

    A migration test may need to import ``app.models`` after an Alembic call
    in order to prove that ORM writes work against the temporary database.
    ``app.db`` creates a module-global engine at import time, and leaving that
    engine alive means the next test can retain a connection to the temporary
    copy.  Only engines from modules that did not exist at scope entry are
    disposed; a caller's already-active application engine is never touched.
    """

    original_engines = {
        id(getattr(module, attribute))
        for module in original_app_modules.values()
        for attribute in ("engine", "async_engine")
        if getattr(module, attribute, None) is not None
    }
    disposed: set[int] = set()
    for name, module in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        for attribute in ("engine", "async_engine"):
            engine = getattr(module, attribute, None)
            if engine is None or id(engine) in original_engines or id(engine) in disposed:
                continue
            dispose = getattr(engine, "dispose", None)
            if callable(dispose):
                dispose()
                disposed.add(id(engine))


@contextmanager
def isolated_import_state() -> Iterator[None]:
    """Restore Tax ``app`` imports and ``sys.path`` after a migration call."""

    original_path = list(sys.path)
    original_app_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    try:
        yield
    finally:
        # Dispose module-global SQLAlchemy engines before removing the module
        # objects.  This closes SQLite handles to temporary migration copies
        # and prevents a later session-scoped fixture from using that DB.
        _dispose_new_app_engines(original_app_modules)
        sys.path[:] = original_path
        for name in list(sys.modules):
            if (
                (name == "app" or name.startswith("app."))
                and name not in original_app_modules
            ):
                del sys.modules[name]
        sys.modules.update(original_app_modules)


def alembic_call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call an Alembic command without leaking a copied application package."""

    with isolated_import_state():
        return function(*args, **kwargs)
