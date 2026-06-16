"""Shared exceptions."""


class JobCancelled(Exception):
    """Raised when a background job is cancelled by the user."""


class IncompleteDatasetError(Exception):
    """Raised when import is blocked because the dataset has gaps."""
