"""Shared pytest fixtures: testcontainers, factories, demo principals (Section 25).

Contract: reusable Postgres/Redis/NATS containers and the token factories used
by the same-tenant / cross-tenant-404 / no-permission-403 authorization triplet
every protected endpoint must be tested against.
"""
