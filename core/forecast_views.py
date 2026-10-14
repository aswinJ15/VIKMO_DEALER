import os
import csv
import math
import pandas as pd
import numpy as np
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Product
from .exceptions import api_error

@api_view(["GET"])
def forecast_detail(request, sku):
    forecast_path = os.path.join(settings.BASE_DIR, "ml", "forecast.csv")
    if not os.path.exists(forecast_path):
        return api_error("NOT_FOUND", "Forecast file not found", ["ml/forecast.csv not found"], 404)

    rows = []
    with open(forecast_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r["sku"] == sku:
                rows.append({
                    "week_start": r["week_start"],
                    "forecast_units": float(r["forecast_units"])
                })

    if not rows:
        return api_error("NOT_FOUND", f"SKU {sku} not found in forecast", [f"sku {sku} not in forecast"], 404)

    rows.sort(key=lambda x: x["week_start"])
    return Response({
        "sku": sku,
        "forecast": rows
    })

@api_view(["GET"])
def reorder_suggestions(request):
    data_dir = settings.DATA_DIR
    lead_path = os.path.join(data_dir, "lead_times.csv")
    sales_path = os.path.join(data_dir, "sales_history.csv")
    forecast_path = os.path.join(settings.BASE_DIR, "ml", "forecast.csv")

    if not os.path.exists(lead_path) or not os.path.exists(forecast_path):
        return api_error("NOT_FOUND", "Required forecasting data files missing", [], 404)

    # 1. Load lead times
    lead_df = pd.read_csv(lead_path) # sku, lead_time_weeks
    lead_dict = dict(zip(lead_df["sku"], lead_df["lead_time_weeks"]))

    # 2. Load forecasts
    forecast_df = pd.read_csv(forecast_path)
    forecast_df = forecast_df.sort_values(["sku", "week_start"])

    # 3. Load sales history and compute sample std over most recent 12 weeks
    sales_df = pd.read_csv(sales_path)
    sales_df = sales_df.sort_values(["sku", "week_start"])

    # 4. Fetch live inventory from database
    products = {p.sku: p.stock for p in Product.objects.filter(sku__in=lead_df["sku"])}

    results = []
    for sku, L_raw in lead_dict.items():
        L = int(L_raw)
        # Current stock
        curr_stock = int(products.get(sku, 0))

        # Forecast units for first L weeks
        sku_fc = forecast_df[forecast_df["sku"] == sku]["forecast_units"].tolist()
        first_l_fc = sku_fc[:L]
        ltd = round(float(sum(first_l_fc)), 4)

        # Historical sigma (sample standard deviation with ddof=1 on up to 12 recent weeks)
        sku_hist = sales_df[sales_df["sku"] == sku].tail(12)["units_sold"].tolist()
        if len(sku_hist) > 1:
            sigma = float(np.std(sku_hist, ddof=1))
        else:
            sigma = 0.0

        # Safety stock = ceil(1.65 * sigma * sqrt(L))
        safety_stock = int(math.ceil(1.65 * sigma * math.sqrt(L)))

        # Reorder point = lead_time_demand + safety_stock
        reorder_point = round(ltd + safety_stock, 4)

        # Reorder qty = max(0, ceil(reorder_point - current_stock))
        reorder_qty = max(0, int(math.ceil(reorder_point - curr_stock)))

        results.append({
            "sku": sku,
            "current_stock": curr_stock,
            "lead_time_weeks": L,
            "lead_time_demand": ltd,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_qty": reorder_qty
        })

    return Response({"results": results})
