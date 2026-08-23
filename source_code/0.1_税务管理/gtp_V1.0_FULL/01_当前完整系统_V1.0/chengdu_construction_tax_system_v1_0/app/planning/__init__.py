"""Project allocation planning sandbox.

The deterministic engine is deliberately importable without loading the
SQLAlchemy/PostgreSQL runtime. Database-backed services live in ``service``.
"""
from .engine import PartyProfile, PlanningRequest, build_scenarios

__all__ = ["PartyProfile", "PlanningRequest", "build_scenarios"]
