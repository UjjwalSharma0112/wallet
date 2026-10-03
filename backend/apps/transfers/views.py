from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.shared.authentication import require_auth

from . import services
from .schema import validate_transfer


@api_view(["POST"])
@require_auth
def transfers(request):
    recipient_email, amount_minor, note = validate_transfer(request.data)

    return Response({"data": services.execute_transfer(request.account, recipient_email, amount_minor, note)}, status=201)
