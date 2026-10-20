import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")

app = Celery("claims_service")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
