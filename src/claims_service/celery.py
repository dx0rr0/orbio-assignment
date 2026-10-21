import os

from celery import Celery
from celery.signals import worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")

app = Celery("claims_service")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_process_init.connect
def configure_tasks(**kwargs: object) -> None:
    """Each worker process builds the dependencies of its tasks, after the fork (the Langfuse
    client starts threads). Imported here: this module loads before Django is set up."""
    from claims_service.intake import services, tasks

    tasks.configure(services.for_tasks())
