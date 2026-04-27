from decimal import Decimal

from django.db import models, transaction
from rest_framework import generics, permissions, status
from django.contrib.auth import get_user_model
from .models import Tool, Payment, Sale, Customer, EquipmentType, Supplier, SaleItem, CodeBatch, ActivationCode, CodeAssignmentLog, BatchSerial, Quotation, DisplayStaff
from .serializers import (
    UserSerializer, ToolSerializer, EquipmentTypeSerializer,
    PaymentSerializer, SaleSerializer, CustomerSerializer, SupplierSerializer, CustomerOwingSerializer,
    CodeBatchSerializer, ActivationCodeSerializer, CodeAssignmentLogSerializer, QuotationSerializer, DisplayStaffSerializer
)
from rest_framework.pagination import PageNumberPagination
from .permissions import IsAdminOrStaff, IsOwnerOrAdmin
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Max, Q, Case, When, F, FloatField
from django.core.mail import send_mail, BadHeaderError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter  
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from datetime import timedelta, datetime
import secrets, uuid, traceback
import json
import pandas as pd
from io import BytesIO
from PIL import Image as PILImage
import csv, io
from django.http import HttpResponse
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as OpenpyxlImage
import base64
import random
from django.db.models.functions import Coalesce
from io import BytesIO
from decimal import Decimal



User = get_user_model()

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

