"""Exceptions shared by the client and every tool.

Two kinds matter to a pipeline: an error that spoils one decision, and an error that means nothing
will work until the user fixes something. Tools fail open on the first (the line passes through
unjudged) and stop on the second.
"""

from __future__ import annotations


class JevError(Exception):
    """One request failed (timeout, 5xx, malformed answer). The rest of the run can continue."""


class JevFatal(JevError):
    """Nothing will work until the user acts: bad key, no credits, unknown backend, budget spent."""


class AuthError(JevFatal):
    """No usable API key, or the API rejected the one it was given."""


class BudgetExceeded(JevFatal):
    """The run reached its dollar budget and stopped."""


class UsageError(Exception):
    """The command line does not make sense. Exit status 2, like every coreutil."""
