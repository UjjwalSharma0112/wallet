import functools

from apps.auth import services as auth_service

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
