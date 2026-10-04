from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import validate_period


@api_view(["GET"])
@require_auth
def analytics(request):
    # The period is the only thing a caller gets to choose. The wallet it is measured
    # against comes from the authenticated account, so there is no parameter that could
    # point the figures at somebody else's money — a parameter that cannot exist is a
    # stronger boundary than one that is checked.
    from_date, to_date = validate_period(request.query_params)

    return Response({"data": services.account_analytics(str(request.account["_id"]), from_date, to_date)})