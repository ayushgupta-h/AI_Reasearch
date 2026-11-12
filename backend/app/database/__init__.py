"""
app/database
============
Persistence boundary.

SQLite access is owned by the existing :class:`session_manager.SessionManager`
(parameterised SQL, schema migration and WAL-friendly connection handling are
already implemented there). It is injected into
:class:`app.services.session_service.SessionService`, so routes never open a
database connection themselves.

A future PostgreSQL/Redis backend can implement the same service interface
without touching the API layer.
"""

__all__: list[str] = []
