"""Cross-cutting error markers. Services raise their own exception types; mixing in NotFound lets callers
(the API) answer "not found" the same way everywhere, and the same for missing and other-tenant resources."""


class NotFound(Exception):
    """List it first among a class's bases, e.g. CaseNotFound(NotFound, CaseError): handlers resolve by MRO."""
