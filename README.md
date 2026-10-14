# VIKMO DealerDesk — AI/ML Track Submission

VIKMO DealerDesk is an enterprise-grade backend system built for two-wheeler parts dealerships. It delivers high-throughput product catalogue management, inventory tracking, draft-order lifecycle handling, a Generative AI Dealer Assistant with real database tool calling, supplier catalog synchronization, and a machine learning demand forecasting & automated reorder pipeline.

---

## 1. Project Overview & Architecture

- **Backend Framework**: Django 5.x & Django REST Framework (DRF)
- **Database**: PostgreSQL (strict `DecimalField` accounting, transactional `select_for_update` locking for concurrency protection, zero overselling)
- **Generative AI Assistant**: Multi-provider support (Gemini, OpenAI, Anthropic, Groq) with function/tool execution against the database (`search_parts`, `check_stock`, `create_draft_order`) and per-session isolation
- **Machine Learning Pipeline**: Chronologically validated, promo-aware demand forecasting across 40 target SKUs over 4 future weeks (160 predictions in `ml/forecast.csv`), coupled with dynamic safety stock and reorder point calculations
- **Supplier Sync**: Resilient upstream catalog synchronization supporting cursor pagination, exponential backoff retries, and deduplication

---

## 2. Technology Stack

- **Python**: 3.11+ / 3.12
- **Database**: PostgreSQL 15+
- **Key Libraries**: Django, djangorestframework, psycopg, dj-database-url, requests, pandas, numpy, scikit-learn, google-genai

---

## 3. Installation & Local Setup

### 3.1 Prerequisites
- Python 3.11 or newer
- PostgreSQL running locally or hosted (Neon / Supabase)

### 3.2 macOS / Linux Setup
```bash
# 1. Clone repository & navigate to root
cd vikmo-dealerdesk

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your PostgreSQL DATABASE_URL, e.g.:
# DATABASE_URL=postgres://localhost:5432/dealerdesk
```

### 3.3 Database Migration & Seeding
```bash
python manage.py migrate --noinput

# Run seed_data (loads 600 catalogue products and 25 dealers idempotently)
python manage.py seed_data
python manage.py seed_data
```

### 3.4 Starting the Server
```bash
python manage.py runserver 0.0.0.0:8000 --noreload
```

Verify health:
```bash
curl http://localhost:8000/api/health/
# -> {"status": "ok"}
```

---

## 4. Machine Learning Forecasting & Validation

The forecasting pipeline generates demand predictions for all 40 SKUs across the four target weeks (`2026-09-28`, `2026-10-05`, `2026-10-12`, `2026-10-19`).

### 4.1 Run the ML Pipeline
```bash
python ml/pipeline.py
```
This performs chronological backtesting across 5 models, displays performance metrics, and writes 160 rows to `ml/forecast.csv`.

Validation results summary:
- **Naive Last Observed**: MAE = 11.73, RMSE = 19.58
- **Moving Average (4-wk)**: MAE = 8.77, RMSE = 15.01
- **Moving Average (12-wk)**: MAE = 9.08, RMSE = 15.02
- **Seasonal Lag (52-wk)**: MAE = 12.28, RMSE = 19.73
- **Promo-Aware Decomposition Model**: **MAE = 6.82**, **RMSE = 9.87** (Selected Model)

Detailed model choices and methodology are documented in `DECISIONS.md`.

---

## 5. Automated Testing & Verification

Run the comprehensive smoke test suite provided:

```bash
# Check AI/ML track requirements:
python smoke_test.py --base-url http://localhost:8000 --track aiml

# Check all tracks including order lifecycle and supplier sync:
python smoke_test.py --base-url http://localhost:8000 --all
```

**Results**: All checks pass (`12/12` passed on `--all`, `10/10` passed on `--track aiml`).

---

## 6. Optional Docker Deployment

To launch the entire application with PostgreSQL using Docker:

```bash
docker-compose up --build
```
This automatically runs database migrations, executes idempotent data seeding twice, and launches the server on `http://localhost:8000`.

---

## 7. Five-Minute Demonstration Plan

1. **Minute 1: Setup & Health Check**
   - Demonstrate clean startup and `GET /api/health/`.
   - Show idempotent seeding running twice without duplicates (600 products, 25 dealers).
2. **Minute 2: Core Catalogue, Inventory & Order Management**
   - Demonstrate product listing with vehicle fitment filter (`Bajaj Pulsar 150`).
   - Update inventory using `PUT /api/inventory/{sku}/`.
   - Create a DRAFT order, verify GST calculation (18% rounded half up per line) and paise accuracy.
   - Confirm the order and show atomic stock deduction.
3. **Minute 3: Generative AI Assistant with Tool Calling**
   - Send `POST /api/assistant/chat/` querying parts for Pulsar 150.
   - Show live execution of `search_parts`, `check_stock`, and `create_draft_order`.
   - Inspect the returned `cited_skus`, `tool_calls`, and generated draft order in `actions`.
4. **Minute 4: ML Forecasting & Reorder Suggestions**
   - Explain the chronological validation results and baseline comparison in `DECISIONS.md`.
   - Demonstrate `GET /api/forecast/{sku}/` returning the 4 forecast weeks.
   - Demonstrate `GET /api/reorder-suggestions/` showing live stock, safety stock calculation ($\lceil 1.65 \times \sigma \times \sqrt{L} \rceil$), and recommended reorder quantities.
5. **Minute 5: Automated Smoke Test Verification**
   - Run `python smoke_test.py --base-url http://localhost:8000 --all`.
   - Show all checks passing (12/12).

---

## 8. Video Walkthrough Link

- **Demo Video URL**: [Watch the 5-Minute Code Walkthrough Video](https://drive.google.com/file/d/1Tmn94E5N9ihM1xvoTNKIUD-8Sp47kSin/view?usp=drive_link)
 
