import re
from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers
from .models import Product, Dealer, Order, OrderItem

MONEY_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")

def format_money(val):
    if val is None:
        return "0.00"
    d = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}"

def parse_price_value(value):
    if value is None or isinstance(value, bool):
        raise serializers.ValidationError("Price must be a positive number with at most 2 decimal places")
    s = str(value).strip()
    if not MONEY_REGEX.match(s):
        raise serializers.ValidationError("Price must be a positive decimal with up to 2 decimal places")
    d = Decimal(s)
    if not d.is_finite() or d <= 0:
        raise serializers.ValidationError("Price must be greater than 0")
    return d.quantize(Decimal("0.01"))

class ProductSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = ["sku", "name", "category", "brand", "vehicle_fitment", "price", "stock", "description"]

    def get_price(self, obj):
        return format_money(obj.price)

class ProductCreateSerializer(serializers.ModelSerializer):
    price = serializers.JSONField()
    vehicle_fitment = serializers.CharField(required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = Product
        fields = ["sku", "name", "category", "brand", "vehicle_fitment", "price", "description"]

    def validate_price(self, value):
        return parse_price_value(value)

    def create(self, validated_data):
        validated_data["stock"] = 0
        return super().create(validated_data)

class ProductUpdateSerializer(serializers.ModelSerializer):
    price = serializers.JSONField(required=False)

    class Meta:
        model = Product
        fields = ["name", "category", "brand", "vehicle_fitment", "price", "description"]

    def validate_price(self, value):
        if value is not None:
            return parse_price_value(value)
        return value

class DealerSerializer(serializers.ModelSerializer):
    credit_limit = serializers.SerializerMethodField()

    class Meta:
        model = Dealer
        fields = ["id", "dealer_code", "name", "city", "phone", "email", "gstin", "credit_limit"]

    def get_credit_limit(self, obj):
        return format_money(obj.credit_limit)

class DealerCreateSerializer(serializers.ModelSerializer):
    credit_limit = serializers.CharField(required=False, default="0.00")

    class Meta:
        model = Dealer
        fields = ["dealer_code", "name", "city", "phone", "email", "gstin", "credit_limit"]

    def validate_credit_limit(self, value):
        if value:
            try:
                d = Decimal(str(value))
                if d < 0:
                    raise serializers.ValidationError("Credit limit cannot be negative")
                return d
            except Exception:
                raise serializers.ValidationError("Invalid credit limit format")
        return Decimal("0.00")

class OrderItemSerializer(serializers.ModelSerializer):
    unit_price = serializers.SerializerMethodField()
    line_subtotal = serializers.SerializerMethodField()
    line_tax = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["sku", "name", "quantity", "unit_price", "line_subtotal", "line_tax", "line_total"]

    def get_unit_price(self, obj):
        return format_money(obj.unit_price)

    def get_line_subtotal(self, obj):
        return format_money(obj.line_subtotal)

    def get_line_tax(self, obj):
        return format_money(obj.line_tax)

    def get_line_total(self, obj):
        return format_money(obj.line_total)

class OrderSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(source="dealer.id")
    items = OrderItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number", "dealer_id", "status", "items",
            "subtotal", "tax_total", "grand_total",
            "created_at", "updated_at"
        ]

    def get_subtotal(self, obj):
        return format_money(obj.subtotal)

    def get_tax_total(self, obj):
        return format_money(obj.tax_total)

    def get_grand_total(self, obj):
        return format_money(obj.grand_total)

    def get_created_at(self, obj):
        return obj.created_at.isoformat()

    def get_updated_at(self, obj):
        return obj.updated_at.isoformat()
