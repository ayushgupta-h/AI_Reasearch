"""
app/repositories
================
Persistence adapters for the application's domain resources.

Repositories own SQL and translate rows into plain dictionaries. Services call
them; routes never see a database connection. This keeps the storage engine
replaceable (the only requirement is the ``Database`` port from
:mod:`app.infrastructure.ports`).
"""

from app.repositories.projects import ProjectRepository

__all__ = ["ProjectRepository"]
