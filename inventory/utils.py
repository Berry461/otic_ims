# inventory/utils.py
import random
import string
from decimal import Decimal
from django.db.models import Sum


def generate_paystack_reference():
    """
    Generates a mock/test Paystack transaction reference.
    Used to simulate payment creation or verification.
    """
    prefix = "PSK"
    unique_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{prefix}-{unique_id}"

def sync_single_customer_financials(customer):
    """Runs the exact same logic you wrote, but for a single customer"""
    # ... paste your Step 1 through Step 6 logic here ...
    # e.g., calculate total_selling_price, amount_paid, next_due
    # update customer.status if sale is overdue, etc.
    customer.save()
