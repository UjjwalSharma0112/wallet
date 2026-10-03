from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import read_idempotency_key, validate_transfer


@api_view(["POST"])
@require_auth
def transfers(request):
    recipient_email, amount_minor, note = validate_transfer(request.data)
    idempotency_key = read_idempotency_key(request.headers)

    data, created = services.execute_transfer(
        request.account, recipient_email, amount_minor, note, idempotency_key
    )

    # A transfer that was created answers 201. A repeat of a key already spent answers 200
    # with the transfer that key produced, because nothing was created this time.
    response = Response({"data": data}, status=201 if created else 200)

    if not created:
        response["Idempotent-Replayed"] = "true"

    return response
