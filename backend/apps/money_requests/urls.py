from django.urls import path

from . import views

urlpatterns = [
    path("money-requests", views.money_requests),
    path("money-requests/<str:request_id>/approval", views.approval),
    path("money-requests/<str:request_id>/decline", views.decline),
]
