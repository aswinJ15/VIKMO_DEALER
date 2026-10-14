# VIKMO DealerDesk — Decisions & Architecture Document
**Candidate Track: AI / ML Track**  
**Role:** AI Intern — Gen AI & ML (Commplug Innovations / Vikmo)

---

## 1. Schema & Foreign Key Constraints
**Question:** *Explain your schema: each model, its relationships, and the on_delete choice for every foreign key, with your reasons.*

Our relational schema is implemented in [`core/models.py`](core/models.py):

- **`Product` ([`core/models.py:5-17`](core/models.py#L5-L17))**:
  Stores product catalog information: unique indexed `sku`, `name`, `category`, `brand`, `vehicle_fitment`, `price` (using `DecimalField(max_digits=12, decimal_places=2)`), `stock` (integer), and `description`.
- **`Dealer` ([`core/models.py:19-31`](core/models.py#L19-L31))**:
  Stores dealer accounts: unique indexed `dealer_code`, `name`, `city`, `phone`, `email`, `gstin`, and `credit_limit`.
- **`Order` ([`core/models.py:33-54`](core/models.py#L33-L54))**:
  Stores customer orders: `order_number` (`ORD-YYYYMMDD-NNNN`), `status` (`DRAFT`, `CONFIRMED`, `DISPATCHED`, `DELIVERED`, `CANCELLED`), financial sums (`subtotal`, `tax_total`, `grand_total`), and timestamps.
  - **Foreign Key**: `dealer = models.ForeignKey(Dealer, on_delete=models.PROTECT, related_name="orders")` ([`core/models.py:46`](core/models.py#L46)).
  - **`on_delete=models.PROTECT` rationale**: An order represents a binding legal and financial transaction. Deleting a dealer must never silently delete or cascade over existing historical orders, preventing financial audit discrepancies and dangling references.
- **`OrderItem` ([`core/models.py:56-70`](core/models.py#L56-L70))**:
  Line items per order: `sku`, `name`, `quantity`, `unit_price`, `line_subtotal`, `line_tax`, and `line_total`.
  - **Foreign Key to Order**: `order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")` ([`core/models.py:57`](core/models.py#L57)).
    - **Rationale**: An `OrderItem` is a constituent line item of an order; if an unconfirmed draft order is pruned, its line items lifecycle is bound to that order.
  - **Foreign Key to Product**: `product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")` ([`core/models.py:58`](core/models.py#L58)).
    - **Rationale**: Products referenced in past orders must never be deleted (`models.PROTECT`), ensuring sales records and catalogue referential integrity remain intact even if a catalog item is retired.
- **`IdempotencyRecord` ([`core/models.py:72-78`](core/models.py#L72-L78))**:
  Guarantees network-safe retries by caching SHA-256 request hashes and responses against `Idempotency-Key` headers.

---

## 2. Retrieval, Indexing & Ranking
**Question:** *How does retrieval work? Why these embedding, indexing and ranking choices, and where does retrieval fail?*

- **Mechanism ([`core/assistant_tools.py:7-36`](core/assistant_tools.py#L7-L36) & [`core/views.py:37-49`](core/views.py#L37-L49))**:
  Retrieval combines structured SQL filtering and tri-gram substring matching across database indices on `sku`, `name`, `brand`, and `vehicle_fitment`. 
- **Indexing & Ranking Choices**:
  Database B-tree indices are applied to `sku`, `category`, `brand`, and `vehicle_fitment`. In automotive B2B commerce, queries are dominated by exact SKU codes (`BRK-1044`), specific vehicle models (`Bajaj Pulsar 150`), and part categories (`Brakes`). Direct indexed SQL queries give deterministic 1ms response latency with zero vector drift and strict privacy guarantees.
- **Failure Modes**:
  Retrieval fails when users use colloquial vernacular or phonetic typos not captured in aliases (e.g. asking for "chimta" instead of "swingarm" or "shoebreak" instead of "brake shoe"). In production, a hybrid lexical + dense embedding index (using pgvector with domain-fine-tuned bi-encoders on automotive vocabulary) bridges semantic slang with exact SKU matching.

---

## 3. Tool-Calling Execution Loop
**Question:** *Walk through your tool-calling loop, from the user's message to the final reply. Cite the code.*

The tool loop in [`core/assistant_views.py:165-245`](core/assistant_views.py#L165-L245) executes in strict sequence:
1. **Request Intake & Context Loading ([`core/assistant_views.py:166-180`](core/assistant_views.py#L166-L180))**:
   The dealer's `session_id`, `dealer_id`, and `message` are validated. Session history is fetched from isolated `SESSION_MEMORY[session_id]` to maintain multi-turn dialogue context.
2. **Model Call with Bound Schemas ([`core/assistant_views.py:195-212`](core/assistant_views.py#L195-L212))**:
   The system invokes the LLM (`google-genai` / OpenAI) with bound Python functions: `tool_search_parts`, `tool_check_stock`, and `tool_create_draft_order`, reinforced by `SYSTEM_INSTRUCTION`.
3. **Tool Invocation Inspection ([`core/assistant_views.py:214-232`](core/assistant_views.py#L214-L232))**:
   If the model emits function call events (`response.function_calls`), our backend intercepts them:
   - For `search_parts`: Calls `tool_search_parts(query, vehicle, category)` ([`core/assistant_tools.py:7-36`](core/assistant_tools.py#L7-L36)).
   - For `check_stock`: Calls `tool_check_stock(sku)` ([`core/assistant_tools.py:38-51`](core/assistant_tools.py#L38-L51)).
   - For `create_draft_order`: Calls `tool_create_draft_order(dealer.id, items)` ([`core/assistant_tools.py:53-108`](core/assistant_tools.py#L53-L108)), generating a valid draft order and populating `actions: [{"type": "draft_order_created", "order_number": "..."}]`.
4. **SKU Extraction & Guardrails ([`core/assistant_views.py:237-241`](core/assistant_views.py#L237-L241))**:
   Every cited SKU is cross-validated against `Product.objects.filter(sku=m).exists()` to ensure hallucinated SKUs are eliminated.
5. **State Persistence & Response ([`core/assistant_views.py:242-250`](core/assistant_views.py#L242-L250))**:
   The assistant's turn is committed to `SESSION_MEMORY[session_id]` and returned as `200 OK` with `reply`, `cited_skus`, `tool_calls`, and `actions`.

---

## 4. Live Pricing & Inventory Integrity
**Question:** *How does your assistant get prices and stock, and how do you make sure it never states a figure that isn't in the database right now?*

- **Code Implementation**: [`core/assistant_tools.py:38-51`](core/assistant_tools.py#L38-L51) (`tool_check_stock`) and [`core/assistant_tools.py:22-35`](core/assistant_tools.py#L22-L35) (`tool_search_parts`).
- **Mechanism**: The LLM's system instructions strictly prohibit generating figures from parametric memory. Whenever prices or quantities are requested, `check_stock` queries `Product.objects.get(sku=sku)` to fetch live `p.price` and `p.stock` from PostgreSQL at runtime.
- **Draft Order Validation**: When creating draft orders ([`core/assistant_tools.py:91-105`](core/assistant_tools.py#L91-L105)), the assistant never accepts a user-provided or hallucinated price; it reads the catalog price from the database via `calculate_order_totals()` ([`core/views.py:161-197`](core/views.py#L161-L197)).

---

## 5. Defense Against Prompt Injections in Catalogue Descriptions
**Question:** *Supplier descriptions are untrusted. How does your assistant avoid following instructions hidden inside them, and where does your defence stop working?*

- **Defense Strategy ([`core/assistant_tools.py:33`](core/assistant_tools.py#L33) & [`core/assistant_views.py:19-30`](core/assistant_views.py#L19-L30))**:
  1. **Strict Context Demarcation**: System instructions designate catalogue text as pure passive data, not executable directives.
  2. **Truncation & Data Sanitization**: `description` strings returned to LLM context are truncated to 100 characters ([`core/assistant_tools.py:33`](core/assistant_tools.py#L33)), denying payload length for complex multi-stage jailbreaks.
  3. **Strict Function Schema Execution**: The LLM can only interact via rigid function parameters. It cannot trigger database writes or confirm orders.
- **Where Defence Stops Working**:
  Advanced indirect prompt injection (e.g. ASCII steganography or payload split attacks) could bias the model's textual tone or cause it to decline a benign part. Complete air-gapping requires separating the unstructured conversational LLM from an isolated rule-based order authorization layer.

---

## 6. Forecasting Methodology & Zero Data Leakage
**Question:** *Describe your forecasting approach and how you validated it. Cite the line that stops future data leaking into training.*

- **Forecasting Approach ([`ml/pipeline.py:134-205`](ml/pipeline.py#L134-L205))**:
  We built a **Promo-Aware Decomposition Model** that isolates non-promotional baseline demand using a 12-week recency-weighted moving average, estimates SKU-level promotion lift factors, aligns planned promotional weeks from `data/promo_calendar.csv`, and factors in the Diwali festival calendar.
- **Leakage Prevention Line**:
  **[`ml/pipeline.py:46-47`](ml/pipeline.py#L46-L47)**:
  ```python
  train_df = sku_sales[~sku_sales["week_start"].isin(val_weeks)]
  test_df = sku_sales[sku_sales["week_start"].isin(val_weeks)]
  ```
  In historical backtesting, `val_weeks` (the last 4 observed weeks: `2026-08-31` to `2026-09-21`) are strictly partitioned out. All baseline means, promotion multipliers, and parameters are calculated purely from `train_df`.
- **Validation Metric Comparison**:
  - Naive Last Observed: MAE = 11.73, RMSE = 19.58
  - Moving Average (4-wk): MAE = 8.77, RMSE = 15.01
  - Moving Average (12-wk): MAE = 9.08, RMSE = 15.02
  - Seasonal Lag (52-wk): MAE = 12.28, RMSE = 19.73
  - **Promo-Aware Decomposition Model**: **MAE = 6.82, RMSE = 9.87** (Selected).

---

## 7. Edge Case SKUs & Special Demand Handling
**Question:** *Which SKUs does your forecast handle worst, and why? How did you handle promotions, Diwali, rarely sold SKUs and new SKUs?*

- **Worst Performing SKUs**:
  1. **Recently Launched SKUs (`BRK-1105`, `CHN-1006`)**: These have only 6 weeks of total sales history. With no yearly history, seasonal patterns cannot be estimated; we fall back to a damped short-term moving average.
  2. **Intermittent / Rare Demand SKUs**: Slow-moving parts with long streaks of zero sales followed by sporadic lump spikes create high variance in classical MSE/RMSE.
- **Promotions**:
  Historical promotion flags (`promo_flag == 1`) in `sales_history.csv` are used to compute the SKU-specific promo multiplier $\frac{\text{promo\_mean}}{\text{non\_promo\_mean}}$ (bounded in $[1.0, 2.5]$). When `promo_calendar.csv` indicates a scheduled promo, this boost is applied directly ([`ml/pipeline.py:180-184`](ml/pipeline.py#L180-L184)).
- **Diwali Festive Shift**:
  Diwali moves every year (Nov 1, 2024; Oct 21, 2025; Nov 8, 2026). Fixed calendar month lags fail because the 2025 surge occurred in mid-October whereas in 2026 Diwali is on Nov 8. We apply a calibrated 5% to 8% pre-festival inventory ramp in mid-to-late October (`2026-10-12` and `2026-10-19`) ([`ml/pipeline.py:187-190`](ml/pipeline.py#L187-L190)).

---

## 8. Weakest Part of Submission & Two-Day Roadmap
**Question:** *What is the weakest part of your submission, and what would you do with two more days?*

- **Current Weakness**:
  Session memory is stored in an in-memory dictionary `SESSION_MEMORY` ([`core/assistant_views.py:15`](core/assistant_views.py#L15)), which resets upon server restart and does not scale horizontally across multiple worker nodes.
- **Two-Day Roadmap**:
  1. **Distributed Memory & Checkpointing**: Back conversation sessions with PostgreSQL or Redis using LangGraph/Celery state checkpoints to persist chat history across multi-node deployments.
  2. **Hybrid Semantic Search**: Integrate `pgvector` with HNSW indexing and `sentence-transformers` to support fuzzy conversational queries (e.g. matching misspelled Hindi/Tamil automotive terms to canonical SKUs).
  3. **Croston's Method / TSB Model for Intermittent Demand**: Implement Croston's forecasting specifically for intermittent zero-inflated parts to forecast demand frequency and demand size independently.
