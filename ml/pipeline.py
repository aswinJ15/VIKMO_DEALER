import os
import csv
import math
import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forecast.csv")

FORECAST_WEEKS = ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"]

def load_data():
    sales = pd.read_csv(os.path.join(DATA_DIR, "sales_history.csv"))
    sales["week_start"] = pd.to_datetime(sales["week_start"])
    promo = pd.read_csv(os.path.join(DATA_DIR, "promo_calendar.csv"))
    promo["week_start"] = pd.to_datetime(promo["week_start"])
    lead = pd.read_csv(os.path.join(DATA_DIR, "lead_times.csv"))
    festival = pd.read_csv(os.path.join(DATA_DIR, "festival_calendar.csv"))
    festival["date"] = pd.to_datetime(festival["date"])
    return sales, promo, lead, festival

def evaluate_models():
    """
    Chronological backtesting on historical data.
    Test period: the last 4 observed weeks (2026-08-31, 2026-09-07, 2026-09-14, 2026-09-21)
    Training period: all weeks prior to 2026-08-31.
    We compare:
    1. Naive Last Observed (Persistent)
    2. Moving Average (4-week)
    3. Moving Average (12-week)
    4. Seasonal Lag (52-week lag if available, fallback to recent MA)
    5. Promo-Aware Trend-Adjusted Exponential Moving Average with promo uplift
    """
    sales, promo_cal, lead, festival = load_data()
    val_weeks = pd.to_datetime(["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21"])
    
    skus = lead["sku"].unique()
    
    actuals = []
    preds_naive = []
    preds_ma4 = []
    preds_ma12 = []
    preds_seasonal = []
    preds_promo_model = []
    
    for sku in skus:
        sku_sales = sales[sales["sku"] == sku].sort_values("week_start")
        train_df = sku_sales[~sku_sales["week_start"].isin(val_weeks)]
        test_df = sku_sales[sku_sales["week_start"].isin(val_weeks)]
        
        if len(test_df) == 0:
            continue
            
        # 1. Naive Last Observed
        last_val = train_df["units_sold"].iloc[-1] if len(train_df) > 0 else 0
        
        # 2. MA4
        ma4 = train_df["units_sold"].tail(4).mean() if len(train_df) >= 4 else last_val
        
        # 3. MA12
        ma12 = train_df["units_sold"].tail(12).mean() if len(train_df) >= 12 else (train_df["units_sold"].mean() if len(train_df) > 0 else 0)
        
        # Calculate promo uplift ratio historically in train
        non_promo_mean = train_df[train_df["promo_flag"] == 0]["units_sold"].mean()
        promo_mean = train_df[train_df["promo_flag"] == 1]["units_sold"].mean()
        if pd.notna(non_promo_mean) and non_promo_mean > 0 and pd.notna(promo_mean) and promo_mean > non_promo_mean:
            promo_multiplier = promo_mean / non_promo_mean
        else:
            promo_multiplier = 1.25 # conservative domain standard
            
        # Recent baseline without promo distortion
        recent_train = train_df.tail(8)
        recent_clean = recent_train[recent_train["promo_flag"] == 0]
        if len(recent_clean) > 0:
            clean_base = recent_clean["units_sold"].mean()
        else:
            clean_base = recent_train["units_sold"].mean()
            
        for _, row in test_df.iterrows():
            act = row["units_sold"]
            actuals.append(act)
            preds_naive.append(max(0.0, float(last_val)))
            preds_ma4.append(max(0.0, float(ma4)))
            preds_ma12.append(max(0.0, float(ma12)))
            
            # Seasonal 52-week lag
            w = row["week_start"]
            lag52_w = w - pd.Timedelta(weeks=52)
            lag_row = train_df[train_df["week_start"] == lag52_w]
            if len(lag_row) > 0:
                s_pred = lag_row["units_sold"].values[0]
            else:
                s_pred = ma4
            preds_seasonal.append(max(0.0, float(s_pred)))
            
            # Promo model: base * (promo_multiplier if promo_flag == 1 else 1.0) with exponential smoothing
            p_pred = clean_base * (promo_multiplier if row["promo_flag"] == 1 else 1.0)
            preds_promo_model.append(max(0.0, float(p_pred)))

    results = {
        "Model": ["Naive Last Observed", "Moving Average (4-wk)", "Moving Average (12-wk)", "Seasonal Lag (52-wk)", "Promo-Aware Decomposition Model"],
        "MAE": [
            mean_absolute_error(actuals, preds_naive),
            mean_absolute_error(actuals, preds_ma4),
            mean_absolute_error(actuals, preds_ma12),
            mean_absolute_error(actuals, preds_seasonal),
            mean_absolute_error(actuals, preds_promo_model)
        ],
        "RMSE": [
            np.sqrt(mean_squared_error(actuals, preds_naive)),
            np.sqrt(mean_squared_error(actuals, preds_ma4)),
            np.sqrt(mean_squared_error(actuals, preds_ma12)),
            np.sqrt(mean_squared_error(actuals, preds_seasonal)),
            np.sqrt(mean_squared_error(actuals, preds_promo_model))
        ]
    }
    res_df = pd.DataFrame(results)
    print("=== Chronological Validation Results ===")
    print(res_df.to_string(index=False))
    return res_df

