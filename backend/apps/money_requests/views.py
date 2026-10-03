from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import validate_create, validate_list


@api_view(["GET", "POST"])
@require_auth
def money_requests(request):
    if request.method == "POST":
        approver_email, amount_minor, note = validate_create(request.data)

        return Response({"data": services.create_request(request.account, approver_email, amount_minor, note)}, status=201)

    role, state, limit, offset = validate_list(request.query_params)

    return Response({"data": services.list_requests(request.account["_id"], role, state, limit, offset)})


@api_view(["POST"])
@require_auth
def approval(request, request_id):
    # Both actions answer with what the request now is. Approving also answers with the
    # transfer that paid it and the approver's own wallet, because that is the engine's own
    # answer and a caller paying money should not have to ask twice to see what it did.
    return Response({"data": services.approve_request(request.account, request_id)})


@api_view(["POST"])
@require_auth
def decline(request, request_id):
    return Response({"data": services.decline_request(request.account, request_id)})
