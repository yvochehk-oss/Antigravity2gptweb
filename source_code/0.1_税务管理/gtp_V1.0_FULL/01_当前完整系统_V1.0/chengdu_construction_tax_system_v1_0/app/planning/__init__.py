"""Project allocation planning sandbox.

The deterministic engine is deliberately importable without loading the
SQLAlchemy/PostgreSQL runtime. Database-backed services live in ``service``.
The production planning service installs a batched candidate-profile loader so
candidate count does not translate into N+1 database queries.
"""
from .engine import PartyProfile, PlanningRequest, build_scenarios
from .bulk_profiles import install_bulk_planning_context

install_bulk_planning_context()

__all__ = ["PartyProfile", "PlanningRequest", "build_scenarios"]
