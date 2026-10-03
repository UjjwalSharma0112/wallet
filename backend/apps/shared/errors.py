from rest_framework.response import Response
from rest_framework.views import exception_handler

INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."
NOT_FOUND_MESSAGE = "The requested resource was not found."

# DRF raises its own exceptions for cases this application does not model itself, such
# as an unsupported method on a known route. Every failure must still use the same
# envelope, so those responses are reshaped rather than returned as DRF built them.
DRF_ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "AUTH_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


class AppError(Exception):
    def __init__(self, status_code, code, message, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class ValidationError(AppError):
    def __init__(self, form_errors, field_errors):
        details = {"formErrors": form_errors, "fieldErrors": field_errors}
        super().__init__(400, "VALIDATION_ERROR", "Request validation failed.", details)


def normalize(response):
    """Reshape a DRF error response into the documented error envelope."""
    fields = dict(response.data) if isinstance(response.data, dict) else {}
    detail = fields.pop("detail", None)

    if isinstance(detail, list):
        detail = " ".join(str(item) for item in detail)

    if isinstance(detail, str) and detail:
        message = detail
    elif response.status_code == 404:
        message = NOT_FOUND_MESSAGE
    else:
        message = INTERNAL_ERROR_MESSAGE

    error = {"code": DRF_ERROR_CODES.get(response.status_code, "REQUEST_FAILED"), "message": message}

    if fields:
        error["details"] = fields

    return Response({"error": error}, status=response.status_code)


def api_exception_handler(exc, context):
    if isinstance(exc, AppError):
        error = {"code": exc.code, "message": exc.message}

        if exc.details is not None:
            error["details"] = exc.details

        return Response({"error": error}, status=exc.status_code)

    response = exception_handler(exc, context)

    if response is not None:
        return normalize(response)

    print("Unhandled error:", repr(exc))

    return Response({"error": {"code": "INTERNAL_ERROR", "message": INTERNAL_ERROR_MESSAGE}}, status=500)
