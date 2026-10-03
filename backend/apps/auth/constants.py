"""The vocabulary of an account's role: what roles exist and who may hold one.

A role is a property of the account rather than of anything it owns, so it is declared here
where the values are defined, and read from the account on every authenticated request rather
than copied into the token that carried the request.
"""

USER_ROLE = "USER"

# The product declares three roles. MERCHANT is a real role rather than a spare name, but no
# requirement states what it may do that a USER may not, so it is given no permissions of its
# own until one is defined for it. That is a deliberate absence: inventing a merchant rule
# would be guessing at how money may move.
MERCHANT_ROLE = "MERCHANT"

# ADMIN is an operational role over the wallet as a system, not over anybody's balance. No
# administrative operation exists yet, so ADMIN currently guards nothing; the guard exists and
# is enforced so that the operation it is meant for has a boundary to attach to when it comes.
ADMIN_ROLE = "ADMIN"

ROLES = [USER_ROLE, MERCHANT_ROLE, ADMIN_ROLE]

# A caller whose role is not one of these is refused everywhere rather than treated as the
# least of them. An account holding a role this application does not recognise is a state the
# application cannot reason about, and assuming it is ordinary would be a way for a mistake in
# stored data to read as permission.
ROLE_NOT_ALLOWED_CODE = "ROLE_NOT_ALLOWED"

# Said without saying which role was held or which was required, so a refusal reveals nothing
# about the account it refused.
ROLE_NOT_ALLOWED_MESSAGE = "Your account is not permitted to perform this operation."