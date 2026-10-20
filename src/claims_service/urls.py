from django.urls import path

from claims_service.intake.api import api

urlpatterns = [path("api/", api.urls)]
