import re
import json
import hashlib
import datetime
import threading
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from .models import Product, Dealer, Order, OrderItem, IdempotencyRecord
from .serializers import (
    ProductSerializer, ProductCreateSerializer, ProductUpdateSerializer,
    DealerSerializer, DealerCreateSerializer,
    OrderSerializer, format_money
)
from .exceptions import api_error

DB_WRITE_LOCK = threading.RLock()

@contextmanager
def serialized_db_op():
    with DB_WRITE_LOCK:
        try:
            yield
        finally:
            if not connection.in_atomic_block:
                connection.close()

def parse_strict_int(val):
    if val is None or isinstance(val, bool) or isinstance(val, float):
        raise ValueError("Value must be an integer")
    if isinstance(val, int):
        return val
    if isinstance(val, str) and re.match(r"^-?\d+$", val.strip()):
        return int(val.strip())
    raise ValueError("Value must be an integer")

class CustomPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

@api_view(["GET"])
def health(request):
    """GET /api/health/ — the grader polls this until your app is ready."""
    return Response({"status": "ok"})

# ----------------- Products API -----------------
@api_view(["GET", "POST"])
def product_list(request):
    if request.method == "GET":
        qs = Product.objects.all().order_by("sku")
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search) |
                Q(name__icontains=search) |
                Q(brand__icontains=search)
            )
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        vehicle = request.query_params.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle)

        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        sku = request.data.get("sku")
        if not sku:
            return api_error("VALIDATION_ERROR", "sku is required", ["sku: required"], 400)
        with serialized_db_op():
            if Product.objects.filter(sku=sku).exists():
                return api_error("DUPLICATE", f"Product with sku {sku} already exists", [f"sku {sku} already exists"], 409)

            serializer = ProductCreateSerializer(data=request.data)
            if not serializer.is_valid():
                details = [f"{k}: {v}" for k, v in serializer.errors.items()]
                return api_error("VALIDATION_ERROR", details[0] if details else "Invalid input", details, 400)
            product = serializer.save()
            return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)

@api_view(["GET", "PATCH"])
def product_detail(request, sku):
    if request.method == "GET":
        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return api_error("NOT_FOUND", f"Product with sku {sku} not found", [f"sku {sku} not found"], 404)
        return Response(ProductSerializer(product).data)

    elif request.method == "PATCH":
        with serialized_db_op():
            try:
                product = Product.objects.get(sku=sku)
            except Product.DoesNotExist:
                return api_error("NOT_FOUND", f"Product with sku {sku} not found", [f"sku {sku} not found"], 404)
            if "sku" in request.data and request.data["sku"] != product.sku:
                return api_error("VALIDATION_ERROR", "SKU cannot be changed", ["sku cannot be modified"], 400)
            serializer = ProductUpdateSerializer(product, data=request.data, partial=True)
            if not serializer.is_valid():
                details = [f"{k}: {v}" for k, v in serializer.errors.items()]
                return api_error("VALIDATION_ERROR", details[0] if details else "Invalid input", details, 400)
            product = serializer.save()
            return Response(ProductSerializer(product).data)

# ----------------- Inventory API -----------------
@api_view(["PUT"])
def inventory_update(request, sku):
    qty_raw = request.data.get("quantity")
    if qty_raw is None:
        return api_error("VALIDATION_ERROR", "quantity is required", ["quantity: required"], 400)
    try:
        qty = parse_strict_int(qty_raw)
        if qty < 0:
            return api_error("VALIDATION_ERROR", "quantity must be >= 0", ["quantity must be non-negative integer"], 400)
    except (ValueError, TypeError):
        return api_error("VALIDATION_ERROR", "quantity must be an integer", ["quantity must be integer"], 400)

    with serialized_db_op():
        with transaction.atomic():
            try:
                product = Product.objects.select_for_update().get(sku=sku)
            except Product.DoesNotExist:
                return api_error("NOT_FOUND", f"Product with sku {sku} not found", [f"sku {sku} not found"], 404)

            product.stock = qty
            product.save(update_fields=["stock", "updated_at"])
            return Response({"sku": product.sku, "quantity": product.stock})