# ----------------------------
# STAFF MANAGEMENT
# ----------------------------
class AddStaffView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)

        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="staff",
            is_active=True,
        )

        try:
            send_mail(
                subject="Your Staff Account Details",
                message=f"Hello {name or 'Staff'},\n\nYour account has been created.\n\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            traceback.print_exc()

        return Response(
            {
                "id": user.id,
                "email": email,
                "name": user.name,
                "phone": user.phone,
                "detail": "Staff created successfully",
            },
            status=status.HTTP_201_CREATED,
        )


class StaffListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(role="staff")


class StaffSalesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        staff_name = request.query_params.get('name', '').strip()
        if not staff_name:
            return Response(
                {"detail": "Staff name query param is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Sale.staff is stored as a plain name string for BOTH
        # registered users and hardcoded staff — so iexact works for both.
        sales = Sale.objects.filter(
            staff__iexact=staff_name
        ).prefetch_related('items').order_by("-date_sold")
        serializer = SaleSerializer(sales, many=True, context={"request": request})
        return Response(serializer.data)

class DisplayStaffListCreateView(generics.ListCreateAPIView):
    serializer_class = DisplayStaffSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DisplayStaff.objects.all()


class DisplayStaffDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DisplayStaffSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = DisplayStaff.objects.all()
    

# ----------------------------
# AUTHENTICATION
# ----------------------------
class EmailLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user or not user.check_password(password):
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response({"detail": "User account is disabled."}, status=status.HTTP_403_FORBIDDEN)

        # Auto-activate customer on first login
        if user.role == "customer":
            try:
                customer = Customer.objects.get(user=user)
                if not customer.is_activated:
                    customer.is_activated = True
                    customer.save()
            except Customer.DoesNotExist:
                pass

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


# ----------------------------
# CUSTOMERS
# ----------------------------
class AddCustomerView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")
        state = request.data.get("state")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)
        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="customer",
            is_active=True,
        )

        Customer.objects.create(
            user=user, name=name, phone=phone, state=state, email=email
        )

        try:
            send_mail(
                subject="Your Customer Account Details",
                message=f"Hello {name or 'Customer'},\n\nAn account has been created for you.\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception as e:
            print("Failed to send email:", e)

        return Response(
            {"id": user.id, "email": email, "name": name, "phone": phone, "state": state},
            status=status.HTTP_201_CREATED,
        )


class CustomerListView(generics.ListAPIView):
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ['name', 'email', 'phone']

    def get_queryset(self):
        return Customer.objects.all().order_by("-id")
    
# ----------------------------
# CUSTOMER OWING/INSTALLMENT TRACKING
# ----------------------------
class CustomerOwingDataView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        try:
            # Get all customers
            customers = Customer.objects.filter(
                status__in=['ongoing', 'overdue']  # <--- Updated to match the new status
            ).exclude(total_selling_price=0)
            
            # Calculate summary statistics
            total_selling_price = sum(customer.total_selling_price for customer in customers)
            total_amount_received = sum(customer.amount_paid for customer in customers)
            total_amount_left = sum(customer.amount_left for customer in customers)
            today = timezone.now().date()
            next_week = today + timedelta(days=7)
            
            upcoming_receivables = sum(
                customer.amount_left for customer in customers 
                if customer.date_next_installment and 
                customer.date_next_installment <= next_week and
                customer.status != 'fully-paid'
            )
            
            # Count overdue customers
            overdue_customers_count = customers.filter(
                status='overdue'
            ).count()
            
            # Prepare summary data
            summary = {
                "totalSellingPrice": float(total_selling_price),
                "totalAmountReceived": float(total_amount_received),
                "totalAmountLeft": float(total_amount_left),
                "upcomingReceivables": float(upcoming_receivables),
                "overdueCustomers": overdue_customers_count,
                "totalCustomers": customers.count()
            }
            
            # Serialize customer data
            customers_data = CustomerOwingSerializer(customers, many=True).data
            
            response_data = {
                "summary": summary,
                "customers": customers_data
            }
            
            return Response(response_data)
            
        except Exception as e:
            print(f"Error in CustomerOwingDataView: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Failed to fetch customer owing data"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class SyncCustomerFinancialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from decimal import Decimal
        try:
            phone_filter = request.query_params.get('phone', '').strip()
            if phone_filter:
                customers = Customer.objects.filter(phone=phone_filter)
            else:
                customers = Customer.objects.all()
            synced = 0
            skipped = 0

            for customer in customers:
                if customer.phone:
                    customer_sales = Sale.objects.exclude(payment_status='pending').filter(phone=customer.phone)
                elif customer.name:
                    customer_sales = Sale.objects.exclude(payment_status='pending').filter(name__iexact=customer.name)
                else:
                    skipped += 1
                    continue

                if not customer_sales.exists():
                    customer.total_selling_price = Decimal('0')
                    customer.amount_paid = Decimal('0')
                    customer.date_next_installment = None
                    customer.save()
                    skipped += 1
                    continue

                total_selling = customer_sales.aggregate(
                    total=Sum('total_cost')
                )['total'] or Decimal('0')

                amount_paid = Decimal('0')
                for sale in customer_sales:
                    sale_status = (sale.payment_status or '').lower()
                    if sale_status in ['completed', 'paid', 'fully-paid']:
                        amount_paid += Decimal(str(sale.total_cost or '0'))
                    elif sale_status in ['ongoing', 'overdue', 'installment']:
                        initial = Decimal(str(sale.initial_deposit or '0'))
                        logged = Payment.objects.filter(
                            sale=sale
                        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        amount_paid += initial + Decimal(str(logged))

                next_due = customer_sales.exclude(
                    payment_status__in=['completed', 'paid', 'fully-paid']
                ).exclude(
                    due_date__isnull=True
                ).order_by('-date_sold').values_list('due_date', flat=True).first()

                latest_payment = Payment.objects.filter(
                    sale__in=customer_sales
                ).order_by('-payment_date').first()

                if latest_payment and latest_payment.payment_date:
                    customer.date_last_paid = (
                        latest_payment.payment_date.date()
                        if hasattr(latest_payment.payment_date, 'date')
                        else latest_payment.payment_date
                    )
                else:
                    completed = customer_sales.filter(
                        payment_status__in=['completed', 'paid', 'fully-paid']
                    ).order_by('-date_sold').first()
                    if completed:
                        customer.date_last_paid = completed.date_sold

                customer.total_selling_price = total_selling
                customer.amount_paid = amount_paid
                customer.date_next_installment = next_due
                customer.save()

                # ── OVERDUE LOGIC ──────────────────────────────────────
                today = timezone.now().date()

                fresh_sales = list(
                    Sale.objects.exclude(payment_status='pending').filter(phone=customer.phone)
                    if customer.phone
                    else Sale.objects.exclude(payment_status='pending').filter(name__iexact=customer.name)
                )

                active_sales = [
                    s for s in fresh_sales
                    if (s.payment_status or '').lower()
                    not in ['completed', 'paid', 'fully-paid']
                ]

                for sale in active_sales:
                    # Rule 1: due_date passed — permanent overdue
                    if sale.due_date and today >= sale.due_date:
                        Sale.objects.filter(pk=sale.pk).update(
                            payment_status='overdue'
                        )
                        continue

                    # Rule 2: 90-day inactivity
                    last_payment = Payment.objects.filter(
                        sale=sale
                    ).order_by('-payment_date').first()

                    if last_payment and last_payment.payment_date:
                        last_activity = (
                            last_payment.payment_date.date()
                            if hasattr(last_payment.payment_date, 'date')
                            else last_payment.payment_date
                        )
                    else:
                        last_activity = sale.date_sold

                    days_inactive = (
                        (today - last_activity).days
                        if last_activity else 0
                    )

                    if days_inactive >= 90:
                        print(f"DEBUG: Sale {sale.pk} OVERDUE — {days_inactive} days inactive, date_sold={sale.date_sold}, last_activity={last_activity}")
                        Sale.objects.filter(pk=sale.pk).update(payment_status='overdue')
                    else:
                        print(f"DEBUG: Sale {sale.pk} ONGOING — {days_inactive} days inactive, date_sold={sale.date_sold}, last_activity={last_activity}")
                        Sale.objects.filter(pk=sale.pk).update(payment_status='ongoing')

                    if customer.phone:
                        customer_sales = Sale.objects.exclude(payment_status='pending').filter(phone=customer.phone)
                    else:
                        customer_sales = Sale.objects.exclude(payment_status='pending').filter(name__iexact=customer.name)

                    has_overdue = customer_sales.filter(
                        payment_status='overdue'
                    ).exists()

                    # ADD THIS LINE
                    print(f"DEBUG CUSTOMER: {customer.name} | phone={customer.phone} | has_overdue={has_overdue} | sales_statuses={list(customer_sales.values_list('id', 'payment_status'))}")

                    all_completed = not customer_sales.exclude(
                        payment_status__in=['completed', 'paid', 'fully-paid']
                    ).exists()

                    if has_overdue:
                        Customer.objects.filter(pk=customer.pk).update(status='overdue')
                    elif all_completed and float(total_selling) > 0:
                        Customer.objects.filter(pk=customer.pk).update(status='fully-paid')

                # Re-fetch after updates
                if customer.phone:
                    customer_sales = Sale.objects.filter(phone=customer.phone)
                else:
                    customer_sales = Sale.objects.filter(name__iexact=customer.name)

                has_overdue = customer_sales.filter(
                    payment_status='overdue'
                ).exists()

                all_completed = not customer_sales.exclude(
                    payment_status__in=['completed', 'paid', 'fully-paid']
                ).exists()

                if has_overdue:
                    Customer.objects.filter(pk=customer.pk).update(status='overdue')
                elif all_completed and float(total_selling) > 0:
                    Customer.objects.filter(pk=customer.pk).update(status='fully-paid')

                synced += 1

            return Response({
                "success": True,
                "synced": synced,
                "skipped": skipped,
                "message": f"Synced {synced} customers, {skipped} had no sales."
            }, status=status.HTTP_200_OK)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Sync failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# class SimulateInactivityView(APIView):
#     """
#     POST /debug/simulate-inactivity/
#     TEST ONLY — simulates 90+ day inactivity on a sale.
#     Remove this endpoint before going to production.

#     Body: { "sale_id": 6, "days_inactive": 91 }
#     """
#     permission_classes = [permissions.IsAuthenticated]

#     def post(self, request):
#         from datetime import timedelta
#         sale_id = request.data.get('sale_id')
#         days_inactive = int(request.data.get('days_inactive', 91))

#         if not sale_id:
#             return Response({"error": "sale_id required"}, status=400)

#         try:
#             sale = Sale.objects.get(pk=sale_id)

#             # 1. Backdate date_sold
#             fake_date = timezone.now().date() - timedelta(days=days_inactive)
#             Sale.objects.filter(pk=sale_id).update(date_sold=fake_date)

#             # 2. Delete all Payment records for this sale
#             # so last_activity falls back to date_sold
#             deleted_count, _ = Payment.objects.filter(sale=sale).delete()

#             return Response({
#                 "success": True,
#                 "sale_id": sale_id,
#                 "date_sold_set_to": str(fake_date),
#                 "payments_deleted": deleted_count,
#                 "message": f"Sale {sale_id} now has {days_inactive} days of inactivity. Click Sync Data to trigger overdue."
#             })

#         except Sale.DoesNotExist:
#             return Response({"error": f"Sale {sale_id} not found"}, status=404)

# class ResetSaleView(APIView):
#     """
#     POST /debug/reset-sale/
#     TEST ONLY — resets a sale's date_sold back to today.
#     """
#     permission_classes = [permissions.IsAuthenticated]

#     def post(self, request):
#         sale_id = request.data.get('sale_id')
#         if not sale_id:
#             return Response({"error": "sale_id required"}, status=400)
#         try:
#             Sale.objects.filter(pk=sale_id).update(
#                 date_sold=timezone.now().date(),
#                 payment_status='ongoing'
#             )
#             return Response({"success": True, "message": f"Sale {sale_id} reset to today."})
#         except Sale.DoesNotExist:
#             return Response({"error": f"Sale {sale_id} not found"}, status=404)

# ----------------------------
# TOOLS
# ----------------------------

class ToolListCreateView(generics.ListCreateAPIView):
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Tool.objects.select_related("supplier").order_by("-date_added")

        # Filter by category and equipment type if provided
        category = self.request.query_params.get('category')
        equipment_type = self.request.query_params.get('equipment_type')
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type:
            # For Receiver category, filter by description/box_type based on equipment_type
            if category == "Receiver":
                if equipment_type == "Base Only":
                    queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
                elif equipment_type == "Rover Only":
                    queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
                elif equipment_type == "Base & Rover Combo":
                    queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
                elif equipment_type == "Accessories":
                    queryset = queryset.filter(description__icontains="accessory")

        if getattr(user, "role", None) == "customer":
            queryset = queryset.filter(stock__gt=0, is_enabled=True)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, "role", None) == "customer":
            raise permissions.PermissionDenied("Customers cannot add tools.")
        
        # Initialize available_serials with serials if not provided
        tool_data = serializer.validated_data
        if 'available_serials' not in tool_data or not tool_data['available_serials']:
            if 'serials' in tool_data and tool_data['serials']:
                tool_data['available_serials'] = tool_data['serials'].copy()
                
        serializer.save()

# NEW: Get tools grouped by name for frontend display
class ToolGroupedListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = self.request.user
        category = request.query_params.get('category')
        equipment_type = request.query_params.get('equipment_type')
        
        # Start with base queryset
        queryset = Tool.objects.filter(stock__gt=0, is_enabled=True)
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type and category == "Receiver":
            # Apply equipment type filtering for Receiver category
            if equipment_type == "Base Only":
                queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
            elif equipment_type == "Rover Only":
                queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
            elif equipment_type == "Base & Rover Combo":
                queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
            elif equipment_type == "Accessories":
                queryset = queryset.filter(description__icontains="accessory")
        
        # Group tools by name and calculate total stock
        from django.db.models import Sum, Count
        grouped_tools = queryset.values('name', 'category', 'cost').annotate(
            total_stock=Sum('stock'),
            tool_count=Count('id'),
            available_serials_count=Sum('stock')  # Assuming each stock item has one serial
        ).order_by('name')
        
        # Convert to list and add additional info
        result = []
        for tool_group in grouped_tools:
            # Get one sample tool for additional fields
            sample_tool = queryset.filter(name=tool_group['name']).first()
            if sample_tool:
                result.append({
                    'name': tool_group['name'],
                    'category': tool_group['category'],
                    'cost': tool_group['cost'],
                    'total_stock': tool_group['total_stock'],
                    'tool_count': tool_group['tool_count'],
                    'description': sample_tool.description,
                    'supplier_name': sample_tool.supplier.name if sample_tool.supplier else None,
                    'group_id': f"group_{tool_group['name'].replace(' ', '_').lower()}"
                })
        
        return Response(result)


class ToolAssignRandomFromGroupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        tool_name = request.data.get('tool_name')
        category = request.data.get('category')
        requested_type = request.data.get('equipment_type', "").lower()
        
        if not tool_name:
            return Response({"error": "Tool name is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            items = list(Tool.objects.select_for_update().filter(
                name=tool_name, 
                category=category,
                stock__gt=0,
                is_enabled=True
            ))

            # ✅ HANDLE ACCESSORIES
            if category == "Accessory":
                if not items:
                    return Response({"error": f"No {tool_name} available."}, status=404)
                
                selected_tool = random.choice(items)
                
                if selected_tool.available_serials and len(selected_tool.available_serials) > 0:
                    serial = selected_tool.available_serials.pop(0)
                    selected_tool.stock -= 1
                    selected_tool.save()
                    
                    return Response({
                        "assigned_tool_id": selected_tool.id,
                        "tool_name": selected_tool.name,
                        "serial_set": [serial],
                        "serial_count": 1,
                        "set_type": "Accessory",
                        "cost": str(selected_tool.cost),
                        "description": selected_tool.description or "Accessory",
                        "remaining_stock": selected_tool.stock,
                        "import_invoice": selected_tool.invoice_number,
                        "datalogger_serial": None,
                        "external_radio_serial": None
                    })
                else:
                    return Response({"error": f"{tool_name} has no serials."}, status=404)

            # ✅ HANDLE RECEIVERS (Base, Rover, Combo)
            wants_combo = "combo" in requested_type or "base & rover" in requested_type
            selected_tool = None
            valid_serial_set = None
            
            random.shuffle(items) 

            for tool in items:
                needed_count = tool.get_serial_set_count()
                
                # STRICT UNIQUE SELECTION LOGIC
                # If combo is selected, it ONLY looks for tools with 4 or more serials.
                # If base/rover is selected, it ONLY looks for tools with less than 4 serials.
                if wants_combo:
                    if needed_count < 4: continue
                else:
                    if needed_count >= 4: continue

                if len(tool.available_serials or []) >= needed_count:
                    selected_tool = tool
                    valid_serial_set = tool.get_random_serial_set()
                    break

            if selected_tool and valid_serial_set:
                # Clean spaces but DO NOT pluck anything out. Send the full array.
                clean_serials = [s.strip() for s in valid_serial_set if s.strip()]

                return Response({
                    "assigned_tool_id": selected_tool.id,
                    "tool_name": selected_tool.name,
                    "serial_set": clean_serials,  # ✅ Full array (4 for combo, 2 for single)
                    "serial_count": len(clean_serials),
                    "set_type": selected_tool.description,
                    "cost": str(selected_tool.cost),
                    "description": selected_tool.description,
                    "remaining_stock": selected_tool.stock,
                    "import_invoice": selected_tool.invoice_number,
                    "datalogger_serial": None,
                    "external_radio_serial": None 
                })

        msg = f"Inventory Error: No available {tool_name} sets found for the selected type."
        return Response({"error": msg}, status=status.HTTP_404_NOT_FOUND)


# NEW: Support for restoring serials if the user cancels or removes an item
class ToolRestoreSerialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        tool_id = request.data.get('tool_id')
        serial_set = request.data.get('serial_set')
        
        if not tool_id or not serial_set:
            return Response({"error": "Missing data"}, status=400)
            
        tool = get_object_or_404(Tool, id=tool_id)
        tool.restore_serials(serial_set)
        
        return Response({"message": "Stock restored successfully", "new_stock": tool.stock})


# NEW: Get random serial number for a tool
class ToolGetRandomSerialView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        try:
            tool = get_object_or_404(Tool, pk=pk)
            
            if not tool.available_serials:
                return Response(
                    {"error": "No available serial numbers for this tool."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            random_serial = tool.get_random_serial()
            
            if not random_serial:
                return Response(
                    {"error": "Failed to get random serial number."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            return Response({
                "serial_number": random_serial,
                "tool_name": tool.name,
                "remaining_serials": len(tool.available_serials)
            })
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
class ToolDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tool.objects.all()
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]


class ToolSoldSerialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        tool = get_object_or_404(Tool, pk=pk)
        
        sold_serials = []
        for serial_info in tool.sold_serials or []:
            if isinstance(serial_info, dict):
                sold_serials.append({
                    'serial': serial_info.get('serial', 'Unknown'),
                    'sale_id': serial_info.get('sale_id'),
                    'customer_name': serial_info.get('customer_name', 'Unknown'),
                    'date_sold': serial_info.get('date_sold'),
                    'invoice_number': serial_info.get('invoice_number'),
                    'import_invoice': serial_info.get('import_invoice')  # NEW: Add import_invoice
                })
            else:
                # Handle case where serial_info is just a string
                sold_serials.append({
                    'serial': serial_info,
                    'sale_id': None,
                    'customer_name': 'Unknown',
                    'date_sold': None,
                    'invoice_number': None,
                    'import_invoice': None  # NEW: Add import_invoice
                })
                
        return Response(sold_serials)

# ----------------------------
# EQUIPMENT TYPE
# ----------------------------

class EquipmentTypeListView(generics.ListCreateAPIView):
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = EquipmentType.objects.all().order_by("category", "name")
        
        # Filter by invoice_number if provided
        invoice_number = self.request.query_params.get('invoice_number')
        if invoice_number:
            queryset = queryset.filter(invoice_number=invoice_number)
        
        # Filter by category if provided
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
            
        return queryset

class EquipmentTypeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = EquipmentType.objects.all()
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

# NEW VIEW: Get equipment grouped by invoice
@api_view(['GET'])
def equipment_by_invoice(request):
    """
    Get equipment types grouped by invoice number with counts and totals
    """
    from django.db.models import F, FloatField
    from django.db.models.functions import Cast
    
    invoices = EquipmentType.objects.exclude(invoice_number__isnull=True)\
        .exclude(invoice_number__exact='')\
        .values('invoice_number')\
        .annotate(
            equipment_count=Count('id'),
            total_value=Sum('default_cost'),
            last_updated=Max('created_at')
        )\
        .order_by('-last_updated')
    
    return Response(list(invoices))

#-------------------
# SUPPLIERS
#--------------------
class SupplierListView(generics.ListCreateAPIView):
    queryset = Supplier.objects.all().order_by("name")
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]

class SupplierDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]


class SaleListCreateView(generics.ListCreateAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        user = self.request.user
        today = timezone.localdate()
        
        # ✅ CHANGED: Now exactly 90 days (3 months)
        ninety_days_ago = today - timedelta(days=90)
        
        # Exclude drafts (pending) from default view unless explicitly requested
        show_drafts = self.request.query_params.get('status', '').lower() == 'pending'

        show_drafts = self.request.query_params.get('status', '').lower() == 'pending'

        queryset = Sale.objects.prefetch_related('items').annotate(
            last_payment_date=Max('payment__payment_date')
        ).order_by("-date_sold", "-id")

        if not show_drafts:
            queryset = queryset.exclude(payment_status='pending')

        if not show_drafts:
            queryset = queryset.exclude(payment_status='pending')       
        
        # --- 1. Role-Based Filtering ---
        if user.is_superuser or user.is_staff or getattr(user, 'role', '') == "admin":
            pass 
        elif getattr(user, 'role', '') == "staff":
            staff_name = user.get_full_name() or user.username
            queryset = queryset.filter(staff=staff_name)
        elif getattr(user, 'role', '') == "customer":
            query = Q()
            if user.name: query |= Q(name__iexact=user.name)
            if user.phone: query |= Q(phone=user.phone)
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name: query |= Q(name__iexact=user.customer.name)
                if user.customer.phone: query |= Q(phone=user.customer.phone)
            
            if query:
                queryset = queryset.filter(query)
            else:
                return Sale.objects.none()
        else:
            return Sale.objects.none()

        # --- 2. URL Parameter Filtering ---
        search_query = self.request.query_params.get('search', '').strip()
        staff_filter = self.request.query_params.get('staff_id') 
        status_filter = self.request.query_params.get('status') 
        start_date    = self.request.query_params.get('start_date', '').strip()
        end_date      = self.request.query_params.get('end_date', '').strip()

        if staff_filter:
            queryset = queryset.filter(staff__icontains=staff_filter)

        if start_date:
            try:
                from datetime import datetime
                # Handle both DD-MM-YYYY (from DB display) and YYYY-MM-DD (from date picker)
                if len(start_date) == 10 and start_date[2] == '-':
                    parsed_start = datetime.strptime(start_date, "%d-%m-%Y").date()
                else:
                     parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
                queryset = queryset.filter(date_sold__gte=parsed_start)
            except (ValueError, AttributeError):
                pass

        if end_date:
            try:
                from datetime import datetime
                if len(end_date) == 10 and end_date[2] == '-':
                    parsed_end = datetime.strptime(end_date, "%d-%m-%Y").date()
                else:
                    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
                queryset = queryset.filter(date_sold__lte=parsed_end)
            except (ValueError, AttributeError):
                pass

        # ✅ NEW: 90-DAY LIVE STATUS FILTERING
        if status_filter == 'overdue':
            queryset = queryset.filter(
                ~Q(payment_status__in=['completed', 'paid', 'fully-paid']) & 
                (
                    Q(payment_status__iexact='overdue') | 
                    Q(due_date__lt=today) | 
                    (Q(last_payment_date__isnull=False) & Q(last_payment_date__lt=ninety_days_ago)) |
                    (Q(last_payment_date__isnull=True) & Q(date_sold__lt=ninety_days_ago))
                )
            )
        elif status_filter:
            queryset = queryset.filter(payment_status__iexact=status_filter)

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(invoice_number__icontains=search_query) |
                Q(items__equipment__icontains=search_query)
            ).distinct()

        return queryset
    
    def list(self, request, *args, **kwargs):
        # 1. Get the filtered list of sales 
        queryset = self.filter_queryset(self.get_queryset())

        # 2. Calculate summary stats
        from django.db.models import Sum
        
        total_revenue = queryset.filter(
            payment_status__in=['ongoing', 'completed']
        ).aggregate(Sum('total_cost'))['total_cost__sum'] or 0
        
        ongoing_sales = queryset.filter(payment_status='ongoing').count()
        overdue_sales = queryset.filter(payment_status='overdue').count()
        completed_sales = queryset.filter(payment_status='completed').count()

        summary_data = {
            "total_revenue": total_revenue,
            "ongoing_sales": ongoing_sales,
            "overdue_sales": overdue_sales,
            "completed_sales": completed_sales,
        }

        # 3. Handle Pagination and inject the summary
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            
            # INJECT THE SUMMARY INTO THE JSON RESPONSE
            response.data['summary'] = summary_data
            return response

        # Fallback if pagination is turned off
        from rest_framework.response import Response
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "summary": summary_data,
            "results": serializer.data
        })

    def perform_create(self, serializer):
        # 1. Get the raw data from the request
        staff_from_form = self.request.data.get('staff')
        import_invoice = self.request.data.get('import_invoice')

        # 2. Get payment plan
        payment_plan = self.request.data.get('payment_plan', 'No')

        # 3. Apply status rules
        explicit_status = str(self.request.data.get('payment_status', '')).strip().lower()
        if explicit_status == 'pending':
            new_status = 'pending'
        elif payment_plan == 'Yes':
            new_status = 'ongoing'
        else:
            new_status = 'completed'

        # 4. Look up customer email by phone to store on the Sale record
        phone_from_form = self.request.data.get('phone', '')
        customer_email = ''
        if phone_from_form:
            cust = Customer.objects.filter(phone=phone_from_form).first()
            if cust:
                customer_email = cust.email or ''

        # 5. Save with staff, status and email
        if staff_from_form and str(staff_from_form).strip() not in ["", "null", "undefined"]:
            serializer.save(
                staff=str(staff_from_form),
                import_invoice=import_invoice,
                payment_status=new_status,
                email=customer_email,
            )
        else:
            user = self.request.user
            fallback_name = getattr(user, 'name', None) or getattr(user, 'username', 'Admin')
            serializer.save(
                staff=fallback_name,
                import_invoice=import_invoice,
                payment_status=new_status,
                email=customer_email,
            )

        # 6. Handle Items
        sale = serializer.instance
        items_raw_data = self.request.data.get('items', [])
        for i, item in enumerate(sale.items.all()):
            if i < len(items_raw_data):
                data = items_raw_data[i]
                serial_set = data.get('serial_set')
                if serial_set and isinstance(serial_set, list):
                    import json
                    item.serial_number = serial_set[0] if len(serial_set) == 1 else json.dumps(serial_set)
                if import_invoice:
                    item.import_invoice = import_invoice
                item.save()


class SaleDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, 'role', '')
        
        # ✅ FIX 1: Exact match with your List View! (Added user.is_staff)
        if user.is_superuser or user.is_staff or role == "admin":
            return Sale.objects.all()
            
        elif role == "staff":
            staff_name = getattr(user, 'name', None) or getattr(user, 'username', '')
            return Sale.objects.filter(staff=staff_name)
            
        elif role == "customer":
            query = Q()
            if getattr(user, 'name', None):
                query |= Q(name__iexact=user.name)
            if getattr(user, 'phone', None):
                query |= Q(phone=user.phone)
                
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name:
                    query |= Q(name__iexact=user.customer.name)
                if user.customer.phone:
                    query |= Q(phone=user.customer.phone)
            
            if query:
                return Sale.objects.filter(query)
                
        return Sale.objects.none()

    def perform_destroy(self, instance):
        """
        When a draft (pending) sale is deleted, restore all assigned
        serials back to inventory before removing the sale record.
        """
        if instance.payment_status == 'pending':
            import json
            for item in instance.items.all():
                if not item.serial_number:
                    continue
                # Parse serial_number — could be a single serial or JSON array
                try:
                    serial_set = json.loads(item.serial_number)
                    if not isinstance(serial_set, list):
                        serial_set = [item.serial_number]
                except (json.JSONDecodeError, TypeError):
                    serial_set = [item.serial_number]

                # Find the tool and restore the serials
                if item.tool:
                    try:
                        item.tool.restore_serials(serial_set)
                    except Exception as e:
                        print(f"Failed to restore serials for item {item.id}: {e}")

        instance.delete()

    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
        role = getattr(user, 'role', '')
        
        # ✅ FIX 2: Only block the update if they are strictly a normal staff member
        if role == "staff" and not (user.is_superuser or user.is_staff or role == "admin"):
            staff_name = getattr(user, 'name', None) or getattr(user, 'username', '')
            if instance.staff != staff_name:
                raise PermissionDenied("You can only edit your own sales.")
                
        return super().perform_update(serializer)

    def partial_update(self, request, *args, **kwargs):
        # 1. Force the database to update the raw field directly, bypassing any model save() rules!
        if 'payment_status' in request.data:
            from django.db.models import F
            # This writes straight to the database instantly
            Sale.objects.filter(pk=self.kwargs['pk']).update(payment_status=request.data['payment_status'])
            
        # 2. Continue with the rest of the normal update process
        return super().partial_update(request, *args, **kwargs)

# ----------------------------
# EMAIL API
# ----------------------------
@api_view(["POST"])
@permission_classes([AllowAny])  
def send_sale_email(request):
    try:
        data = request.data
        send_mail(
            subject=data.get("subject", "Your Payment Link"),
            message=data.get("message", "Hello, your payment link will be available soon."),
            from_email="runocole@gmail.com",  
            recipient_list=[data.get("to_email")],
            fail_silently=False,
        )
        return Response({"message": "Email sent successfully!"})
    except Exception as e:
        return Response({"error": str(e)}, status=500)
    

# ----------------------------
# QUOTATIONS
# ----------------------------
class QuotationListCreateView(generics.ListCreateAPIView):
    serializer_class = QuotationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = Quotation.objects.all()
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(quote_number__icontains=search) |
                Q(phone__icontains=search)
            )
        return qs


class QuotationDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = QuotationSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Quotation.objects.all()


class QuotationConvertView(APIView):
    """
    POST /api/quotations/<pk>/convert/
    Converts a quotation to a real Sale — no inventory deduction,
    just creates the Sale record so staff can then assign serials normally.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            quotation = Quotation.objects.get(pk=pk)
        except Quotation.DoesNotExist:
            return Response(
                {"error": "Quotation not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if quotation.is_converted:
            return Response(
                {"error": "This quotation has already been converted to a sale."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Determine due date from payment months
        due_date = None
        if quotation.payment_months:
            from datetime import date
            from dateutil.relativedelta import relativedelta
            due_date = date.today() + relativedelta(months=quotation.payment_months)

        # Determine status
        if quotation.payment_plan == 'Yes':
            sale_status = 'ongoing'
        else:
            sale_status = 'completed'

        # Create the Sale
        import random, string
        invoice_number = f"INV-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

        sale = Sale.objects.create(
            name=quotation.name,
            phone=quotation.phone or '',
            state=quotation.state or '',
            staff=quotation.staff or '',
            total_cost=quotation.total_cost,
            tax_amount=quotation.tax_amount,
            payment_plan=quotation.payment_plan,
            initial_deposit=quotation.initial_deposit,
            payment_months=quotation.payment_months,
            due_date=due_date,
            payment_status=sale_status,
            invoice_number=invoice_number,
            date_sold=date.today(),
        )

        # Create SaleItems from QuotationItems (no serial assignment)
        for qitem in quotation.items.all():
            for _ in range(qitem.quantity):
                SaleItem.objects.create(
                    sale=sale,
                    equipment=qitem.equipment,
                    equipment_type=qitem.equipment_type or '',
                    category=qitem.category or '',
                    cost=qitem.cost,
                )

        # Mark quotation as converted
        quotation.is_converted = True
        quotation.converted_sale_id = sale.id
        quotation.save()

        return Response({
            "success": True,
            "sale_id": sale.id,
            "invoice_number": sale.invoice_number,
            "message": f"Quotation {quotation.quote_number} converted to sale {sale.invoice_number}.",
        }, status=status.HTTP_201_CREATED)


class QuotationSendEmailView(APIView):
    """
    POST /api/quotations/<pk>/send-email/
    Sends the quotation summary to the customer email.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            quotation = Quotation.objects.get(pk=pk)
        except Quotation.DoesNotExist:
            return Response({"error": "Quotation not found."}, status=404)

        if not quotation.email:
            return Response(
                {"error": "No email address on this quotation."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from django.core.mail import send_mail
            items_text = "\n".join([
                f"  - {item.equipment} ({item.equipment_type or 'N/A'}) x{item.quantity} — NGN {item.cost:,.2f}"
                for item in quotation.items.all()
            ])
            body = f"""Dear {quotation.name},

Please find your quotation details below:

Quotation Number: {quotation.quote_number}
Date: {quotation.date_created}
Valid Until: {quotation.valid_until or 'N/A'}

Items:
{items_text}

Total: NGN {quotation.total_cost:,.2f}
{f'Initial Deposit: NGN {quotation.initial_deposit:,.2f}' if quotation.initial_deposit else ''}
{f'Payment Duration: {quotation.payment_months} months' if quotation.payment_months else ''}

{quotation.notes or ''}

Bank: Zenith Bank
Account Name: OTIC GEOSYSTEMS LTD
Account NO: 1015175251

Thank you for your interest.

OTIC GEOSYSTEMS LTD
"""
            send_mail(
                subject=f"Quotation {quotation.quote_number} from OTIC GEOSYSTEMS LTD",
                message=body,
                from_email=None,
                recipient_list=[quotation.email],
                fail_silently=False,
            )
            return Response({"success": True, "message": f"Quotation sent to {quotation.email}."})
        except Exception as e:
            return Response(
                {"error": f"Failed to send email: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        

# ----------------------------
# DASHBOARD SUMMARY
# ----------------------------
class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        total_sales = Sale.objects.exclude(payment_status='pending').count()
        total_revenue = Sale.objects.exclude(payment_status='pending').aggregate(total=Sum("total_cost"))["total"] or 0
        tools_count = Tool.objects.filter(stock__gt=0).count()
        staff_count = User.objects.filter(role="staff").count()
        active_customers = Sale.objects.exclude(payment_status='pending').filter(payment_plan="Yes").values('phone').distinct().count()

        today = timezone.now()
        month_start = today.replace(day=1)
        mtd_revenue = (
            Sale.objects.exclude(payment_status='pending').filter(date_sold__gte=month_start)
            .aggregate(total=Sum("total_cost"))
            .get("total")
            or 0
        )

        inventory_breakdown = []
        receiver_tools_breakdown = (
            Tool.objects
            .filter(category="Receiver", stock__gt=0)  
            .values("name")
            .annotate(count=Count("id"))
            .order_by("name")
        )
        
        for item in receiver_tools_breakdown:
            inventory_breakdown.append({
                "receiver_type": item["name"],
                "count": item["count"]
            })

        if not inventory_breakdown:
            inventory_breakdown.append({
                "receiver_type": "No receiver tools",
                "count": 0
            })

        # ✅ FIXED: Low stock should only show items that actually have stock
        low_stock_items = list(
            Tool.objects.values("name", "category")
            .annotate(total_stock=Sum("stock"))
            .filter(total_stock__gt=0, total_stock__lt=5)
            .order_by("total_stock")[:10]
        )
        # Rename total_stock to stock so frontend receives consistent field name
        for item in low_stock_items:
            item["stock"] = item.pop("total_stock")

        # Top selling tools
        top_selling_tools = (
            SaleItem.objects.values("tool__name")
            .annotate(total_sold=Count("id"))
            .order_by("-total_sold")[:5]
        )

        # Recent sales
        recent_sales = Sale.objects.exclude(payment_status='pending').prefetch_related('items').order_by('-date_sold')[:10]
        recent_sales_data = []
        for sale in recent_sales:
            first_item = sale.items.first()
            tool_name = first_item.equipment if first_item else "No equipment"
            
            recent_sales_data.append({
                'invoice_number': sale.invoice_number,
                'customer_name': sale.name,
                'tool_name': tool_name,
                'cost_sold': sale.total_cost,
                'payment_status': sale.payment_status,
                'date_sold': sale.date_sold,
                'import_invoice': sale.import_invoice  # NEW: Add import_invoice to recent sales
            })

        # Expiring receivers - only show items with stock
        thirty_days_from_now = timezone.now() + timedelta(days=30)
        expiring_codes = (
            ActivationCode.objects
            .filter(
                expiry_date__isnull=False,
                expiry_date__gt=timezone.now(),
                expiry_date__lte=thirty_days_from_now,
                status__in=["assigned", "activated"]
            )
            .order_by("expiry_date")[:10]
        )

        expiring_receivers_data = []
        for code in expiring_codes:
            expiring_receivers_data.append({
                "name": code.receiver_serial or "Unknown",
                "serialNumber": code.receiver_serial or "—",
                "expirationDate": code.expiry_date.isoformat() if code.expiry_date else None,
            })

        return Response(
            {
                "totalTools": tools_count,
                "totalStaff": staff_count,
                "activeCustomers": active_customers,
                "mtdRevenue": mtd_revenue,
                "inventoryBreakdown": inventory_breakdown,
                "lowStockItems": low_stock_items,
                "topSellingTools": list(top_selling_tools),
                "recentSales": recent_sales_data,
                "expiringReceivers": expiring_receivers_data,
            }
        )

class MonthlyRevenueView(APIView):
    """
    GET /dashboard/monthly-revenue/

    Returns revenue grouped by month across all time.
    Used by the dashboard Revenue card modal to show
    month-by-month breakdown with running totals.

    Response shape:
    {
        "months": [
            {
                "month": "January 2025",
                "year": 2025,
                "month_number": 1,
                "revenue": 3800000.00,
                "sales_count": 4
            },
            ...
        ],
        "total_all_time": 15200000.00,
        "total_sales_count": 18
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models.functions import TruncMonth
        from django.db.models import Sum, Count
        import calendar

        # Group all sales by month
        monthly_data = (
            Sale.objects.exclude(payment_status='pending')
            .annotate(month=TruncMonth('date_sold'))
            .values('month')
            .annotate(
                revenue=Sum('total_cost'),
                sales_count=Count('id')
            )
            .order_by('-month')  # Most recent first
        )

        months_list = []
        for entry in monthly_data:
            if entry['month']:
                month_name = entry['month'].strftime('%B %Y')  # e.g. "March 2026"
                months_list.append({
                    'month': month_name,
                    'year': entry['month'].year,
                    'month_number': entry['month'].month,
                    'revenue': float(entry['revenue'] or 0),
                    'sales_count': entry['sales_count'] or 0,
                })

        total_all_time = Sale.objects.exclude(payment_status='pending').aggregate(
            total=Sum('total_cost')
        )['total'] or 0

        total_sales_count = Sale.objects.exclude(payment_status='pending').count()

        return Response({
            'months': months_list,
            'total_all_time': float(total_all_time),
            'total_sales_count': total_sales_count,
        })
# ----------------------------
# PAYMENTS
# ----------------------------
class PaymentListCreateView(generics.ListCreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    # ── THE FIX: Auto-update the Sale when a Payment is logged ──
    def perform_create(self, serializer):
        from django.db.models import Sum # Make sure this is imported at the top of your file!

        # 1. Save the new payment log to the database
        payment = serializer.save()

        # 2. If this payment is attached to a Sale, update the Sale's status
        if payment.sale:
            sale = payment.sale
            
            # Safely get the base numbers
            initial_deposit = float(sale.initial_deposit or 0.0)
            total_cost = float(sale.total_cost or 0.0)
            
            # Sum up ALL payment logs attached to this sale (including the one we just saved)
            logged_payments_sum = sale.payment_set.aggregate(total=Sum('amount'))['total'] or 0.0
            
            # Calculate true total paid: Initial Deposit + All Subsequent Logged Payments
            new_total_paid = initial_deposit + float(logged_payments_sum)
            
            # 🚨 NOTICE: We completely removed `sale.initial_deposit = str(new_total_paid)`!
            # The initial_deposit remains untouched forever.

            # 3. Automatically update the status based on the new true balance
            today = timezone.localdate()
            
            if new_total_paid >= total_cost:
                sale.payment_status = 'completed'
            elif sale.due_date and today >= sale.due_date:
                # ABSOLUTE OVERDUE RULE
                sale.payment_status = 'overdue'
            else:
                # ONGOING RULE
                sale.payment_status = 'ongoing'
                
            # Save the status changes to the Sale!
            sale.save()

            # EXPLICIT SYNC: Force customer update after every payment
            try:
                from inventory.models import Customer, sync_single_customer
                customer = Customer.objects.filter(phone=sale.phone).first()
                if customer:
                    sync_single_customer(customer)
            except Exception as e:
                print(f'Customer sync error: {e}')

class PaymentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [IsOwnerOrAdmin]


class PaymentSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        search_query = request.query_params.get('search', '').strip()
        status_filter = request.query_params.get('status', 'all').lower()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        today = timezone.localdate()
        from datetime import timedelta, datetime

        # ✅ CHANGED TO 90 DAYS
        ninety_days_ago = today - timedelta(days=90)

        # ✅ ANNOTATE WITH LAST PAYMENT DATE
        # Exclude pending (draft) sales from all calculations and display
        all_sales = Sale.objects.exclude(
            payment_status='pending'
        ).annotate(
            last_payment_date=Max('payment__payment_date')
        )

        safe_cost = Coalesce(F('total_cost'), 0.0, output_field=FloatField())
        safe_deposit = Coalesce(F('initial_deposit'), 0.0, output_field=FloatField())

        # ✅ SYNCED OVERDUE LOGIC
        is_overdue_logic = (
            ~Q(payment_status__in=['completed', 'paid', 'fully-paid']) &
            (
                Q(payment_status__iexact='overdue') |
                Q(due_date__lt=today) |
                (Q(last_payment_date__isnull=False) & Q(last_payment_date__lt=ninety_days_ago)) |
                (Q(last_payment_date__isnull=True) & Q(date_sold__lt=ninety_days_ago))
            )
        )

        aggregations = all_sales.aggregate(
            money_received=Sum(
                Case(
                    When(payment_status__iexact='completed', then=safe_cost),
                    When(payment_status__in=['ongoing', 'installment'], then=safe_deposit),
                    When(payment_status__iexact='overdue', then=safe_deposit),
                    default=0.0,
                    output_field=FloatField()
                )
            ),
            receivables=Sum(
                Case(
                    When(payment_status__in=['ongoing', 'installment', 'overdue'], then=safe_cost - safe_deposit),
                    default=0.0,
                    output_field=FloatField()
                )
            ),
            overdue_amount=Sum(
                Case(
                    When(is_overdue_logic, then=safe_cost - safe_deposit),
                    default=0.0,
                    output_field=FloatField()
                )
            )
        )

        money_received = aggregations["money_received"] or 0
        receivables = aggregations["receivables"] or 0
        overdue_amount = aggregations["overdue_amount"] or 0
        total_revenue = money_received + receivables
        overdue_count = all_sales.filter(is_overdue_logic).count()

        # Filtering
        sales_query = all_sales.prefetch_related("items").order_by("-date_sold", "-id")

        # ── DATE FILTER ──────────────────────────────────────────────────────
        # Filters by EITHER date_sold OR the date of any logged payment.
        # This ensures sales imported without a payment log are never excluded
        # as long as their sale date falls within the chosen range.
        # Uses a single clean OR condition — no double-filtering, no off-by-one.
        if start_date:
            try:
                if len(start_date) == 10 and start_date[2] == '-':
                    parsed_start = datetime.strptime(start_date, "%d-%m-%Y").date()
                else:
                    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
                sales_query = sales_query.filter(date_sold__gte=parsed_start)
            except (ValueError, AttributeError):
                pass

        if end_date:
            try:
                if len(end_date) == 10 and end_date[2] == '-':
                    parsed_end = datetime.strptime(end_date, "%d-%m-%Y").date()
                else:
                    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
                sales_query = sales_query.filter(date_sold__lte=parsed_end)
            except (ValueError, AttributeError):
                pass
        # ────────────────────────────────────────────────────────────────────

        if search_query:
            sales_query = sales_query.filter(
                Q(name__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(invoice_number__icontains=search_query) |
                Q(items__equipment__icontains=search_query)
            ).distinct()

        if status_filter == 'overdue':
            sales_query = sales_query.filter(is_overdue_logic)
        elif status_filter and status_filter != 'all':
            sales_query = sales_query.filter(payment_status__iexact=status_filter)

        # Pagination
        paginator = StandardResultsSetPagination()
        paginated_sales = paginator.paginate_queryset(sales_query, request, view=self)

        rows = []
        for sale in paginated_sales:
            items_list = list(sale.items.values("equipment", "equipment_type", "serial_number", "cost"))

            db_status = (sale.payment_status or "pending").lower()

            if db_status not in ['completed', 'paid', 'fully-paid']:
                if sale.is_overdue:
                    display_status = "overdue"
                else:
                    display_status = "ongoing"
            else:
                display_status = db_status

            rows.append({
                "payment_id":     sale.invoice_number,
                "invoice_number": sale.invoice_number,
                "customer_name":  sale.name,
                "customer_phone": sale.phone,
                "items":          items_list,
                "amount":         str(sale.total_cost),
                "date":           sale.date_sold.strftime("%Y-%m-%d") if sale.date_sold else "—",
                "payment_plan":   sale.payment_plan or "Full Payment",
                "payment_status": display_status,
                "state":          sale.state or "—",
            })

        return Response({
            "summary": {
                "total_revenue":  float(total_revenue),
                "receivables":    float(receivables),
                "overdue_amount": float(overdue_amount),
                "overdue_count":  overdue_count,
                "total_sales":    all_sales.count(),
            },
            "payments": rows,
            "total_pages": getattr(paginator.page.paginator, 'num_pages', 1),
            "current_page": getattr(paginator.page, 'number', 1),
            "total_items": getattr(paginator.page.paginator, 'count', len(rows))
        }, status=status.HTTP_200_OK)

# ----------------------------
#  CODE MANAGEMENT VIEWS
# ----------------------------

# 1. IMPORT CODES FROM EXCEL
class ImportCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            excel_file = request.FILES.get('excel_file')
            batch_number = request.data.get('batch_number', f'CHINA-{timezone.now().strftime("%Y%m%d")}')
            supplier = request.data.get('supplier', 'China Supplier')
            
            if not excel_file:
                return Response(
                    {"error": "Excel file is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Read Excel file
            df = pd.read_excel(excel_file)
            
            # Expected columns: code, duration, serial_number (optional)
            required_columns = ['code', 'duration']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                return Response(
                    {"error": f"Missing columns: {', '.join(missing_columns)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create batch
            batch = CodeBatch.objects.create(
                batch_number=batch_number,
                supplier=supplier,
                notes=f"Imported {len(df)} codes from Excel"
            )
            
            # Import codes
            imported_count = 0
            assigned_count = 0
            
            for _, row in df.iterrows():
                code = str(row['code']).strip()
                duration = str(row['duration']).strip().lower()
                serial_number = str(row.get('serial_number', '')).strip() if pd.notna(row.get('serial_number')) else None
                
                # Map duration to valid choices
                duration_map = {
                    '2 weeks': '2weeks',
                    '1 month': '1month',
                    '3 months': '3months',
                    'unlimited': 'unlimited',
                    '2weeks': '2weeks',
                    '1month': '1month',
                    '3months': '3months',
                }
                
                duration = duration_map.get(duration, '3months')  # Default to 3months
                
                # Create code
                activation_code = ActivationCode.objects.create(
                    code=code,
                    duration=duration,
                    batch=batch,
                    receiver_serial=serial_number if serial_number else None
                )
                
                imported_count += 1
                
                # Auto-assign if serial number provided and matches a sold receiver
                if serial_number:
                    try:
                        # Find sale item with this serial number
                        sale_item = SaleItem.objects.filter(
                            serial_number__icontains=serial_number
                        ).first()
                        
                        if sale_item:
                            # Get the sale and customer
                            sale = sale_item.sale
                            
                            # Find customer
                            customer = Customer.objects.filter(
                                Q(name__iexact=sale.name) | 
                                Q(phone__iexact=sale.phone)
                            ).first()
                            
                            if customer:
                                # Assign code
                                activation_code.receiver_serial = serial_number
                                activation_code.customer = customer
                                activation_code.sale = sale
                                activation_code.status = 'assigned'
                                activation_code.assigned_date = timezone.now()
                                activation_code.save()
                                
                                # Log assignment
                                CodeAssignmentLog.objects.create(
                                    code=activation_code,
                                    receiver_serial=serial_number,
                                    customer=customer,
                                    sale=sale,
                                    assigned_by=request.user,
                                    notes="Auto-assigned during import"
                                )
                                
                                assigned_count += 1
                    except Exception as e:
                        print(f"Error auto-assigning code {code}: {str(e)}")
                        continue
            
            return Response({
                "success": True,
                "message": f"Imported {imported_count} codes. Auto-assigned {assigned_count} codes.",
                "batch_id": batch.id,
                "batch_number": batch.batch_number
            })
            
        except Exception as e:
            return Response(
                {"error": f"Import failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 2. ASSIGN CODE TO RECEIVER
class AssignCodeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            receiver_serial = request.data.get('receiver_serial')
            code_id = request.data.get('code_id')
            customer_id = request.data.get('customer_id')
            sale_id = request.data.get('sale_id')
            
            if not receiver_serial or not code_id:
                return Response(
                    {"error": "Receiver serial and code ID are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get code
            code = ActivationCode.objects.get(id=code_id, status='available')
            
            # Get customer and sale
            customer = None
            sale = None
            
            if customer_id:
                customer = Customer.objects.get(id=customer_id)
            
            if sale_id:
                sale = Sale.objects.get(id=sale_id)
            
            # Find customer from serial if not provided
            if not customer:
                # Try to find sale item with this serial
                sale_item = SaleItem.objects.filter(
                    serial_number__icontains=receiver_serial
                ).first()
                
                if sale_item and sale_item.sale:
                    sale = sale_item.sale
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale.name) | 
                        Q(phone__iexact=sale.phone)
                    ).first()
            
            # Assign code
            code.receiver_serial = receiver_serial
            code.customer = customer
            code.sale = sale
            code.status = 'assigned'
            code.assigned_date = timezone.now()
            code.save()
            
            # Log assignment
            CodeAssignmentLog.objects.create(
                code=code,
                receiver_serial=receiver_serial,
                customer=customer,
                sale=sale,
                assigned_by=request.user
            )
            
            return Response({
                "success": True,
                "message": f"Code {code.code} assigned to receiver {receiver_serial}",
                "code": ActivationCodeSerializer(code).data
            })
            
        except ActivationCode.DoesNotExist:
            return Response(
                {"error": "Code not found or not available"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Assignment failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 3. GET CUSTOMER CODES
class CustomerCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        try:
            user = request.user
            receiver_serial = request.query_params.get('receiver_serial')
            
            if user.role == 'customer':
                # Get customer's codes
                customer = Customer.objects.get(user=user)
                codes = ActivationCode.objects.filter(
                    customer=customer,
                    status='assigned'
                ).order_by('-assigned_date')
                
                # Filter by serial if provided
                if receiver_serial:
                    codes = codes.filter(receiver_serial__icontains=receiver_serial)
                
                # Check payment status for each receiver
                result = []
                for code in codes:
                    # Get payment info for this receiver
                    last_payment = Payment.objects.filter(
                        customer=user,
                        sale=code.sale
                    ).order_by('-payment_date').first()
                    
                    months_since_payment = None
                    if last_payment:
                        months_since_payment = (timezone.now() - last_payment.payment_date).days // 30
                    
                    # Check eligibility
                    eligible_for_regular = months_since_payment is not None and months_since_payment <= 4
                    
                    result.append({
                        **ActivationCodeSerializer(code).data,
                        'eligible_for_regular': eligible_for_regular,
                        'months_since_last_payment': months_since_payment,
                        'requires_payment': not eligible_for_regular and code.is_emergency,
                        'can_request_emergency': not eligible_for_regular
                    })
                
                return Response(result)
            
            elif user.role in ['admin', 'staff']:
                # Admin can query any customer
                customer_id = request.query_params.get('customer_id')
                receiver_serial = request.query_params.get('receiver_serial')
                
                if customer_id:
                    customer = Customer.objects.get(id=customer_id)
                    codes = ActivationCode.objects.filter(customer=customer)
                elif receiver_serial:
                    codes = ActivationCode.objects.filter(receiver_serial__icontains=receiver_serial)
                else:
                    return Response(
                        {"error": "Provide customer_id or receiver_serial for admin query"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                return Response(ActivationCodeSerializer(codes, many=True).data)
            
            return Response([])
            
        except Customer.DoesNotExist:
            return Response(
                {"error": "Customer not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 4. GENERATE EMERGENCY CODE
class GenerateEmergencyCodeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            receiver_serial = request.data.get('receiver_serial')
            customer_id = request.data.get('customer_id')
            
            if not receiver_serial:
                return Response(
                    {"error": "Receiver serial is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Find available 2-week code
            emergency_code = ActivationCode.objects.filter(
                duration='2weeks',
                status='available',
                is_emergency=True
            ).first()
            
            if not emergency_code:
                return Response(
                    {"error": "No emergency codes available"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get customer
            customer = None
            if customer_id:
                customer = Customer.objects.get(id=customer_id)
            else:
                # Try to find customer from serial
                sale_item = SaleItem.objects.filter(
                    serial_number__icontains=receiver_serial
                ).first()
                
                if sale_item and sale_item.sale:
                    sale = sale_item.sale
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale.name) | 
                        Q(phone__iexact=sale.phone)
                    ).first()
            
            # Assign emergency code
            emergency_code.receiver_serial = receiver_serial
            emergency_code.customer = customer
            emergency_code.status = 'assigned'
            emergency_code.assigned_date = timezone.now()
            emergency_code.save()
            
            # Log assignment
            CodeAssignmentLog.objects.create(
                code=emergency_code,
                receiver_serial=receiver_serial,
                customer=customer,
                assigned_by=request.user,
                notes="Emergency code generated"
            )
            
            return Response({
                "success": True,
                "message": f"Emergency 2-week code generated for {receiver_serial}",
                "code": emergency_code.code,
                "expiry_date": emergency_code.expiry_date
            })
            
        except Exception as e:
            return Response(
                {"error": f"Failed to generate emergency code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 5. AVAILABLE CODES VIEW (for admin)
class AvailableCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def get(self, request):
        duration = request.query_params.get('duration')
        
        codes = ActivationCode.objects.filter(status='available')
        
        if duration:
            codes = codes.filter(duration=duration)
        
        # Count by duration
        counts = codes.values('duration').annotate(count=Count('id'))
        
        return Response({
            'codes': ActivationCodeSerializer(codes, many=True).data,
            'counts': list(counts),
            'total_available': codes.count()
        })


# 6. RECEIVERS NEEDING CODES
class ReceiversNeedingCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def get(self, request):
        # Find receivers sold but without codes or with expired codes
        receivers_needing_codes = []
        
        # Get all sale items that are receivers
        receiver_sales = SaleItem.objects.filter(
            tool__category='Receiver'
        ).select_related('sale', 'tool')
        
        for sale_item in receiver_sales:
            # Get serial number(s)
            serials = []
            if sale_item.serial_number:
                # Check if it's a JSON array or single serial
                try:
                    serials_data = json.loads(sale_item.serial_number)
                    if isinstance(serials_data, list):
                        serials = serials_data
                    else:
                        serials = [serials_data]
                except:
                    serials = [sale_item.serial_number]
            
            # Check each serial
            for serial in serials:
                # Check if code exists for this serial
                code_exists = ActivationCode.objects.filter(
                    receiver_serial=serial,
                    status='assigned'
                ).exists()
                
                # Check if existing code is expired
                expired_code = ActivationCode.objects.filter(
                    receiver_serial=serial,
                    status='assigned'
                ).first()
                
                is_expired = expired_code and expired_code.is_expired if expired_code else False
                
                if not code_exists or is_expired:
                    # Find customer
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale_item.sale.name) | 
                        Q(phone__iexact=sale_item.sale.phone)
                    ).first()
                    
                    receivers_needing_codes.append({
                        'serial': serial,
                        'customer_id': customer.id if customer else None,
                        'customer_name': customer.name if customer else sale_item.sale.name,
                        'sale_id': sale_item.sale.id,
                        'sale_invoice': sale_item.sale.invoice_number,
                        'last_payment_date': customer.date_last_paid if customer else None,
                        'needs_urgent': is_expired,  # Expired = urgent
                        'has_code': code_exists,
                        'code_expired': is_expired
                    })
        
        return Response(receivers_needing_codes)

# ---------------------------------------------------------
# DIRECT CODE MANAGEMENT (MANUAL EDITING)
# ---------------------------------------------------------

class ReceiverCodeManagementView(APIView):
    # Leave this as IsAuthenticated so both internal team and customers can hit the URL
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 1. FIXED SECURITY CHECK: Allow both 'admin' and 'staff' to see everything
        is_internal_team = (
            user.is_staff or 
            user.is_superuser or 
            getattr(user, 'role', '') in ['admin', 'staff']
        )

        in_stock_data = []
        sold_data = []

        # --- 1. IN-STOCK RECEIVERS (Internal Team Only) ---
        if is_internal_team:
            in_stock_tools = Tool.objects.filter(category__icontains='Receiver', stock__gt=0)
            for tool in in_stock_tools:
                serials = tool.available_serials or []
                for serial in serials:
                    if any(x in serial.upper() for x in ["DL-", "ER-", "RADIO", "EXTERNAL"]):
                        continue

                    code_obj = ActivationCode.objects.filter(receiver_serial=serial).order_by('-id').first()
                    
                    in_stock_data.append({
                        "serial": serial,
                        "tool_name": tool.name,
                        "status": "In Stock",
                        "current_code": code_obj.code if code_obj else "",
                        "duration": getattr(code_obj, 'duration', "") if code_obj else "",
                        "qr_code_image": code_obj.qr_code_image if code_obj else "",
                        "payment_status": "N/A" # <-- FIXED: Items in stock have no payment status yet
                    })

        # --- 2. SOLD RECEIVERS (Team sees all, Customers see their own) ---
        if is_internal_team:
            # Admins and Staff see everything
            sold_items = SaleItem.objects.filter(tool__category__icontains='Receiver').select_related('sale', 'tool')
        else:
            # 2. FIXED CUSTOMER QUERY: Search by Name or Phone text fields
            query = Q()
            
            if user.name:
                query |= Q(sale__name__iexact=user.name)
            if user.phone:
                query |= Q(sale__phone=user.phone)
                
            # Fallback to Customer profile if it exists
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name:
                    query |= Q(sale__name__iexact=user.customer.name)
                if user.customer.phone:
                    query |= Q(sale__phone=user.customer.phone)
            
            # If we don't know the user's name or phone, return an empty list
            if not query:
                sold_items = SaleItem.objects.none()
            else:
                sold_items = SaleItem.objects.filter(query, tool__category__icontains='Receiver').select_related('sale', 'tool')

        # --- PROCESS SOLD ITEMS ---
        today = timezone.now().date() 

        for item in sold_items:
            # 1. Calculate the exact database status from the Sale
            calculated_status = "pending"
            if item.sale:
                calculated_status = item.sale.payment_status if item.sale.payment_status else "pending"
                
                if item.sale.payment_plan == "Yes" and calculated_status == "pending":
                    calculated_status = "ongoing"

                if calculated_status.lower() not in ['completed', 'paid']:
                    if item.sale.due_date and item.sale.due_date < today:
                        calculated_status = "overdue"

            # 👇 2. NEW TRANSLATOR LOGIC 👇
            # Translate the database status into the exact words your frontend expects
            if calculated_status.lower() == 'overdue':
                final_frontend_status = "Overdue"
            else:
                final_frontend_status = "Paid" 

            serials = []
            if item.serial_number:
                try:
                    parsed = json.loads(item.serial_number)
                    serials = parsed if isinstance(parsed, list) else [item.serial_number]
                except:
                    serials = [item.serial_number]

            for serial in serials:
                if not serial or any(x in serial.upper() for x in ["DL-", "ER-", "RADIO", "EXTERNAL"]):
                    continue

                code_obj = ActivationCode.objects.filter(receiver_serial=serial).order_by('-id').first()
                
                sold_data.append({
                    "serial": serial,
                    "tool_name": item.tool.name,
                    "customer_name": item.sale.name if item.sale else "Unknown",
                    "invoice": item.sale.invoice_number if item.sale else "No Invoice",
                    "payment_status": final_frontend_status, # <--- Use the translated status!
                    "status": "Sold",
                    "current_code": code_obj.code if code_obj else "",
                    "duration": getattr(code_obj, 'duration', "") if code_obj else "",
                    "qr_code_image": code_obj.qr_code_image if code_obj else "",
                })

        return Response({
            "in_stock": in_stock_data,
            "sold": sold_data
        }, status=status.HTTP_200_OK)

class SaveReceiverCodeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated] 

    def post(self, request):
        serial = request.data.get('serial')
        new_code = request.data.get('code')
        # REMOVED: duration = request.data.get('duration', 'unlimited')

        if not serial or not new_code:
            return Response({"error": "Serial and code are required."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. FIND THE SALE AND CUSTOMER
        sale_item = SaleItem.objects.filter(serial_number__icontains=serial).select_related('sale').first()
        
        customer_obj = None
        sale_obj = None

        if sale_item:
            sale_obj = sale_item.sale
            customer_obj = Customer.objects.filter(name=sale_item.sale.name).first()

        # 2. UPDATE OR CREATE ACTIVATION CODE (without duration)
        code_obj, created = ActivationCode.objects.update_or_create(
            receiver_serial=serial,
            defaults={
                "code": new_code,
                # REMOVED: "duration": duration,
                "customer": customer_obj,
                "sale": sale_obj,
                "status": 'assigned',
                "assigned_date": timezone.now(),
            }
        )

        # 3. ENSURE A BATCH EXISTS
        if created or not code_obj.batch:
            manual_batch, _ = CodeBatch.objects.get_or_create(
                batch_number="MANUAL-ENTRY",
                defaults={"supplier": "Manual Entry", "notes": "Codes entered manually via Code Management"}
            )
            code_obj.batch = manual_batch
            code_obj.save()

        return Response({
            "message": "Code saved successfully",
            "linked_to": customer_obj.name if customer_obj else "No Customer Found"
        }, status=status.HTTP_200_OK)
    

# ─────────────────────────────────────────────────────────────────────────────
#  1. BATCH LIST + CREATE
#     GET  /api/code-batches/
#     POST /api/code-batches/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        batches = CodeBatch.objects.annotate(
            code_count=Count("codes")
        ).order_by("-created_at")

        data = []
        for b in batches:
            valid_sold_statuses = ['ongoing', 'completed', 'overdue']
            in_stock_count = BatchSerial.objects.filter(batch=b, status="not sold").count()
            draft_count = BatchSerial.objects.filter(
                batch=b, status="active"
            ).exclude(payment_status__in=valid_sold_statuses).count()
            sold_count = BatchSerial.objects.filter(
                batch=b, status="active", payment_status__in=valid_sold_statuses
            ).count()
            in_stock_count = in_stock_count + draft_count
            data.append({
                "id":             b.id,
                "batch_number":   b.batch_number,
                "received_date":  str(b.received_date),
                "supplier":       b.supplier,
                "notes":          b.notes or "",
                "code_count":     b.code_count,
                "in_stock_count": in_stock_count,
                "sold_count":     sold_count,
                "created_at":     b.created_at.isoformat(),
            })
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        batch_number = request.data.get("batch_number", "").strip()
        if not batch_number:
            return Response({"detail": "batch_number is required."}, status=status.HTTP_400_BAD_REQUEST)

        if CodeBatch.objects.filter(batch_number=batch_number).exists():
            return Response(
                {"detail": f"A batch named '{batch_number}' already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        batch = CodeBatch.objects.create(
            batch_number  = batch_number,
            supplier      = request.data.get("supplier", "China Supplier"),
            notes         = request.data.get("notes", ""),
            received_date = request.data.get("received_date") or timezone.now().date(),
        )
        return Response({
            "id":             batch.id,
            "batch_number":   batch.batch_number,
            "received_date":  str(batch.received_date),
            "supplier":       batch.supplier,
            "notes":          batch.notes or "",
            "code_count":     0,
            "in_stock_count": 0,
            "sold_count":     0,
        }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
#  2. BATCH ITEMS — in_stock / sold split for one batch
#     GET /api/code-batches/<pk>/items/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchItemsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        batch   = get_object_or_404(CodeBatch, pk=pk)
        serials = BatchSerial.objects.filter(batch=batch).order_by("serial_number")

        in_stock = []
        sold     = []

        for s in serials:
            # Look up activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            # Format expiry_date cleanly as DD/MM/YYYY
            expiry_display = ""
            if code_obj and code_obj.expiry_date:
                try:
                    expiry_display = code_obj.expiry_date.strftime("%d/%m/%Y")
                except Exception:
                    expiry_display = str(code_obj.expiry_date)[:10]

            row = {
                "serial":         s.serial_number,
                "status":         s.status,
                "payment_status": s.payment_status,
                "customer_name":  s.customer_name or "",
                "customer_email": s.customer_email or "",
                "assigned_date":  str(s.assigned_date) if s.assigned_date else "",
                "current_code":   code_obj.code if code_obj else "",
                "code_expiry":    expiry_display,
                "duration": f"{(code_obj.expiry_date.date() - timezone.now().date()).days} days" if (code_obj and code_obj.expiry_date) else "unlimited",
                "qr_code_image": code_obj.qr_code_image if code_obj else "",  # NEW
            }

            if s.status == "not sold":
                in_stock.append(row)
            else:
                sold.append(row)

        return Response({"in_stock": in_stock, "sold": sold}, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
#  3. CSV UPLOAD — auto-detects format A (supplier codes) or B (assignments)
#     POST /api/code-batches/<pk>/upload-csv/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchUploadCSVView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _parse_date(self, date_str):
        """Handle all date formats: datetime objects, Excel serials, and strings."""
        if not date_str:
            return None

        if hasattr(date_str, 'date'):
            return date_str.date()
        if hasattr(date_str, 'year'):
            return date_str

        date_str = str(date_str).strip()
        if not date_str or date_str.lower() in ('none', 'null', 'nan', ''):
            return None

        try:
            serial = int(float(date_str))
            if 20000 < serial < 70000:
                from datetime import date as date_type, timedelta
                return date_type(1899, 12, 30) + timedelta(days=serial)
        except (ValueError, TypeError):
            pass

        date_str = date_str.split(" ")[0].split("T")[0]

        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y",
                    "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_qr_codes_from_excel(self, uploaded_file):
        """
        Extract QR code images from Excel file.
        Returns a dict mapping 1-indexed row numbers to base64-encoded images.

        The QR codes in this Excel contain FUNCTIONREG data — NOT serial numbers —
        so we cannot match by decoded content. Instead we match strictly by row:
        image anchored at Excel row N belongs to the serial number at data row N.

        Row 1 = header, Row 2 = first data row, etc.
        anchor._from.row is 0-indexed so we add 1 to get the 1-indexed Excel row.
        """
        qr_codes = {}

        try:
            temp_file = BytesIO(uploaded_file.read())
            wb = load_workbook(temp_file)
            ws = wb.active

            for img in ws._images:
                if hasattr(img, 'anchor') and hasattr(img.anchor, '_from'):
                    # Convert 0-indexed anchor row to 1-indexed Excel row
                    anchor_row = img.anchor._from.row + 1

                    if hasattr(img, '_data'):
                        try:
                            image_data = img._data()
                            pil_image = PILImage.open(BytesIO(image_data))

                            # Resize for storage
                            max_size = (300, 300)
                            pil_image.thumbnail(max_size, PILImage.Resampling.LANCZOS)
                            buffered = BytesIO()
                            pil_image.save(buffered, format="PNG")
                            img_base64 = base64.b64encode(
                                buffered.getvalue()
                            ).decode('utf-8')

                            # Store by row number — strict positional match
                            qr_codes[anchor_row] = img_base64

                        except Exception:
                            pass

        except Exception:
            import traceback
            traceback.print_exc()

        return qr_codes

    def post(self, request, pk):
        # Initialize variables
        imported = 0
        errors = []
        qr_codes = {}
        rows = []
        cols = set()

        batch = get_object_or_404(CodeBatch, pk=pk)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response(
                {"detail": "No file uploaded."},
                status=status.HTTP_400_BAD_REQUEST
            )

        file_ext = uploaded_file.name.lower().split('.')[-1]

        if file_ext not in ['csv', 'xlsx', 'xls']:
            return Response(
                {"detail": "Only .csv, .xlsx, and .xls files are accepted."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Extract QR codes from Excel before reading data
        if file_ext in ['xlsx', 'xls']:
            qr_codes = self._extract_qr_codes_from_excel(uploaded_file)
            uploaded_file.seek(0)

        # Read file data
        if file_ext in ['xlsx', 'xls']:
            import pandas as pd
            from io import BytesIO as _BytesIO
            df = pd.read_excel(_BytesIO(uploaded_file.read()))
            df.columns = [
                str(col).strip().lower().replace(" ", "_").replace("-", "_")
                for col in df.columns
            ]
            cols = set(df.columns)
            rows = df.to_dict('records')

        else:
            import csv as _csv
            import io as _io
            raw = uploaded_file.read()
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = raw.decode("latin-1")

            reader = _csv.DictReader(_io.StringIO(decoded))

            if not reader.fieldnames:
                return Response(
                    {"detail": "CSV appears to be empty."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            reader.fieldnames = [
                f.strip().lower().replace(" ", "_").replace("-", "_")
                for f in reader.fieldnames
            ]
            cols = set(reader.fieldnames)
            rows = list(reader)

        # Validate serial number column exists
        if "serial_number" not in cols and "sn" not in cols:
            return Response(
                {"detail": f"File must have a 'serial_number' or 'SN' column. Found: {sorted(cols)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Detect format — supplier format has activation codes
        columns = [str(c).lower() for c in rows[0].keys()] if rows else []
        is_supplier_format = any(
            col in columns
            for col in ['code', 'current_code', 'activation_code', 'activationcode', 'temporary_code']
        )

        if is_supplier_format:
            for row_num, row in enumerate(rows, start=2):
                serial = str(row.get("serial_number") or row.get("sn") or "").strip()

                code = str(
                    row.get("code", "") or
                    row.get("current_code", "") or
                    row.get("activation_code", "") or
                    row.get("activationcode", "") or
                    row.get("temporary_code", "") or ""
                ).strip()

                if not serial or serial == 'nan':
                    errors.append(f"Row {row_num}: serial_number is empty — skipped.")
                    continue
                if not code or code == 'nan':
                    errors.append(f"Row {row_num} ({serial}): no code value found — skipped.")
                    continue

                expiry_raw = (
                    row.get("expiry_date", "") or
                    row.get("code_expiry", "") or
                    row.get("expiry", "") or
                    row.get("date_to", "") or ""
                )
                expiry_date = self._parse_date(expiry_raw)

                row_status         = str(row.get("status", "not sold")).strip().lower()
                row_payment        = str(row.get("payment_status", "not_applicable")).strip().lower()
                row_customer_email = str(row.get("customer_email", "")).strip()
                row_customer_name  = str(row.get("customer_name", "")).strip()
                row_assigned_date  = self._parse_date(row.get("assigned_date", ""))

                # Clean up pandas NaN strings
                if row_customer_email.lower() in ['nan', 'none', 'null']:
                    row_customer_email = ""
                if row_customer_name.lower() in ['nan', 'none', 'null']:
                    row_customer_name = ""

                # ── QR code matching ──────────────────────────────────────────
                # QR codes contain FUNCTIONREG data, NOT serial numbers.
                # Match strictly by row position: image at anchor row N
                # belongs to the serial at data row N.
                # row_num starts at 2 (row 1 = header) matching Excel row numbers.
                qr_code_base64 = qr_codes.get(row_num, None)

                try:
                    import json

                    # Auto-match serial to SaleItem if no customer info in the file
                    if not row_customer_name and not row_customer_email:
                        matched_sale_item = None

                        # Try exact match first
                        exact = SaleItem.objects.filter(
                            serial_number=serial
                        ).select_related('sale').first()

                        if exact:
                            matched_sale_item = exact
                        else:
                            # Try JSON array match
                            for item in SaleItem.objects.exclude(
                                serial_number__isnull=True
                            ).exclude(serial_number='').select_related('sale'):
                                try:
                                    parsed = json.loads(item.serial_number)
                                    if isinstance(parsed, list):
                                        cleaned = [str(s).strip().upper() for s in parsed]
                                        if serial.upper() in cleaned:
                                            matched_sale_item = item
                                            break
                                except (json.JSONDecodeError, TypeError):
                                    if serial.upper() in str(item.serial_number).upper():
                                        matched_sale_item = item
                                        break

                        if matched_sale_item and matched_sale_item.sale:
                            sale_record = matched_sale_item.sale
                            row_customer_name = sale_record.name or row_customer_name

                            # Try email from Sale record directly
                            if not row_customer_email and getattr(sale_record, 'email', None):
                                row_customer_email = sale_record.email

                            # Fallback — look up Customer by phone
                            if not row_customer_email and sale_record.phone:
                                cust = Customer.objects.filter(
                                    phone=sale_record.phone
                                ).first()
                                if cust:
                                    row_customer_email = cust.email or ''

                            # Fallback — look up Customer by name
                            if not row_customer_email and sale_record.name:
                                cust = Customer.objects.filter(
                                    name__iexact=sale_record.name
                                ).first()
                                if cust:
                                    row_customer_email = cust.email or ''

                            # Mark as sold since we found a matching sale
                            if row_status == 'not sold':
                                row_status = 'active'

                            # Use sale payment status — more reliable than Excel
                            sale_status = (sale_record.payment_status or '').lower().strip()
                            status_map = {
                                'completed': 'completed',
                                'ongoing':   'ongoing',
                                'overdue':   'overdue',
                            }
                            row_payment = status_map.get(sale_status, sale_status or row_payment)

                    # Save or update activation code
                    ActivationCode.objects.update_or_create(
                        receiver_serial=serial,
                        defaults={
                            "code":          code,
                            "batch":         batch,
                            "expiry_date":   expiry_date,
                            "status":        "assigned" if row_status == "active" else "available",
                            "assigned_date": timezone.now() if row_status == "active" else None,
                            "qr_code_image": qr_code_base64,
                        }
                    )

                    # Save or update batch serial with customer info
                    BatchSerial.objects.update_or_create(
                        batch=batch,
                        serial_number=serial,
                        defaults={
                            "status":         row_status,
                            "payment_status": row_payment,
                            "customer_email": row_customer_email or None,
                            "customer_name":  row_customer_name  or None,
                            "assigned_date":  row_assigned_date,
                        }
                    )

                    imported += 1

                except Exception as exc:
                    errors.append(f"Row {row_num} ({serial}): {exc}")
                    import traceback
                    traceback.print_exc()

        return Response({
            "imported": imported,
            "errors":   errors,
            "qr_codes_extracted": len(qr_codes),
        }, status=status.HTTP_200_OK)

# ─────────────────────────────────────────────────────────────────────────────
#  4. CSV DOWNLOAD — exports batch serials + their codes back to CSV
#     GET /api/code-batches/<pk>/download-csv/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchDownloadCSVView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        batch   = get_object_or_404(CodeBatch, pk=pk)
        serials = BatchSerial.objects.filter(batch=batch).order_by("serial_number")

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="batch_{batch.batch_number}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow([
            "serial_number", "current_code", "code_expiry",
            "status", "payment_status",
            "customer_email", "customer_name", "assigned_date",
        ])

        for s in serials:
            # Get activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            writer.writerow([
                s.serial_number,
                code_obj.code        if code_obj else "",
                code_obj.expiry_date.strftime("%d/%m/%Y") if (code_obj and code_obj.expiry_date) else "",
                s.status,
                s.payment_status,
                s.customer_email or "",
                s.customer_name  or "",
                s.assigned_date.strftime("%d/%m/%Y") if s.assigned_date else "",
            ])

        return response

class SendBulkExpirationEmailsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        batch = get_object_or_404(CodeBatch, pk=pk)
        
        # Get all sold/active serials that actually have an email address
        sold_serials = BatchSerial.objects.filter(
            batch=batch, 
            status__in=["active", "sold"] 
        ).exclude(customer_email="") 

        sent_count = 0
        failed_count = 0

        for s in sold_serials:
            # Look up the latest activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            # Skip if they don't have a code or an expiry date yet
            if not code_obj or not code_obj.expiry_date:
                continue

            try:
                # Format the date nicely
                expiry_display = code_obj.expiry_date.strftime("%d/%m/%Y")
                
                # Build the email
                customer_name = s.customer_name if s.customer_name else "Valued Customer"
                subject = "Action Required: Your Equipment Activation Code is Expiring Soon"
                
                message = (
                    f"Hello {customer_name},\n\n"
                    f"This is a friendly reminder regarding your equipment (Serial: {s.serial_number}).\n"
                    f"Your current activation code ({code_obj.code}) is set to expire on {expiry_display}.\n\n"
                    f"Please contact us to renew your activation code and ensure uninterrupted service.\n\n"
                    f"Best regards,\nYour Support Team"
                )

                # Send the email using Django's free built-in sender
                send_mail(
                    subject,
                    message,
                    'your-company@gmail.com',  # <-- Change to your sending email
                    [s.customer_email],
                    fail_silently=False,
                )
                sent_count += 1
                
            except Exception as e:
                print(f"Failed to send email to {s.customer_email}: {str(e)}")
                failed_count += 1

        return Response(
            {"sent": sent_count, "failed": failed_count}, 
            status=status.HTTP_200_OK
        )

class PublicCodeSearchView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serial_number = request.data.get("serial_number", "").strip()

        if not serial_number:
            return Response(
                {"error": "Please enter a valid serial number."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. Find the serial in BatchSerial
        batch_serial = BatchSerial.objects.filter(
            serial_number=serial_number
        ).first()

        if not batch_serial:
            return Response(
                {"error": "Serial number not found in our records."},
                status=status.HTTP_404_NOT_FOUND
            )

        payment_status = str(batch_serial.payment_status or "").strip().lower()

        # 2. Enforce payment rules
        # OVERDUE — payment has lapsed for 90 days or due date passed
        if payment_status == 'overdue':
            return Response(
                {"error": "Your activation code has been locked. We have not received payment from you in over 3 months. Please contact support to restore access."},
                status=status.HTTP_403_FORBIDDEN
            )

        # NOT YET PAID / UNKNOWN — serial exists but no payment recorded
        locked_statuses = ['not_applicable', 'not applicable', 'pending', '', 'unknown']
        if payment_status in locked_statuses:
            return Response(
                {"error": f"Activation code locked. Current payment status: {payment_status.title().replace('_', ' ') or 'Unknown'}."},
                status=status.HTTP_403_FORBIDDEN
            )

        # ONGOING — customer is on an active installment plan, code is accessible
        # PAID / COMPLETED — fully paid, code is accessible
        # Both ongoing and paid/completed get the code
        allowed_statuses = ['paid', 'ongoing', 'completed', 'fully-paid', 'fully_paid']
        if payment_status not in allowed_statuses:
            return Response(
                {"error": f"Activation code locked. Current payment status: {payment_status.title().replace('_', ' ')}."},
                status=status.HTTP_403_FORBIDDEN
            )

        # 3. Fetch the activation code
        activation = ActivationCode.objects.filter(
            receiver_serial=serial_number
        ).first()

        if not activation:
            return Response(
                {"error": "Activation code has not been generated for this device yet."},
                status=status.HTTP_404_NOT_FOUND
            )

        # 4. Return the code with status context
        status_display = {
            'paid':       'Fully Paid',
            'completed':  'Fully Paid',
            'fully-paid': 'Fully Paid',
            'fully_paid': 'Fully Paid',
            'ongoing':    'Ongoing — Installment Plan Active',
        }.get(payment_status, payment_status.title())

        return Response({
            "success":        True,
            "serial_number":  serial_number,
            "code":           activation.code,
            "expiry_date":    activation.expiry_date,
            "customer_name":  batch_serial.customer_name,
            "payment_status": status_display,
        }, status=status.HTTP_200_OK)
