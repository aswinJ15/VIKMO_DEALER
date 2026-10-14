from django.urls import path
from . import views
from . import sync_views
from . import forecast_views
from . import assistant_views

urlpatterns = [
    # Health
    path("health/", views.health),

    # Products
    path("products/", views.product_list),
    path("products/<str:sku>/", views.product_detail),

    # Inventory
    path("inventory/<str:sku>/", views.inventory_update),

    # Dealers
    path("dealers/", views.dealer_list),
    path("dealers/<int:pk>/", views.dealer_detail),

    # Orders
    path("orders/", views.order_list),
    path("orders/<str:order_number>/", views.order_detail),
    path("orders/<str:order_number>/confirm/", views.order_confirm),
    path("orders/<str:order_number>/dispatch/", views.order_dispatch),
    path("orders/<str:order_number>/deliver/", views.order_deliver),
    path("orders/<str:order_number>/cancel/", views.order_cancel),

    # Sync
    path("sync/supplier/", sync_views.sync_supplier),

    # Assistant
    path("assistant/chat/", assistant_views.assistant_chat),

    # Forecast & Reorder
    path("forecast/<str:sku>/", forecast_views.forecast_detail),
    path("reorder-suggestions/", forecast_views.reorder_suggestions),
]
