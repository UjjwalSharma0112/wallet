from django.urls import path

from . import views

urlpatterns = [
    path("refunds", views.refunds),
]