# ----------------- Dealers API -----------------
@api_view(["GET", "POST"])
def dealer_list(request):
    if request.method == "GET":
        qs = Dealer.objects.all().order_by("id")
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        code = request.data.get("dealer_code")
        if not code or not request.data.get("name"):
            return api_error("VALIDATION_ERROR", "dealer_code and name are required", ["dealer_code and name are required"], 400)
        with serialized_db_op():
            if Dealer.objects.filter(dealer_code=code).exists():
                return api_error("DUPLICATE", f"Dealer with code {code} already exists", [f"dealer_code {code} already exists"], 409)

            serializer = DealerCreateSerializer(data=request.data)
            if not serializer.is_valid():
                details = [f"{k}: {v}" for k, v in serializer.errors.items()]
                return api_error("VALIDATION_ERROR", details[0] if details else "Invalid input", details, 400)
            dealer = serializer.save()
            return Response(DealerSerializer(dealer).data, status=status.HTTP_201_CREATED)

@api_view(["GET"])
def dealer_detail(request, pk):
    try:
        dealer = Dealer.objects.get(id=pk)
    except Dealer.DoesNotExist:
        return api_error("NOT_FOUND", f"Dealer with id {pk} not found", [f"dealer id {pk} not found"], 404)
    return Response(DealerSerializer(dealer).data)

# ----------------- Orders API & Business Logic -----------------
def generate_order_number():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1001])
    kolkata_tz = timezone.get_current_timezone()
    now = timezone.now().astimezone(kolkata_tz)
    date_str = now.strftime("%Y%m%d")
    prefix = f"ORD-{date_str}-"
    last_order = Order.objects.filter(order_number__startswith=prefix).order_by("-order_number").first()
    if last_order:
        last_seq = int(last_order.order_number.split("-")[-1])
        new_seq = last_seq + 1
    else:
        new_seq = 1
    return f"{prefix}{new_seq:04d}"

