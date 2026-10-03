import functools

from apps.auth import services as auth_service
from apps.auth.constants import ROLES, ROLE_NOT_ALLOWED_CODE, ROLE_NOT_ALLOWED_MESSAGE

from .errors import AppError

BEARER_PREFIX = "Bearer "
AUTH_REQUIRED_MESSAGE = "Sign in to your wallet to continue."


def read_token(request):
    authorization = request.headers.get("Authorization", "")

    if not authorization.startswith(BEARER_PREFIX):
        raise AppError(401, "AUTH_REQUIRED", AUTH_REQUIRED_MESSAGE)

    token = authorization[len(BEARER_PREFIX) :].strip()

    if not token:
        raise AppError(401, "AUTH_REQUIRED", AUTH_REQUIRED_MESSAGE)

    return token


def require_auth(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        request.account, request.auth_payload = auth_service.authenticate(read_token(request))

        return view(request, *args, **kwargs)

    return wrapper


def require_role(*allowed_roles):
    """Refuse a request whose account does not hold one of the given roles.

    A role is a fact about the caller rather than about the thing being acted on, so it is
    checked here beside authentication and above the view, where it applies to every call the
    route makes. Ownership is the other half of authorization and is a fact about the
    resource, so it stays in the service that owns it and is not affected by anything here.

    `require_auth` is applied outside this decorator, so an unauthenticated caller is told to
    sign in and is never told whether a role would have been allowed:

        @require_auth
        @require_role(ADMIN_ROLE)
        def view(request): ...

    A role this application does not recognise is refused as surely as a recognised role that
    was not asked for. Treating it as the least role would turn a mistake in stored data into
    permission granted.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            role = auth_service.account_role(request.account)

            if role not in ROLES or role not in allowed_roles:
                raise AppError(403, ROLE_NOT_ALLOWED_CODE, ROLE_NOT_ALLOWED_MESSAGE)

            return view(request, *args, **kwargs)

        return wrapper

    return decorator