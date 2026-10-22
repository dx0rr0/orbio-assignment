from django.urls import path

from claims_service.intake import services
from claims_service.intake.api import build_api

urlpatterns = [path("api/", build_api(services.for_api()).urls)]