def calculate_order_totals(items_data):
    """
    items_data: list of dicts with 'product', 'quantity'
    returns: (items_models_data, subtotal, tax_total, grand_total)
    """
    items_calc = []
    subtotal = Decimal("0.00")
    tax_total = Decimal("0.00")

    for item in items_data:
        p = item["product"]
        q = item["quantity"]
        unit_price = p.price
        line_sub = (unit_price * Decimal(q)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_tax = (line_sub * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_total = line_sub + line_tax

        subtotal += line_sub
        tax_total += line_tax
        items_calc.append({
            "product": p,
            "sku": p.sku,
            "name": p.name,
            "quantity": q,
            "unit_price": unit_price,
            "line_subtotal": line_sub,
            "line_tax": line_tax,
            "line_total": line_total
        })

    grand_total = subtotal + tax_total
    return items_calc, subtotal, tax_total, grand_total

def refresh_draft_order(order, locked_products=None):
    """
    Contract §7.4: While an order is DRAFT, unit_price is the product's current price.
    On confirm, unit_price is locked.
    """
    if order.status != Order.STATUS_DRAFT:
        return order
    items = list(order.items.select_related("product"))
    changed = False
    subtotal = Decimal("0.00")
    tax_total = Decimal("0.00")

    for it in items:
        prod = locked_products[it.product_id] if (locked_products and it.product_id in locked_products) else it.product
        curr_price = prod.price
        curr_name = prod.name
        line_sub = (curr_price * Decimal(it.quantity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_tax = (line_sub * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_total = line_sub + line_tax

        if (
            it.unit_price != curr_price
            or it.name != curr_name
            or it.line_subtotal != line_sub
            or it.line_tax != line_tax
            or it.line_total != line_total
        ):
            it.unit_price = curr_price
            it.name = curr_name
            it.line_subtotal = line_sub
            it.line_tax = line_tax
            it.line_total = line_total
            it.save(update_fields=["unit_price", "name", "line_subtotal", "line_tax", "line_total"])
            changed = True

        subtotal += line_sub
        tax_total += line_tax

    grand_total = subtotal + tax_total
    if changed or order.subtotal != subtotal or order.tax_total != tax_total or order.grand_total != grand_total:
        order.subtotal = subtotal
        order.tax_total = tax_total
        order.grand_total = grand_total
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])
    return order

def validate_order_items_payload(raw_items):
    if not raw_items or not isinstance(raw_items, list) or len(raw_items) == 0:
        return None, api_error("VALIDATION_ERROR", "items must be a non-empty list", ["items: at least one item required"], 400)

    seen_skus = set()
    validated_items = []
    for it in raw_items:
        if not isinstance(it, dict):
            return None, api_error("VALIDATION_ERROR", "Each item must be an object", ["item must be an object"], 400)
        sku = it.get("sku")
        qty_raw = it.get("quantity")
        if not sku or qty_raw is None:
            return None, api_error("VALIDATION_ERROR", "Each item requires sku and quantity", ["item missing sku or quantity"], 400)
        try:
            qty = parse_strict_int(qty_raw)
            if qty < 1:
                return None, api_error("VALIDATION_ERROR", "quantity must be integer >= 1", [f"quantity {qty} < 1"], 400)
        except (ValueError, TypeError):
            return None, api_error("VALIDATION_ERROR", "quantity must be an integer", ["quantity must be integer"], 400)

        if sku in seen_skus:
            return None, api_error("VALIDATION_ERROR", f"Duplicate SKU {sku} in items list", [f"duplicate sku {sku}"], 400)
        seen_skus.add(sku)

        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return None, api_error("VALIDATION_ERROR", f"Product with sku {sku} does not exist", [f"unknown sku {sku}"], 400)

        validated_items.append({"product": product, "quantity": qty})

    return validated_items, None

@api_view(["GET", "POST"])
def order_list(request):
    if request.method == "GET":
        qs = Order.objects.all().order_by("-created_at")
        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            try:
                qs = qs.filter(dealer_id=parse_strict_int(dealer_id))
            except ValueError:
                qs = qs.none()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        for o in page:
            if o.status == Order.STATUS_DRAFT:
                refresh_draft_order(o)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        idempotency_key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
        req_hash = hashlib.sha256(json.dumps(request.data, sort_keys=True, default=str).encode()).hexdigest()

        with serialized_db_op():
            with transaction.atomic():
                if idempotency_key:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(idempotency_key)])
                    rec = IdempotencyRecord.objects.filter(idempotency_key=idempotency_key).first()
                    if rec:
                        if rec.request_hash == req_hash:
                            return Response(rec.response_body, status=rec.response_status)
                        else:
                            return api_error("IDEMPOTENCY_CONFLICT", "Idempotency key reused with different body", ["Idempotency conflict"], 409)

                dealer_id_raw = request.data.get("dealer_id")
                if dealer_id_raw is None:
                    return api_error("VALIDATION_ERROR", "dealer_id is required", ["dealer_id is required"], 400)
                try:
                    dealer_id_val = parse_strict_int(dealer_id_raw)
                    dealer = Dealer.objects.get(id=dealer_id_val)
                except (ValueError, TypeError, Dealer.DoesNotExist):
                    return api_error("VALIDATION_ERROR", f"Dealer with id {dealer_id_raw} does not exist", ["dealer does not exist"], 400)

                validated_items, err_resp = validate_order_items_payload(request.data.get("items"))
                if err_resp:
                    return err_resp

                order_number = generate_order_number()
                items_calc, subtotal, tax_total, grand_total = calculate_order_totals(validated_items)
                order = Order.objects.create(
                    order_number=order_number,
                    dealer=dealer,
                    status=Order.STATUS_DRAFT,
                    subtotal=subtotal,
                    tax_total=tax_total,
                    grand_total=grand_total
                )
                for it in items_calc:
                    OrderItem.objects.create(
                        order=order,
                        product=it["product"],
                        sku=it["sku"],
                        name=it["name"],
                        quantity=it["quantity"],
                        unit_price=it["unit_price"],
                        line_subtotal=it["line_subtotal"],
                        line_tax=it["line_tax"],
                        line_total=it["line_total"]
                    )

                data = OrderSerializer(order).data
                if idempotency_key:
                    IdempotencyRecord.objects.create(
                        idempotency_key=idempotency_key,
                        request_hash=req_hash,
                        response_status=201,
                        response_body=data
                    )
                return Response(data, status=status.HTTP_201_CREATED)

@api_view(["GET", "PUT"])
def order_detail(request, order_number):
    if request.method == "GET":
        with serialized_db_op():
            try:
                order = Order.objects.get(order_number=order_number)
            except Order.DoesNotExist:
                return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)
            if order.status == Order.STATUS_DRAFT:
                refresh_draft_order(order)
            return Response(OrderSerializer(order).data)

    elif request.method == "PUT":
        with serialized_db_op():
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().get(order_number=order_number)
                except Order.DoesNotExist:
                    return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)

                if order.status != Order.STATUS_DRAFT:
                    return api_error("ORDER_LOCKED", f"Cannot edit order with status {order.status}", ["order is not DRAFT"], 409)

                validated_items, err_resp = validate_order_items_payload(request.data.get("items"))
                if err_resp:
                    return err_resp

                order.items.all().delete()
                items_calc, subtotal, tax_total, grand_total = calculate_order_totals(validated_items)
                order.subtotal = subtotal
                order.tax_total = tax_total
                order.grand_total = grand_total
                order.save()

                for it in items_calc:
                    OrderItem.objects.create(
                        order=order,
                        product=it["product"],
                        sku=it["sku"],
                        name=it["name"],
                        quantity=it["quantity"],
                        unit_price=it["unit_price"],
                        line_subtotal=it["line_subtotal"],
                        line_tax=it["line_tax"],
                        line_total=it["line_total"]
                    )
                return Response(OrderSerializer(order).data)

