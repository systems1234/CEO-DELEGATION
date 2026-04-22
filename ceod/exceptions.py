from __future__ import annotations


class CeodError(Exception):
  """Base exception for the Python CEOD service."""


class ConfigurationError(CeodError):
  """Raised when required configuration is missing or invalid."""


class ExternalServiceError(CeodError):
  """Raised when an upstream service call fails."""


class ValidationError(CeodError):
  """Raised when input data is invalid."""


class NotFoundError(CeodError):
  """Raised when a required entity cannot be found."""

