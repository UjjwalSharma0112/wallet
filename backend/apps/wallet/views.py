from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth
from apps.refunds import services as refund_services
from apps.transfers import services as transfer_services

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
    account_id = str(request.account["_id"])
    page = services.list_transactions(account_id, limit, offset)
    # Who a transfer was with is transfer data, so it is resolved by the transfers app
    # rather than copied onto the ledger. Calling it from here keeps that dependency
    # pointing one way, since the transfers app already depends on this one. Refund
    # context is attached the same way: the wallet does not know about refunds, the
    # refunds app does, so it is the refunds app that says what a row can give back.
    page["entries"] = transfer_services.decorate_entries(page["entries"], account_id)
    page["entries"] = refund_services.decorate_entries(page["entries"], account_id)

    return Response({"data": page})
