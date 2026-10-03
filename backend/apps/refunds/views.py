from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth
from apps.transfers.schema import read_idempotency_key

from . import services
from .schema import validate_create, validate_list


@api_view(["GET", "POST"])
@require_auth
def refunds(request):
    if request.method == "POST":
        original_transfer_id, amount_minor, note = validate_create(request.data)
        idempotency_key = read_idempotency_key(request.headers)

        data, created = services.create_refund(request.account, original_transfer_id, amount_minor, note, idempotency_key)
        response = Response({"data": data}, status=201 if created else 200)

        if not created:
            response["Idempotent-Replayed"] = "true"

        return response

    limit, offset = validate_list(request.query_params)

    return Response({"data": services.list_refunds(request.account["_id"], limit, offset)})
