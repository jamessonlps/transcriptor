"""Application services — orchestration of domain logic.

Services are FastAPI-agnostic on purpose: they take plain Python args and
return plain Python results, so they can be unit-tested without spinning up
the HTTP layer.
"""
