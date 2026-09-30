import os
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.db.models import Sum
from inventory.models import Tool


LOW_STOCK_RECIPIENT = os.environ.get("LOW_STOCK_RECIPIENT", "oticsurveyslagos@gmail.com")
LOW_STOCK_THRESHOLD = 5  # strictly less than this


class Command(BaseCommand):
    help = "Weekly sweep for low-stock tools; emails a summary and resets flags for restocked items."

    def handle(self, *args, **options):
        # 1. Reset the flag for anything that's been restocked since last sweep
        restocked = Tool.objects.values("name").annotate(
            total_stock=Sum("stock")
        ).filter(total_stock__gte=LOW_STOCK_THRESHOLD)
        restocked_names = [r["name"] for r in restocked]
        Tool.objects.filter(name__in=restocked_names, low_stock_notified=True).update(
            low_stock_notified=False
        )

        # 2. Find currently low-stock tools (aggregate stock per name)
        low_stock = list(
            Tool.objects.values("name", "category")
            .annotate(total_stock=Sum("stock"))
            .filter(total_stock__gt=0, total_stock__lt=LOW_STOCK_THRESHOLD)
            .order_by("total_stock")
        )

        # 3. Exclude ones already flagged as notified
        already_notified = set(
            Tool.objects.filter(low_stock_notified=True).values_list("name", flat=True)
        )
        new_low_stock = [item for item in low_stock if item["name"] not in already_notified]

        if not new_low_stock:
            self.stdout.write("No new low-stock items to report.")
            return

        # 4. Mark these as notified so they don't repeat next week
        Tool.objects.filter(
            name__in=[item["name"] for item in new_low_stock]
        ).update(low_stock_notified=True)

        # 5. Build and send the email
        lines = [
            f"- {item['name']} ({item['category']}): {item['total_stock']} left"
            for item in new_low_stock
        ]
        body = (
            "The following items are low on stock (below 5 units):\n\n"
            + "\n".join(lines)
            + "\n\nEach item will stop appearing in this alert once its stock is restocked to 5 or more units."
        )

        send_mail(
            subject=f"Low Stock Alert — {len(new_low_stock)} item(s) need reordering",
            message=body,
            from_email=None,
            recipient_list=[LOW_STOCK_RECIPIENT],
            fail_silently=False,
        )

        self.stdout.write(f"Sent low-stock alert for {len(new_low_stock)} item(s).")