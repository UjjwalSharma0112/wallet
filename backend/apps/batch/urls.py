from django.urls import path

from . import views

urlpatterns = [
    path("batches", views.batches),
    path("batches/<str:batch_id>/processing", views.processing),
]