"""Enrichers add Setup / Usage / Tools metadata to existing discovery candidates.

Each enricher takes a list of candidates and returns the same list with one or
more fields populated (``docs``, ``tools``, ``skills``, ``openapi_url``).
Enrichers are intended to run *after* discovery sources have produced rows, on
a recurring upkeep schedule. They never invent new candidates.
"""
