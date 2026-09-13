"""Shared Pydantic DTOs and event schemas (Section 4, Section 18.1).

Contract: every cross-service payload and every broker event body is defined
here once, versioned, and imported by both producer and consumer. Nothing in
this package imports from a service.
"""
