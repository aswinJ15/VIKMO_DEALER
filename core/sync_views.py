import os
import time
import requests
from datetime import datetime
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Product
from .exceptions import api_error

def _parse_iso_ts(val):
    if not val or not isinstance(val, str):
        return ""
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
    except Exception:
        return val

@api_view(["POST"])
def sync_supplier(request):
    supplier_url = (os.environ.get("SUPPLIER_API_URL") or getattr(settings, "SUPPLIER_API_URL", "http://127.0.0.1:8099")).rstrip("/")
    api_key = os.environ.get("SUPPLIER_API_KEY") if "SUPPLIER_API_KEY" in os.environ else getattr(settings, "SUPPLIER_API_KEY", "")

    headers = {}
    if api_key:
        headers["X-Api-Key"] = api_key

    pages_fetched = 0
    requests_made = 0
    cursor = None
    all_raw_items = []

    # Fetch pages
    while True:
        url = f"{supplier_url}/v1/products"
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor

        success = False
        for attempt in range(5):
            requests_made += 1
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    pages_fetched += 1
                    items = data.get("items", [])
                    all_raw_items.extend(items)
                    cursor = data.get("next_cursor")
                    success = True
                    break
                elif resp.status_code == 429:
                    try:
                        retry_after = float(resp.headers.get("Retry-After", 1))
                    except (ValueError, TypeError):
                        retry_after = 1.0
                    time.sleep(max(0.1, retry_after) + 0.05)
                elif resp.status_code in (401, 403):
                    return api_error("SUPPLIER_UNAVAILABLE", "Supplier rejected API key", ["Unauthorized API key"], 502)
                elif resp.status_code in (500, 502, 503):
                    time.sleep(0.3 * (attempt + 1))
                else:
                    time.sleep(0.3 * (attempt + 1))
            except Exception:
                time.sleep(0.3 * (attempt + 1))

        if not success:
            return api_error("SUPPLIER_UNAVAILABLE", "Failed to fetch from supplier after 5 attempts", ["Supplier unavailable"], 502)

        if not cursor:
            break

    # Process items
    rejected = []
    valid_items = []
    for item in all_raw_items:
        supp_sku = item.get("supplier_sku", "")
        sku = item.get("sku")
        price_val = item.get("price")

        # Validate SKU
        if not sku or not isinstance(sku, str) or not sku.strip():
            rejected.append({"supplier_sku": supp_sku, "reason": "missing or invalid sku"})
            continue

        # Validate Price
        if price_val is None or isinstance(price_val, bool):
            rejected.append({"supplier_sku": supp_sku, "reason": "invalid price"})
            continue
        try:
            d_price = Decimal(str(price_val).strip())
            if not d_price.is_finite() or d_price <= 0 or d_price.as_tuple().exponent < -2:
                rejected.append({"supplier_sku": supp_sku, "reason": "invalid price"})
                continue
        except Exception:
            rejected.append({"supplier_sku": supp_sku, "reason": "invalid price"})
            continue

        valid_items.append(item)

    # Deduplicate by sku using latest updated_at
    latest_by_sku = {}
    for item in valid_items:
        sku = item["sku"].strip()
        up_at = _parse_iso_ts(item.get("updated_at", ""))
        if sku not in latest_by_sku:
            latest_by_sku[sku] = item
        else:
            prev_up_at = _parse_iso_ts(latest_by_sku[sku].get("updated_at", ""))
            if up_at > prev_up_at:
                latest_by_sku[sku] = item

    created_count = 0
    updated_count = 0
    unchanged_count = 0

    with transaction.atomic():
        existing_products = {p.sku: p for p in Product.objects.filter(sku__in=latest_by_sku.keys())}
        for sku, item in latest_by_sku.items():
            name = item.get("name", "").strip()
            cat = item.get("category", "").strip()
            brand = item.get("brand", "").strip()
            vf = item.get("vehicle_fitment", "").strip()
            desc = item.get("description", "").strip()
            price = Decimal(str(item.get("price"))).quantize(Decimal("0.01"))

            if sku not in existing_products:
                Product.objects.create(
                    sku=sku,
                    name=name,
                    category=cat,
                    brand=brand,
                    vehicle_fitment=vf,
                    description=desc,
                    price=price,
                    stock=0
                )
                created_count += 1
            else:
                p = existing_products[sku]
                diff = (
                    p.name != name or
                    p.category != cat or
                    p.brand != brand or
                    p.vehicle_fitment != vf or
                    p.description != desc or
                    p.price != price
                )
                if diff:
                    p.name = name
                    p.category = cat
                    p.brand = brand
                    p.vehicle_fitment = vf
                    p.description = desc
                    p.price = price
                    p.save()
                    updated_count += 1
                else:
                    unchanged_count += 1

    return Response({
        "created": created_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made
    })
