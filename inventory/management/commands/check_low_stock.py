import os
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.db.models import Sum
from inventory.models import Tool


LOW_STOCK_RECIPIENT = os.environ.get("LOW_STOCK_RECIPIENT", "emmanueludeme1821@gmail.com")
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

        # 5. Build and send the email — table format (Equipment | Qty Remaining | Threshold)
        name_width = max([len("Equipment")] + [len(item["name"]) for item in new_low_stock])
        qty_width = max(len("Qty Remaining"), 5)
        threshold_width = max(len("Threshold"), 3)

        header = (
            f"{'Equipment'.ljust(name_width)}  "
            f"{'Qty Remaining'.rjust(qty_width)}  "
            f"{'Threshold'.rjust(threshold_width)}"
        )
        separator = "-" * len(header)
        text_rows = [
            f"{item['name'].ljust(name_width)}  "
            f"{str(item['total_stock']).rjust(qty_width)}  "
            f"{str(LOW_STOCK_THRESHOLD).rjust(threshold_width)}"
            for item in new_low_stock
        ]

        body = (
            f"The following items are low on stock (below {LOW_STOCK_THRESHOLD} units):\n\n"
            + header + "\n" + separator + "\n"
            + "\n".join(text_rows)
            + f"\n\nEach item will stop appearing in this alert once its stock is restocked to {LOW_STOCK_THRESHOLD} or more units."
        )

        html_rows = "".join(
            f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{item['name']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;color:#64748b;">{item['category']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;text-align:right;font-weight:bold;color:#dc2626;">{item['total_stock']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;text-align:right;color:#64748b;">{LOW_STOCK_THRESHOLD}</td>
            </tr>"""
            for item in new_low_stock
        )

        html_body = f"""
        <div style="font-family:Arial,Helvetica,sans-serif;color:#1e293b;">
            <p>The following items are low on stock (below {LOW_STOCK_THRESHOLD} units):</p>
            <table style="border-collapse:collapse;width:100%;max-width:600px;margin:16px 0;">
                <thead>
                    <tr style="background:#1e293b;color:#ffffff;">
                        <th style="padding:8px 12px;text-align:left;">Equipment</th>
                        <th style="padding:8px 12px;text-align:left;">Category</th>
                        <th style="padding:8px 12px;text-align:right;">Qty Remaining</th>
                        <th style="padding:8px 12px;text-align:right;">Threshold</th>
                    </tr>
                </thead>
                <tbody>{html_rows}
                </tbody>
            </table>
            <p>Each item will stop appearing in this alert once its stock is restocked to {LOW_STOCK_THRESHOLD} or more units.</p>
        </div>
        """

        send_mail(
            subject=f"Low Stock Alert — {len(new_low_stock)} item(s) need reordering",
            message=body,
            from_email=None,
            recipient_list=[LOW_STOCK_RECIPIENT],
            html_message=html_body,
            fail_silently=False,
        )