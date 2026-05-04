from django.db import connection
cursor = connection.cursor()
cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='inventory_quotation'")
cols = [row[0] for row in cursor.fetchall()]
print("Columns:", cols)
print("Has document_type:", 'document_type' in cols)