@api_view(["POST"])
def order_confirm(request, order_number):
    with serialized_db_op():
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(order_number=order_number)
            except Order.DoesNotExist:
                return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)

            if order.status != Order.STATUS_DRAFT:
                return api_error("INVALID_TRANSITION", f"Cannot confirm order in {order.status} state", [f"state {order.status} not allowed"], 409)

            # Lock products in deterministic ID order to avoid deadlocks
            items = list(order.items.select_related("product"))
            product_ids = sorted({item.product_id for item in items})
            locked_products = {
                p.id: p for p in Product.objects.filter(id__in=product_ids).order_by("id").select_for_update()
            }

            insufficient_details = []
            insufficient_messages = []
            for it in items:
                prod = locked_products[it.product_id]
                if prod.stock < it.quantity:
                    insufficient_details.append({
                        "sku": prod.sku,
                        "requested": it.quantity,
                        "available": prod.stock
                    })
                    insufficient_messages.append(
                        f"Insufficient stock for {prod.name}. Available: {prod.stock}, Requested: {it.quantity}"
                    )

            if insufficient_details:
                return Response(
                    {
                        "error": {
                            "code": "INSUFFICIENT_STOCK",
                            "message": ". ".join(insufficient_messages),
                            "details": insufficient_details
                        }
                    },
                    status=status.HTTP_409_CONFLICT
                )

            # Refresh and lock current product prices on confirm (§7.4)
            refresh_draft_order(order, locked_products=locked_products)

            # Deduct stock
            for it in items:
                prod = locked_products[it.product_id]
                prod.stock -= it.quantity
                prod.save(update_fields=["stock", "updated_at"])

            order.status = Order.STATUS_CONFIRMED
            order.save(update_fields=["status", "updated_at"])
            return Response(OrderSerializer(order).data)

@api_view(["POST"])
def order_dispatch(request, order_number):
    with serialized_db_op():
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(order_number=order_number)
            except Order.DoesNotExist:
                return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)

            if order.status != Order.STATUS_CONFIRMED:
                return api_error("INVALID_TRANSITION", f"Cannot dispatch order in {order.status} state", [f"state {order.status} not allowed"], 409)

            order.status = Order.STATUS_DISPATCHED
            order.save(update_fields=["status", "updated_at"])
            return Response(OrderSerializer(order).data)

@api_view(["POST"])
def order_deliver(request, order_number):
    with serialized_db_op():
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(order_number=order_number)
            except Order.DoesNotExist:
                return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)

            if order.status != Order.STATUS_DISPATCHED:
                return api_error("INVALID_TRANSITION", f"Cannot deliver order in {order.status} state", [f"state {order.status} not allowed"], 409)

            order.status = Order.STATUS_DELIVERED
            order.save(update_fields=["status", "updated_at"])
            return Response(OrderSerializer(order).data)

@api_view(["POST"])
def order_cancel(request, order_number):
    with serialized_db_op():
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(order_number=order_number)
            except Order.DoesNotExist:
                return api_error("NOT_FOUND", f"Order {order_number} not found", [f"order {order_number} not found"], 404)

            if order.status not in (Order.STATUS_DRAFT, Order.STATUS_CONFIRMED):
                return api_error("INVALID_TRANSITION", f"Cannot cancel order in {order.status} state", [f"state {order.status} not allowed"], 409)

            if order.status == Order.STATUS_CONFIRMED:
                # Restore stock in deterministic product ID order
                items = list(order.items.select_related("product"))
                product_ids = sorted({item.product_id for item in items})
                locked_products = {
                    p.id: p for p in Product.objects.filter(id__in=product_ids).order_by("id").select_for_update()
                }
                for it in items:
                    prod = locked_products[it.product_id]
                    prod.stock += it.quantity
                    prod.save(update_fields=["stock", "updated_at"])

            order.status = Order.STATUS_CANCELLED
            order.save(update_fields=["status", "updated_at"])
            return Response(OrderSerializer(order).data)
