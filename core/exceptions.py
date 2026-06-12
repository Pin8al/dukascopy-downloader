"""Shared exceptions."""


class JobCancelled(Exception):
    """Raised when a background job is cancelled by the user."""


class IncompleteDatasetError(Exception):
    """Raised when export is blocked because the dataset has gaps."""
