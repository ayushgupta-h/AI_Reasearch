"""Service layer: business logic between HTTP routes and infrastructure.

Consumers import concrete modules (``app.services.container``) so import graphs
stay acyclic and application startup stays fast.
"""
