from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save
import uuid, random, string
from datetime import date
from django.contrib.auth import get_user_model
from django.utils import timezone

# ----------------------------
#  USER
# ----------------------------
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")
        if not password:
            raise ValueError("Superuser must have a password")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("staff", "Staff"),
        ("customer", "Customer"),
    )

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="staff")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


# ----------------------------
# DISPLAY STAFF (no login access)
# ----------------------------
class DisplayStaff(models.Model):
    """
    Staff members for sales attribution only.
    No login, no password — just a name and email
    that appears in the sales form dropdown.
    """
    name  = models.CharField(max_length=100, unique=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# ----------------------------
#  CUSTOMERS
# ----------------------------
class Customer(models.Model):
    STATUS_CHOICES = [
        ('ongoing', 'Ongoing'),
        ('overdue', 'Overdue'),
        ('fully-paid', 'Fully Paid'),
        ('credit', 'Credit'),
    ]
    # ... (keep all the other fields exactly the same until you hit status)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='ongoing', # <--- THIS MUST BE ONGOING
        verbose_name="Payment Status"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    is_activated = models.BooleanField(default=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='customer_profile'
    )
    
    # Installment tracking fields
    total_selling_price = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Total Selling Price"
    )
    amount_paid = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Amount Paid"
    )
    amount_left = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Amount Left"
    )
    date_last_paid = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Date Last Paid"
    )
    date_next_installment = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Next Installment Date"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='ongoing',
        verbose_name="Payment Status"
    )
    progress = models.IntegerField(
        default=0,
        verbose_name="Payment Progress (%)",
        help_text="Percentage of total amount paid"
    )

    def __str__(self):
        return self.name or "Unnamed Customer"

    def save(self, *args, **kwargs):
        # Auto-calculate amount_left and progress before saving
        if self.total_selling_price > 0:
            self.amount_left = self.total_selling_price - self.amount_paid
            self.progress = min(int((self.amount_paid / self.total_selling_price) * 100), 100)
            
            # Auto-update status based on amounts and dates
            self.update_status()
        else:
            self.amount_left = 0
            self.progress = 0
            
        super().save(*args, **kwargs)

    def update_status(self):
        """Update customer status based on payment progress, sales, and dates"""
        from django.utils import timezone
        
        # Prevent circular import errors
        try:
            from .models import Sale
        except ImportError:
            pass 

        # 1. Check if overpaid — company owes customer
        if self.amount_left < 0:
            self.status = 'credit'
            return

        # 2. Check if exactly fully paid
        if self.amount_left == 0:
            self.status = 'fully-paid'
            return
            
        # 2. Check if ANY linked sale is overdue
        if self.phone:
            linked_sales = Sale.objects.filter(phone=self.phone)
        elif getattr(self, 'name', None):
            linked_sales = Sale.objects.filter(name__iexact=self.name)
        else:
            linked_sales = []
            
        if linked_sales and any(getattr(sale, 'is_overdue', False) for sale in linked_sales):
            self.status = 'overdue'
            return
        
        # 3. Standard date logic (Ongoing vs Overdue only)
        today = timezone.now().date()
        
        if not self.date_next_installment:
            self.status = 'ongoing'
            return
            
        if self.date_next_installment < today:
            self.status = 'overdue'
        else:
            self.status = 'ongoing'

    def set_next_installment_date(self, date):
        """Set the next installment date"""
        self.date_next_installment = date
        self.save()

    @property
    def is_overdue(self):
        """Check if customer is overdue on payments"""
        from django.utils import timezone
        if self.date_next_installment:
            return self.date_next_installment < timezone.now().date()
        return False

    @property
    def is_due_soon(self):
        """Check if payment is due within 7 days"""
        from django.utils import timezone
        from datetime import timedelta
        
        if self.date_next_installment:
            next_week = timezone.now().date() + timedelta(days=7)
            return (timezone.now().date() < self.date_next_installment <= next_week)
        return False


