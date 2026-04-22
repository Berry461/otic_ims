from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import CodeAssignmentLog, Tool, EquipmentType, Payment, Sale, Customer, Supplier, SaleItem, CodeBatch, ActivationCode, Quotation, QuotationItem, DisplayStaff
from django.utils import timezone
from datetime import timedelta
import json

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["id", "email", "name", "phone", "role", "password"]
        read_only_fields = ["id", "role"]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

class DisplayStaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisplayStaff
        fields = ['id', 'name', 'email', 'phone', 'created_at']
        read_only_fields = ['id', 'created_at']


class ToolSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    equipment_type_name = serializers.CharField(source="equipment_type.name", read_only=True)
    equipment_type_id = serializers.CharField(source="equipment_type.id", read_only=True)
    box_type = serializers.CharField(source="description", read_only=True)  # Map description to box_type for frontend
    invoice_no = serializers.CharField(source="invoice_number", read_only=True)  # Map invoice_number to invoice_no

    class Meta:
        model = Tool
        fields = [
            "id",
            "name",
            "code",
            "category",
            "description",
            "box_type",  # Added for frontend compatibility
            "cost",
            "stock",
            "supplier",
            "supplier_name",
            "equipment_type",  
            "equipment_type_name",  
            "equipment_type_id",  
            "is_enabled",
            "invoice_number",
            "invoice_no",  # Added for frontend compatibility
            "date_added",
            "expiry_date",  # NEW: Added expiry_date
            "serials",
            "available_serials",  # NEW: Added available_serials
            "sold_serials",       # NEW: Added sold_serials
        ]
        extra_kwargs = {
            'expiry_date': {'required': False, 'allow_null': True}
        }

    def validate_serials(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Serials must be a list.")
        if not all(isinstance(s, str) for s in value):
            raise serializers.ValidationError("Each serial must be a string.")
        return value

    def validate_expiry_date(self, value):
        """Validate that expiry date is not in the past when creating/updating"""
        if value and value < timezone.now().date():
            raise serializers.ValidationError("Expiry date cannot be in the past.")
        return value

class EquipmentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentType  
        fields = ["id", "name", "default_cost", "naira_cost", "category", "description", "invoice_number", "created_at"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = "__all__"

class SaleItemSerializer(serializers.ModelSerializer):
    tool_id = serializers.PrimaryKeyRelatedField(
        queryset=Tool.objects.all(), source="tool", write_only=True
    )
    
    # NEW: Add computed fields for frontend
    serial_set = serializers.SerializerMethodField()
    datalogger_serial = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    assigned_tool_id = serializers.CharField(required=False, allow_blank=True)
    import_invoice = serializers.CharField(required=False, allow_blank=True, allow_null=True)  # NEW: Add import_invoice field
    equipment_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    external_radio_serial = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = SaleItem
        fields = [
            'id', 'tool_id', 'equipment', 'cost', 'category', 
            'serial_number', 'serial_set', 'datalogger_serial', 
            'invoice_number', 'assigned_tool_id', 'import_invoice',
            'equipment_type',          # ✅ ADD THIS
            'external_radio_serial'  # NEW: Added import_invoice
        ]
        read_only_fields = ['id']

    def get_serial_set(self, obj):
        """Convert serial_number to serial_set array for frontend"""
        if obj.serial_number:
            # If it's a JSON string, parse it, otherwise treat as single serial
            try:
                serials = json.loads(obj.serial_number)
                if isinstance(serials, list):
                    return serials
            except (json.JSONDecodeError, TypeError):
                pass
            # Return as single item array
            return [obj.serial_number]
        return []

    def get_datalogger_serial(self, obj):
        """Extract datalogger serial from tool if available"""
        if obj.tool and hasattr(obj.tool, 'datalogger_serial'):
            return obj.tool.datalogger_serial
        return None

    def get_invoice_number(self, obj):
        """Get import invoice from sale"""
        if obj.sale and obj.sale.invoice_number:
            return obj.sale.invoice_number
        return None

    def create(self, validated_data):
        # Get a random serial number if not provided
        tool = validated_data.get('tool')
        if tool and not validated_data.get('serial_number'):
            random_serial = tool.get_random_serial()
            if random_serial:
                validated_data['serial_number'] = random_serial
                
        return super().create(validated_data)


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    # Since staff is now a CharField, these no longer need 'source="staff.name"'
    staff = serializers.CharField(required=False, allow_blank=True)
    staff_name = serializers.CharField(source="staff", read_only=True)
    sold_by = serializers.CharField(default="N/A", read_only=True)
    
    date_sold = serializers.DateField(format='%Y-%m-%d')
    import_invoice = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_overdue = serializers.ReadOnlyField()
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            "id", "staff", "staff_name", "sold_by", "name", "phone", 
            "state", "items", "total_cost", "tax_amount", "date_sold", 
            "invoice_number", "payment_plan", 'initial_deposit', 
            'payment_months', "due_date", "payment_status", "import_invoice",
            "is_overdue",
        ]
        read_only_fields = ["staff_name", "sold_by", "date_sold", "invoice_number"]

    def get_payment_status(self, obj):
        db_status = (obj.payment_status or "ongoing").lower()

        # 0. If it's a draft (pending), return immediately — never override
        if db_status == 'pending':
            return 'pending'

        # 1. If it's fully paid, keep it that way
        if db_status in ['completed', 'paid', 'fully-paid']:
            return db_status

        today = timezone.localdate()

        # 2. THE ABSOLUTE RULE: If the final due date has passed, it is permanently overdue
        if obj.due_date and today >= obj.due_date:
            return "overdue"

        # 3. THE 90-DAY (3 MONTHS) INACTIVITY RULE:
        # Get the most recent payment logged for this sale
        last_payment = obj.payment_set.order_by('-payment_date').first()
        
        if last_payment and last_payment.payment_date:
            last_activity_date = last_payment.payment_date
            if hasattr(last_activity_date, 'date'):
                last_activity_date = last_activity_date.date()
        else:
            # If they haven't made ANY payments yet, calculate from the day it was sold
            last_activity_date = obj.date_sold

        if last_activity_date:
            days_inactive = (today - last_activity_date).days
            if days_inactive >= 90:  # Flips to overdue if inactive for 90 days or more
                return "overdue"

        # 4. If due_date hasn't passed AND they made a payment within 90 days:
        return "ongoing"

    def create(self, validated_data):
        # 1. Extract nested items
        items_data = validated_data.pop('items', [])
        
        # 2. Get the staff string sent from React
        request = self.context.get('request')
        staff_val = request.data.get('staff') if request else None

        # 3. Logic: Priority to React name, then User attributes
        if staff_val and str(staff_val).strip() not in ["", "null", "undefined"]:
            final_staff_name = str(staff_val)
        elif request and request.user:
            user = request.user
            # ✅ SAFE CHECK: Try 'name' field first, then 'username'
            # We AVOID .get_full_name() here to prevent the crash
            final_staff_name = getattr(user, 'name', None) or getattr(user, 'username', 'Admin')
        else:
            final_staff_name = "Admin"
        
        # Ensure it's stored as a string in the validated_data
        validated_data['staff'] = final_staff_name

        # 4. Save the Sale
        sale = Sale.objects.create(**validated_data)
        
        # 5. Create the nested SaleItems
        for item_data in items_data:
            SaleItem.objects.create(sale=sale, **item_data)
            
        return sale

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        
        # Update Sale fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update SaleItems if provided
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                SaleItem.objects.create(sale=instance, **item_data)
                
        return instance
    
class QuotationItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotationItem
        fields = ['id', 'equipment', 'equipment_type', 'category', 'cost', 'quantity']
        read_only_fields = ['id']


class QuotationSerializer(serializers.ModelSerializer):
    items = QuotationItemSerializer(many=True)

    class Meta:
        model = Quotation
        fields = [
            'id', 'quote_number', 'name', 'phone', 'email', 'state',
            'staff', 'total_cost', 'tax_amount', 'payment_plan',
            'initial_deposit', 'payment_months', 'notes',
            'date_created', 'valid_until', 'is_converted',
            'converted_sale_id', 'items',
            'bank_name', 'account_name', 'account_number', 'tin_number', 'footer_note',
        ]
        read_only_fields = ['id', 'quote_number', 'date_created']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        quotation = Quotation.objects.create(**validated_data)
        for item in items_data:
            QuotationItem.objects.create(quotation=quotation, **item)
        return quotation

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                QuotationItem.objects.create(quotation=instance, **item)
        return instance
    
    
class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "state", "is_activated"]

class CustomerOwingSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='user.id', read_only=True) if not serializers.CharField else serializers.CharField(read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'email', 'phone', 'total_selling_price', 
            'amount_paid', 'amount_left', 'date_last_paid', 
            'date_next_installment', 'status', 'progress'
        ]
    
    def to_representation(self, instance):
        """Convert the data to match frontend expectations"""
        data = super().to_representation(instance)
        
        # Convert field names to match frontend camelCase
        data['totalSellingPrice'] = float(data.pop('total_selling_price'))
        data['amountPaid'] = float(data.pop('amount_paid'))
        data['amountLeft'] = float(data.pop('amount_left'))
        data['dateLastPaid'] = data.pop('date_last_paid')
        data['dateNextInstallment'] = data.pop('date_next_installment')
        
        # Ensure ID is string format for frontend
        data['id'] = str(instance.id)
        
        return data

class PaymentSerializer(serializers.ModelSerializer):
    sale = serializers.PrimaryKeyRelatedField(
        queryset=Sale.objects.all(), required=False, allow_null=True
    )

    items = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField() # This is the key field

    class Meta:
        model = Payment
        fields = [
            "id", "customer", "sale", "items", "amount",
            "payment_method", "payment_reference", "payment_date", "status", 
            "payment_status"
        ]
        read_only_fields = ["customer", "payment_date", "status"]

    def get_items(self, obj):
        if obj.sale:
            return list(obj.sale.items.values('equipment', 'equipment_type'))
        return []

    def get_payment_status(self, obj):
        if not obj.sale:
            return (obj.status or "completed").lower()

        sale = obj.sale
        db_sale_status = (sale.payment_status or "ongoing").lower()
        
        # 1. If the sale is actually finished, show completed
        if db_sale_status in ['completed', 'paid', 'fully-paid']:
            return "completed"

        today = timezone.localdate()

        # 2. THE ABSOLUTE RULE
        if sale.due_date and today >= sale.due_date:
            return "overdue"

        # 3. THE 90-DAY (3 MONTHS) INACTIVITY RULE
        last_payment = sale.payment_set.order_by('-payment_date').first()
        
        if last_payment and last_payment.payment_date:
            last_activity_date = last_payment.payment_date
            if hasattr(last_activity_date, 'date'):
                last_activity_date = last_activity_date.date()
        else:
            last_activity_date = sale.date_sold

        if last_activity_date:
            days_inactive = (today - last_activity_date).days
            if days_inactive >= 90:  # Flips to overdue if inactive for 90 days or more
                return "overdue"

        # 4. Everything is good
        return "ongoing"

    def create(self, validated_data):
        # Keep your existing create logic
        user = self.context["request"].user
        validated_data["customer"] = user
        payment = super().create(validated_data)
        payment.status = "completed"
        payment.save()

        # Optional: If this payment clears the debt, you'd mark the sale completed here
        # For now, I am leaving your existing logic:
        if payment.sale:
            sale = payment.sale
            sale.payment_status = "completed"
            sale.save()

        return payment

class CodeBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeBatch
        fields = '__all__'


class ActivationCodeSerializer(serializers.ModelSerializer):
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    qr_code_image = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_expired = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = ActivationCode
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at','qr_code_image']



class CodeAssignmentLogSerializer(serializers.ModelSerializer):
    code_str = serializers.CharField(source='code.code', read_only=True)
    assigned_by_email = serializers.CharField(source='assigned_by.email', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    
    class Meta:
        model = CodeAssignmentLog
        fields = '__all__'
