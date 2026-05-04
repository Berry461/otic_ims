content = open('/app/inventory/models.py').read()

old = "                self.quote_number = f\"INV-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}\""

new = """                year = timezone.now().year
                last = Quotation.objects.filter(
                    quote_number__startswith=f"{year}/INV/"
                ).order_by("-quote_number").first()
                if last and last.quote_number:
                    try:
                        last_seq = int(last.quote_number.split("/")[-1])
                    except (ValueError, IndexError):
                        last_seq = 0
                else:
                    last_seq = 0
                self.quote_number = f"{year}/INV/{str(last_seq + 1).zfill(5)}\""""

if old in content:
    content = content.replace(old, new)
    open('/app/inventory/models.py', 'w').write(content)
    print('Updated successfully')
else:
    print('Pattern not found - old string not in file')
    # Show what's around line 721
    lines = content.split('\n')
    for i, line in enumerate(lines[718:726], start=719):
        print(f"{i}: {repr(line)}")