@receiver(post_save, sender=Customer)
def create_user_for_customer(sender, instance, created, **kwargs):
    if created and not instance.user:
        user = User.objects.create_user(
            email=instance.email or f"{instance.phone}@example.com",
            password="defaultpass123",
            role="customer",
            is_active=True,  # <--- CHANGED TO TRUE
        )
        instance.user = user
        instance.save()



# ----------------------------
#  TOOLS MODEL
# ----------------------------


class Tool(models.Model):
    CATEGORY_CHOICES = (
        ("Receiver", "Receiver"),
        ("Accessory", "Accessory"),
        ("Total Station", "Total Station"),
        ("Level", "Level"),
        ("Drones", "Drones"),
        ("EchoSounder", "EchoSounder"),
        ("Laser Scanner", "Laser Scanner"),
        ("Other", "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    description = models.TextField(blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=1)

    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools",
    )

    equipment_type = models.ForeignKey(
        "EquipmentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools",
        verbose_name="Equipment Type"
    )

    is_enabled = models.BooleanField(default=True)
    invoice_number = models.CharField(max_length=50, blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Expiry Date")

    # JSON Storage for Serials
    serials = models.JSONField(default=list, blank=True)
    available_serials = models.JSONField(default=list, blank=True)
    pending_serials = models.JSONField(default=list, blank=True)  # Reserved during selection
    sold_serials = models.JSONField(default=list, blank=True)     # Finalized sales

    def __str__(self):
        return f"{self.name} ({self.code})"

    # --- STOCK MANAGEMENT ---

    def decrease_stock(self):
        """Reduces stock count manually if needed."""
        if self.stock > 0:
            self.stock -= 1
            self.save(update_fields=["stock"])

    def increase_stock(self):
        """Increases stock count manually if needed."""
        self.stock += 1
        self.save(update_fields=["stock"])

    # --- SERIAL ASSIGNMENT LOGIC ---

    def get_serial_set_count(self):
        """Calculates how many serials are needed based on description."""
        if not self.description:
            return 1
        
        desc = self.description.lower()
        if "base only" in desc or "rover only" in desc:
            return 2  # receiver + datalogger
        elif "combo" in desc or "base and rover" in desc:
            return 4  # 2 receivers + 2 dataloggers
        return 1

    def get_random_serial_set(self):
        """
        Logic for the 'Assign' button. 
        Moves serials to PENDING and decreases stock immediately.
        """
        set_count = self.get_serial_set_count()
        
        if not self.available_serials or len(self.available_serials) < set_count:
            return None
            
        # 1. Grab the next available set
        serial_set = self.available_serials[:set_count]
        
        # 2. Remove from available
        self.available_serials = self.available_serials[set_count:]
        
        # 3. Add to pending (so fast loops don't grab them again)
        pending_entry = {
            'serial_set': serial_set,
            'reserved_at': timezone.now().isoformat(),
            'import_invoice': self.invoice_number
        }
        self.pending_serials.append(pending_entry)
        
        # 4. Auto-decrease stock
        if self.stock > 0:
            self.stock -= 1

        self.save(update_fields=["available_serials", "pending_serials", "stock"])
        return serial_set

    def restore_serials(self, serial_set):
        """
        Call this if a user removes an item from the sale table 
        WITHOUT finishing the sale. Puts serials back and restores stock.
        """
        # Remove from pending list
        self.pending_serials = [p for p in self.pending_serials if p.get('serial_set') != serial_set]
        
        # Put back in available
        self.available_serials.extend(serial_set)
        
        # Restore stock count
        self.stock += 1
        
        self.save(update_fields=["available_serials", "pending_serials", "stock"])

    def finalize_sale_serials(self, serial_set, sale_id, customer_name):
        """
        Call this when 'Save Sale' is clicked. 
        Moves serials from PENDING to SOLD permanently.
        """
        # 1. Remove from pending
        self.pending_serials = [p for p in self.pending_serials if p.get('serial_set') != serial_set]

        # 2. Add to sold with details
        sold_info = {
            'serial_set': serial_set,
            'sale_id': str(sale_id),
            'customer': customer_name,
            'date_sold': timezone.now().date().isoformat(),
            'import_invoice': self.invoice_number
        }
        self.sold_serials.append(sold_info)
        
        self.save(update_fields=["pending_serials", "sold_serials"])

    # --- PROPERTIES ---

    @property
    def display_equipment_type(self):
        return self.equipment_type.name if self.equipment_type else "N/A"

    @property
    def is_expired(self):
        return self.expiry_date < timezone.now().date() if self.expiry_date else False

    @property
    def expires_soon(self):
        if self.expiry_date:
            from datetime import timedelta
            thirty_days = timezone.now().date() + timedelta(days=30)
            return timezone.now().date() < self.expiry_date <= thirty_days
        return False

# ----------------------------
#  EQUIPMENT TYPES
# ----------------------------        
class EquipmentType(models.Model):  
    CATEGORY_CHOICES = [
        ("Receiver", "Receiver"),
        ("Accessory", "Accessory"), 
        ("Total Station", "Total Station"),
        ("Level", "Level"),
        ("Drones", "Drones"),
        ("EchoSounder", "EchoSounder"),
        ("Laser Scanner", "Laser Scanner"),
        ("Other", "Other"),
    ]
    
    name = models.CharField(max_length=100)
    default_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    naira_cost = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="Receiver")
    description = models.TextField(blank=True, null=True)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)  # NEW FIELD
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.category} - {self.name}"

    class Meta:
        indexes = [
            models.Index(fields=['invoice_number']),
            models.Index(fields=['category']),
        ]
    
