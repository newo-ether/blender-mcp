"""Stable Blender Extension error contracts."""


class BlenderMCPAddonError(RuntimeError):
    """Bridge error with a stable public category."""

    def __init__(self, code, message, retryable=False, details=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}
