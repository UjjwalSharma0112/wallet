from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import validate_create, validate_list


@api_view(["GET", "POST"])
@require_auth
def batches(request):
    if request.method == "POST":
        return Response({"data": services.create_batch(request.account, validate_create(request.data))}, status=201)

    state, limit, offset = validate_list(request.query_params)

    return Response({"data": services.list_batches(request.account["_id"], state, limit, offset)})


@api_view(["POST"])
@require_auth
def processing(request, batch_id):
    # Processing answers with the batch as it now stands and the owner's own wallet, because
    # this is a caller moving money and should not have to ask twice to see what it did.
    return Response({"data": services.process_batch(request.account, batch_id)})