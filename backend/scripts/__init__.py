"""Standalone operational scripts.

These modules are entry points run on demand (e.g. via
`docker exec neuthek-backend python -m backend.scripts.<name>`). They are
intentionally NOT imported by the API process or the worker loop — keep it
that way so importing the package has no side effects on the live app.
"""
