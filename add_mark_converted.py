import re

# ── 1. Add view to views.py ──────────────────────────────────────────────────
views_content = open('/app/inventory/views.py').read()

new_view = '''

class QuotationMarkConvertedView(APIView):
    """
    POST /api/quotations/<pk>/mark-converted/
    Simply marks a quotation or standalone invoice as converted
    without creating a sale. Used when sale is created via Sales dialog.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            quotation = Quotation.objects.get(pk=pk)
            quotation.is_converted = True
            quotation.save(update_fields=['is_converted'])
            return Response({"success": True, "message": f"{quotation.quote_number} marked as converted."})
        except Quotation.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        except Exception as e:
            return Response({"error": str(e)}, status=500)
'''

# Insert after QuotationSendEmailView class
if 'class QuotationMarkConvertedView' not in views_content:
    # Find a good insertion point - after the last quotation view
    if 'class QuotationSendEmailView' in views_content:
        # Find the end of QuotationSendEmailView by finding next class after it
        idx = views_content.find('class QuotationSendEmailView')
        # Find next class definition after this one
        next_class = views_content.find('\nclass ', idx + 1)
        if next_class == -1:
            views_content += new_view
        else:
            views_content = views_content[:next_class] + new_view + views_content[next_class:]
        open('/app/inventory/views.py', 'w').write(views_content)
        print('✅ View added to views.py')
    else:
        print('❌ Could not find insertion point in views.py')
else:
    print('ℹ️ View already exists in views.py')

# ── 2. Add URL to urls.py ─────────────────────────────────────────────────────
urls_content = open('/app/inventory/urls.py').read()

if 'mark-converted' not in urls_content:
    # Add import
    old_import = 'QuotationListCreateView, QuotationDetailView, QuotationConvertView'
    new_import = 'QuotationListCreateView, QuotationDetailView, QuotationConvertView, QuotationMarkConvertedView'
    
    if old_import in urls_content:
        urls_content = urls_content.replace(old_import, new_import)
        print('✅ Import updated in urls.py')
    else:
        print('⚠️ Could not find import line - adding manually')
        # Try to add it near other quotation imports
        urls_content = urls_content.replace(
            'QuotationConvertView\n)',
            'QuotationConvertView, QuotationMarkConvertedView\n)'
        )

    # Add URL pattern
    old_url = "    path('quotations/<int:pk>/convert/', QuotationConvertView.as_view(), name='quotation-convert'),"
    new_url = """    path('quotations/<int:pk>/convert/', QuotationConvertView.as_view(), name='quotation-convert'),
    path('quotations/<int:pk>/mark-converted/', QuotationMarkConvertedView.as_view(), name='quotation-mark-converted'),"""
    
    if old_url in urls_content:
        urls_content = urls_content.replace(old_url, new_url)
        open('/app/inventory/urls.py', 'w').write(urls_content)
        print('✅ URL pattern added to urls.py')
    else:
        print('❌ Could not find convert URL pattern')
        print('Looking for:', repr(old_url))
else:
    print('ℹ️ URL already exists in urls.py')

print('Done.')