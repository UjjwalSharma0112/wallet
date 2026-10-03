from django.urls import path, re_path
from rest_framework.decorators import api_view

from apps.shared.views import health, not_found, route_not_found


@api_view(["GET", "POST", "PATCH", "PUT", "DELETE"])
def unknown_api_route(request, path):
    route_not_found(request)


urlpatterns = [
    path("api/v1/health", health),
    re_path(r"^api/v1/(?P<path>.*)$", unknown_api_route),
    re_path(r"^.*$", not_found),
]