#----------------------------
# SUPPLIERS 
#----------------------------

class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
    
#----------------------------
# SALES 
#----------------------------
class Sale(models.Model):
    PAYMENT_STATUS_CHOICES = (
        ("pending", "Pending"),
        ('ongoing', 'Ongoing'),
        ("completed", "Completed"),
        ("installment", "Installment"),
        ("failed", "Failed"),
        ("overdue", "Overdue"),
    )

    # 🔹 Who made the sale (staff)
    # staff = models.ForeignKey(
    #     User,
    #     on_delete=models.SET_NULL,
    #     related_name="sales_made",
    #     limit_choices_to={"role": "staff"},
    #     null=True,
    #     blank=True,
    # )

    staff = models.CharField(max_length=100, null=True, blank=True)

    # 🔹 Customer information (stored directly in Sale)
    name = models.CharField(max_length=255, db_index=True)
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True, null=True, db_index=True)
    state = models.CharField(max_length=100)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Tax Amount (7.5%)")
    date_sold = models.DateField(default=timezone.now, db_index=True)
    invoice_number = models.CharField(max_length=100, unique=True, blank=True)
    payment_plan = models.CharField(max_length=100, blank=True, null=True)
    initial_deposit = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        blank=True, 
        null=True,
        verbose_name="Initial Deposit Amount"
    )
    payment_months = models.IntegerField(
        blank=True, 
        null=True,
        verbose_name="Number of Payment Months"
    )
    due_date = models.DateField(blank=True, null=True, verbose_name="Final Due Date")
    # expiry_date = models.DateField(blank=True, null=True)
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default="pending", db_index=True
    )
    
    # NEW: Add import_invoice field
    import_invoice = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Import Invoice Number"
    )

    def __str__(self):
        return f"{self.name} - {self.invoice_number}"

    def save(self, *args, **kwargs):
        """Auto-generate invoice or receipt number on creation."""
        if not self.invoice_number:
            year = timezone.now().year

            if self.payment_status == 'completed':
                # Completed at point of sale — generate receipt number, never invoice
                last = (
                    Sale.objects
                    .filter(invoice_number__startswith=f"{year}/RCP/")
                    .order_by("-invoice_number")
                    .first()
                )
                if last and last.invoice_number:
                    try:
                        last_seq = int(last.invoice_number.split("/")[-1])
                    except (ValueError, IndexError):
                        last_seq = 0
                else:
                    last_seq = 0
                next_seq = last_seq + 1
                self.invoice_number = f"{year}/RCP/{str(next_seq).zfill(5)}"
            else:
                # Ongoing, pending, overdue — generate invoice number
                last = (
                    Sale.objects
                    .filter(invoice_number__startswith=f"{year}/INV/")
                    .order_by("-invoice_number")
                    .first()
                )
                if last and last.invoice_number:
                    try:
                        last_seq = int(last.invoice_number.split("/")[-1])
                    except (ValueError, IndexError):
                        last_seq = 0
                else:
                    last_seq = 0
                next_seq = last_seq + 1
                self.invoice_number = f"{year}/INV/{str(next_seq).zfill(5)}"

        # Ensure installment fields are cleared when payment plan is "No"
        if self.payment_plan == "No":
            self.initial_deposit = None
            self.payment_months = None

        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        """Single source of truth for the 90-day inactivity and absolute due date rules."""
        from django.utils import timezone
        
        db_status = (self.payment_status or "ongoing").lower()
        
        # 1. If it's fully paid, it is not overdue
        if db_status in ['completed', 'paid', 'fully-paid']:
            return False

        today = timezone.localdate()

        # 2. THE ABSOLUTE RULE
        if self.due_date and today >= self.due_date:
            return True

        # 3. THE 90-DAY INACTIVITY RULE
        last_payment = self.payment_set.order_by('-payment_date').first()
        
        if last_payment and last_payment.payment_date:
            last_activity_date = last_payment.payment_date
            if hasattr(last_activity_date, 'date'):
                last_activity_date = last_activity_date.date()
        else:
            last_activity_date = self.date_sold

        if last_activity_date:
            days_inactive = (today - last_activity_date).days
            if days_inactive >= 90:
                return True

        return False

