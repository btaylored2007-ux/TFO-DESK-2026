"""Errors for the customer platform. None of these place a live order."""


class PlatformOff(PermissionError):
    """Raised when the store is constructed without an explicit enable."""


class Denied(PermissionError):
    pass


class InvalidAmount(ValueError):
    pass


class ResetBlocked(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class LiveDisabled(Exception):
    def __init__(self, message="live execution is not enabled"):
        super().__init__(message)
        self.code = "live_disabled"


class StalePreview(ResetBlocked):
    def __init__(self):
        super().__init__("stale_preview", "preview no longer matches the paper book")


class IdempotencyConflict(Exception):
    def __init__(self):
        super().__init__("idempotency key was already used for a different request")
        self.code = "idempotency_conflict"


class RailsIntegrity(Exception):
    def __init__(self):
        super().__init__("hard risk rails do not match the sealed hash")
        self.code = "rails_integrity"


class BrainError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ProviderDisabled(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class NewsBlocked(Exception):
    def __init__(self):
        super().__init__("news delivery is not licensed")
        self.code = "news_blocked"


class PaymentRejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Crash(Exception):
    """Injected fault for crash tests. Not used by the HTTP routes."""

    def __init__(self, point):
        super().__init__(point)
        self.point = point
