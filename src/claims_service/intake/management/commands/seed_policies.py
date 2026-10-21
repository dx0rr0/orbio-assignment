from django.core.management.base import BaseCommand
from django.utils import timezone

from claims_agent.seed import seed_policies
from claims_service.intake.models import PolicyRecord


class Command(BaseCommand):
    help = "Create or refresh the demo policies, with dates relative to today."

    def handle(self, *args: object, **options: object) -> None:
        for policy in seed_policies(timezone.localdate()):
            PolicyRecord.objects.update_or_create(
                number=policy.number, defaults=policy.model_dump(exclude={"number"})
            )
            self.stdout.write(f"{policy.number} {policy.start_date} to {policy.end_date}")