def generate_forecasts():
    sales, promo_cal, lead, festival = load_data()
    skus = lead["sku"].unique()
    
    rows = []
    for sku in skus:
        sku_sales = sales[sales["sku"] == sku].sort_values("week_start")
        
        # Calculate historical promo multiplier
        non_promo = sku_sales[sku_sales["promo_flag"] == 0]
        promo_hist = sku_sales[sku_sales["promo_flag"] == 1]
        
        non_promo_mean = non_promo["units_sold"].mean() if len(non_promo) > 0 else 1.0
        promo_mean = promo_hist["units_sold"].mean() if len(promo_hist) > 0 else non_promo_mean
        
        if non_promo_mean > 0 and promo_mean > non_promo_mean:
            promo_ratio = min(2.5, promo_mean / non_promo_mean) # bound reasonable boost
        else:
            promo_ratio = 1.25
            
        # Recent baseline calculation using exponentially weighted / recency weighted mean
        recent_12 = sku_sales.tail(12)
        # un-boost promo weeks in recent history to get true clean baseline
        cleaned_units = []
        weights = []
        for i, (_, r) in enumerate(recent_12.iterrows()):
            w = 1.0 + (i / 12.0) # slightly weight more recent weeks
            val = r["units_sold"]
            if r["promo_flag"] == 1 and promo_ratio > 1.0:
                val = val / promo_ratio
            cleaned_units.append(val)
            weights.append(w)
            
        clean_baseline = np.average(cleaned_units, weights=weights) if cleaned_units else sku_sales["units_sold"].mean()
        clean_baseline = max(0.1, clean_baseline)
        
        # Check festival / seasonal lift for Diwali (Diwali 2026 is Nov 8, 2026; late October sees ramp-up)
        # Oct 12 and Oct 19 are 3-4 weeks before Diwali
        for w_str in FORECAST_WEEKS:
            w_date = pd.to_datetime(w_str)
            p_flag = 0
            p_sub = promo_cal[(promo_cal["sku"] == sku) & (promo_cal["week_start"] == w_date)]
            if len(p_sub) > 0:
                p_flag = int(p_sub["promo_flag"].iloc[0])
                
            multiplier = promo_ratio if p_flag == 1 else 1.0
            
            # Festive pre-Diwali lift factor (modest 5% to 10% lift in late Oct leading into Nov Diwali)
            if w_str == "2026-10-12":
                multiplier *= 1.05
            elif w_str == "2026-10-19":
                multiplier *= 1.08
                
            pred_units = round(clean_baseline * multiplier, 1)
            pred_units = max(0.0, pred_units)
            rows.append({
                "sku": sku,
                "week_start": w_str,
                "forecast_units": pred_units
            })
            
    df_out = pd.DataFrame(rows)
    # Ensure exact columns: sku,week_start,forecast_units
    df_out = df_out[["sku", "week_start", "forecast_units"]]
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    df_out.to_csv(OUTPUT_PATH, index=False)
    print(f"Generated {len(df_out)} forecast rows at {OUTPUT_PATH}")

if __name__ == "__main__":
    evaluate_models()
    generate_forecasts()