class SaleItem(models.Model):
    """Individual items within a sale"""
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE)
    equipment = models.CharField(max_length=255)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=100, blank=True, null=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    assigned_tool_id = models.CharField(max_length=100, blank=True, null=True)
    
    # NEW: Add import_invoice field to SaleItem
    import_invoice = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Import Invoice Number"
    )

    equipment_type = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Equipment Type",
        help_text="Base Only, Rover Only, Base & Rover Combo, etc."
    )

    external_radio_serial = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="External Radio Serial"
    )

    def __str__(self):
        return f"{self.equipment} - ₦{self.cost}"

    def save(self, *args, **kwargs):
        """Deduct stock on first save only"""
        if not self.pk and self.tool.stock > 0:
            self.tool.decrease_stock()
            
            # If serial number is provided, mark it as sold in the tool
            if self.serial_number and self.sale_id:
                self.tool.add_sold_serial_info(
                    serial=self.serial_number,
                    sale_id=self.sale_id,
                    customer_name=self.sale.name,
                    invoice_number=self.sale.invoice_number
                )
                
        super().save(*args, **kwargs)

    # def get_actual_status(self):
    #     """Returns 'overdue' if the date has passed, otherwise returns the stored status."""
    #     if self.payment_status in ['completed', 'fully-paid']:
    #         return self.payment_status
            
    #     if self.due_date and self.due_date < timezone.now().date():
    #         return "overdue"
            
    #     return self.payment_status

    @property
    def is_overdue(self):
        """Delegates the overdue check to the parent Sale to ensure 100% synced logic."""
        if not self.sale:
            return False
        return self.sale.is_overdue
    
    

# ----------------------------
# QUOTATIONS
# ----------------------------
class QuotationItem(models.Model):
    quotation = models.ForeignKey(
        'Quotation', on_delete=models.CASCADE, related_name='items'
    )
    equipment = models.CharField(max_length=255)
    equipment_type = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=100, blank=True, null=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.equipment} x{self.quantity}"


