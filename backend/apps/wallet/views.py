from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import validate_list


@api_view(["GET"])
@require_auth
def wallet(request):
    return Response({"data": services.get_wallet(str(request.account["_id"]))})


@api_view(["GET"])
@require_auth
def transactions(request):
    limit, offset = validate_list(request.query_params)

    return Response({"data": services.list_transactions(str(request.account["_id"]), limit, offset)})
