import traceback
from inventory.models import Quotation, QuotationItem
from inventory.serializers import QuotationSerializer

data = {
    'name': 'Test Customer',
    'phone': '08012345678',
    'email': '',
    'state': 'Lagos',
    'staff': 'Admin',
    'payment_plan': 'No',
    'initial_deposit': None,
    'payment_months': None,
    'notes': '',
    'valid_until': None,
    'bank_name': 'PROVIDUS BANK',
    'account_name': 'OTIC GEOSYSTEMS LTD',
    'account_number': '1309010165',
    'tin_number': '31413107-0001',
    'footer_note': 'Test',
    'document_type': 'invoice',
    'total_cost': '1000',
    'tax_amount': '0',
    'items': [{'equipment': 'T20', 'equipment_type': 'Base', 'category': 'Receiver', 'cost': '1000', 'quantity': 1}],
}

try:
    s = QuotationSerializer(data=data)
    valid = s.is_valid()
    print('Valid:', valid)
    print('Errors:', s.errors)
    if valid:
        q = s.save()
        print('SUCCESS - id:', q.id, 'quote_number:', q.quote_number, 'document_type:', q.document_type)
        print('Items count:', q.items.count())
except Exception as e:
    print('EXCEPTION:', type(e).__name__, str(e))
    traceback.print_exc()