class Quotation(models.Model):
    quote_number = models.CharField(max_length=100, unique=True, blank=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    staff = models.CharField(max_length=100, blank=True, null=True)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_plan = models.CharField(max_length=100, blank=True, null=True)
    initial_deposit = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )
    payment_months = models.IntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    date_created = models.DateField(auto_now_add=True)
    valid_until = models.DateField(blank=True, null=True)
    is_converted = models.BooleanField(default=False)
    converted_sale_id = models.IntegerField(blank=True, null=True)
    bank_name = models.CharField(max_length=100, default="Zenith Bank")
    account_name = models.CharField(max_length=100, default="OTIC GEOSYSTEMS LTD")
    account_number = models.CharField(max_length=50, default="1015175251")
    tin_number = models.CharField(max_length=50, default="31413107-0001")
    footer_note = models.TextField(default="This is a quotation only and does not constitute a final invoice.")

    class Meta:
        ordering = ['-date_created', '-id']

    def __str__(self):
        return f"{self.quote_number} — {self.name}"

    def save(self, *args, **kwargs):
        if not self.quote_number:
            import random, string
            self.quote_number = f"QUO-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        super().save(*args, **kwargs)


# ----------------------------
#  PAYMENTS
# ----------------------------
class Payment(models.Model):
    PAYMENT_METHODS = [
        ("cash", "Cash"),
        ("transfer", "Bank Transfer"),
        ("paystack", "Paystack"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="paystack")
    payment_reference = models.CharField(max_length=100, blank=True, null=True)
    payment_date = models.DateField(default=timezone.now, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    def __str__(self):
        return f"Payment {self.id} - {self.customer.email}"

# ----------------------------
#  ACTIVATION CODES
# ----------------------------

class CodeBatch(models.Model):
    """Batch of codes received from China"""
    batch_number = models.CharField(max_length=100, unique=True)
    received_date = models.DateField(default=timezone.now)
    supplier = models.CharField(max_length=200, default="China Supplier")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.batch_number} ({self.received_date})"
    
class BatchSerial(models.Model):
    """
    Stores individual receiver serials imported from a CSV into a CodeBatch.

    status values (match your CSV exactly):
        'not sold'  → In Stock tab
        'active'    → Sold tab (assigned to a customer)
    """
    batch          = models.ForeignKey(CodeBatch, on_delete=models.CASCADE, related_name='serials')
    serial_number  = models.CharField(max_length=100)
    status         = models.CharField(max_length=50, default='not sold')
    payment_status = models.CharField(max_length=50, default='not_applicable')
    customer_email = models.EmailField(blank=True, null=True)
    customer_name  = models.CharField(max_length=255, blank=True, null=True)
    assigned_date  = models.DateField(blank=True, null=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('batch', 'serial_number')
        ordering = ['serial_number']

    def __str__(self):
        return f"{self.serial_number} ({self.batch.batch_number}) — {self.status}"


class ActivationCode(models.Model):
    
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('assigned', 'Assigned'),
        ('activated', 'Activated'),
        ('expired', 'Expired'),
    ]
    
    # The actual code
    code = models.CharField(max_length=100, unique=True)
    
    # Code properties
    batch = models.ForeignKey(CodeBatch, on_delete=models.SET_NULL, null=True, related_name='codes')
    
    # Assignment
    receiver_serial = models.CharField(max_length=100, blank=True, null=True)  # Serial number of the receiver
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='codes')
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='codes')
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    is_emergency = models.BooleanField(default=False)  # Emergency vs regular code
    
    # Dates
    assigned_date = models.DateTimeField(null=True, blank=True)
    activated_date = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateTimeField(null=True, blank=True)  # Can still set this manually
    
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #QR code image field 
    qr_code_image = models.TextField(null=True, blank=True, help_text="Base64 encoded QR code image")
    
    def __str__(self):
        return f"{self.code} - {self.get_status_display()}"
    
    @property
    def is_expired(self):
        """Check if code is expired"""
        if self.expiry_date:
            from django.utils import timezone
            return timezone.now() > self.expiry_date
        return False
    
    @property
    def is_active(self):
        """Check if code is currently active (assigned and not expired)"""
        if self.status == 'assigned' and not self.is_expired:
            return True
        return False


class CodeAssignmentLog(models.Model):
    """Log of code assignments for audit trail"""
    code = models.ForeignKey(ActivationCode, on_delete=models.CASCADE, related_name='assignment_logs')
    receiver_serial = models.CharField(max_length=100)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    assigned_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"Code {self.code.code} → {self.receiver_serial}"
    
