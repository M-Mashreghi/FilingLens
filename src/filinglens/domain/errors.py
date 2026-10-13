"""Domain-level exceptions."""

from __future__ import annotations


class FilingLensError(Exception):
    """Base class for FilingLens domain errors."""


class NotFoundError(FilingLensError):
    pass


class ConflictError(FilingLensError):
    """An identity already exists with different, incompatible content."""


class ImmutabilityError(FilingLensError):
    """Attempted mutation of an immutable record (snapshot, completed run, ...)."""


class InvalidTransitionError(FilingLensError):
    pass


class AmbiguityError(FilingLensError):
    pass
