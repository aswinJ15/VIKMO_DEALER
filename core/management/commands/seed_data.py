import csv
import os
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import Product, Dealer

class Command(BaseCommand):
    help = "Load the files in data/ into the database. Must be safe to run more than once."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR
        catalogue_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not catalogue_path.exists():
            self.stderr.write(self.style.ERROR(f"catalogue.csv not found at {catalogue_path}"))
            return

        # 1. Seed Catalogue (600 products)
        with open(catalogue_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row["sku"].strip()
                price = Decimal(row["price"].strip())
                stock = int(row["stock"].strip())
                Product.objects.update_or_create(
                    sku=sku,
                    defaults={
                        "name": row["name"].strip(),
                        "category": row["category"].strip(),
                        "brand": row["brand"].strip(),
                        "vehicle_fitment": row["vehicle_fitment"].strip(),
                        "price": price,
                        "stock": stock,
                        "description": row.get("description", "").strip(),
                    }
                )

        # 2. Seed Dealers (25 dealers)
        if dealers_path.exists():
            with open(dealers_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row["dealer_code"].strip()
                    credit_limit = Decimal(row["credit_limit"].strip()) if row.get("credit_limit") else Decimal("0.00")
                    Dealer.objects.update_or_create(
                        dealer_code=code,
                        defaults={
                            "name": row["name"].strip(),
                            "city": row.get("city", "").strip(),
                            "phone": row.get("phone", "").strip(),
                            "email": row.get("email", "").strip(),
                            "gstin": row.get("gstin", "").strip(),
                            "credit_limit": credit_limit,
                        }
                    )

        total_products = Product.objects.count()
        total_dealers = Dealer.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Seeding completed successfully: {total_products} products, {total_dealers} dealers."))