# ----------------------------
#  AUTO-SYNC SIGNALS
# ----------------------------
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from decimal import Decimal

def sync_single_customer(customer):
    """Recalculates a single customer's financials and updates their status"""
    if not customer:
        return

    # 1. Find all NON-DRAFT sales for this customer
    # Excluding pending ensures drafts never affect financials, due dates, or status
    if customer.phone:
        customer_sales = Sale.objects.exclude(payment_status='pending').filter(phone=customer.phone)
    elif customer.name:
        customer_sales = Sale.objects.exclude(payment_status='pending').filter(name__iexact=customer.name)
    else:
        return

    if not customer_sales.exists():
        customer.total_selling_price = Decimal('0')
        customer.amount_paid = Decimal('0')
        customer.date_next_installment = None
        customer.save()
        return

    # 2. Calculate Total Selling Price from real sales only
    total_selling = customer_sales.aggregate(total=Sum('total_cost'))['total'] or Decimal('0')

    # 3. Calculate Amount Paid
    # For completed sales: use total_cost (they are fully paid)
    # For ongoing/overdue: use initial_deposit + all logged payments
    # This is the single source of truth — no max(), no caching issues
    amount_paid = Decimal('0')
    for sale in customer_sales:
        sale_status = (sale.payment_status or '').lower()
        initial = Decimal(str(sale.initial_deposit or '0'))
        logged = Payment.objects.filter(sale=sale).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        if sale_status in ['completed', 'paid', 'fully-paid']:
            amount_paid += Decimal(str(sale.total_cost or '0'))
        else:
            print(f'SYNC CALC: sale={sale.id}, status={sale_status}, initial={initial}, logged={logged}, adding={initial+logged}')
            amount_paid += initial + logged

    # 4. Find Next Installment Date
    next_due = customer_sales.exclude(
        payment_status__in=['completed', 'paid', 'fully-paid']
    ).exclude(
        due_date__isnull=True
    ).order_by('-date_sold').values_list('due_date', flat=True).first()

    # 5. Find Last Paid Date
    latest_payment = Payment.objects.filter(sale__in=customer_sales).order_by('-payment_date').first()
    if latest_payment and latest_payment.payment_date:
        customer.date_last_paid = latest_payment.payment_date.date() if hasattr(latest_payment.payment_date, 'date') else latest_payment.payment_date
    else:
        completed = customer_sales.filter(payment_status__in=['completed', 'paid', 'fully-paid']).order_by('-date_sold').first()
        if completed:
            customer.date_last_paid = completed.date_sold

    # 6. Save — triggers Customer.save() which auto-calculates amount_left and progress
    customer.total_selling_price = total_selling
    customer.amount_paid = amount_paid
    customer.date_next_installment = next_due
    customer.save()

    # 7. Force status override based on sales
    has_overdue_sale = any(sale.is_overdue for sale in customer_sales)
    all_completed = not customer_sales.exclude(payment_status__in=['completed', 'paid', 'fully-paid']).exists()

    if has_overdue_sale:
        Customer.objects.filter(pk=customer.pk).update(status='overdue')
    elif all_completed and float(total_selling) > 0:
        Customer.objects.filter(pk=customer.pk).update(status='fully-paid')


# Listen for any saved or deleted Payment
# Payment signal disabled - sync handled explicitly in perform_create
# to avoid race condition where signal fires before sale status updates.
# @receiver([post_save, post_delete], sender=Payment)
# def trigger_customer_sync_on_payment(sender, instance, **kwargs):
#     if instance.sale:
#         customer = Customer.objects.filter(phone=instance.sale.phone).first()
#         if customer:
#             sync_single_customer(customer)

# Sale signal disabled - sync handled explicitly in perform_create
# @receiver([post_save, post_delete], sender=Sale)
# def trigger_customer_sync_on_sale(sender, instance, **kwargs):
#     customer = Customer.objects.filter(phone=instance.phone).first()
#     if customer:
#         sync_single_customer(customer)
