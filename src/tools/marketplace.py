"""
Marketplace backend simulation (e-commerce catalog, pricing, and seller communication).
"""
import copy
from typing import Any, Dict, List, Optional
from src.models.types import MarketplaceItem


class MarketplaceCatalog:
    """
    Simulated in-memory marketplace datastore representing an e-commerce platform like Mercari.
    """
    def __init__(self):
        self._items: Dict[str, MarketplaceItem] = {
            "item-001": MarketplaceItem(
                item_id="item-001",
                title="Sony FX3 Cinema Camera Body",
                category="Cameras & Optics",
                price_jpy=480000,
                seller_id="seller-tokyo-camera",
                status="ACTIVE",
            ),
            "item-002": MarketplaceItem(
                item_id="item-002",
                title="Porter Yoshida Tanker 2-Way Briefcase (Sage Green)",
                category="Bags & Accessories",
                price_jpy=32000,
                seller_id="seller-ginza-vintage",
                status="ACTIVE",
            ),
            "item-003": MarketplaceItem(
                item_id="item-003",
                title="Apple MacBook Pro 16 M3 Max 64GB 1TB",
                category="Computers",
                price_jpy=410000,
                seller_id="seller-akiba-tech",
                status="ACTIVE",
            ),
            "item-004": MarketplaceItem(
                item_id="item-004",
                title="Nike Dunk Low Retro Panda (Size 28cm)",
                category="Footwear",
                price_jpy=16500,
                seller_id="seller-harajuku-kicks",
                status="ACTIVE",
            ),
            "item-005": MarketplaceItem(
                item_id="item-005",
                title="Vintage Seiko Diver 6309-7040 Turtle 1982",
                category="Watches",
                price_jpy=85000,
                seller_id="seller-kyoto-horology",
                status="ACTIVE",
            ),
        }
        self._notifications: List[Dict[str, Any]] = []

    def search_listings(self, query: str = "", category: Optional[str] = None, max_price: Optional[int] = None) -> List[Dict[str, Any]]:
        results = []
        for item in self._items.values():
            if item.status != "ACTIVE":
                continue
            if query and query.lower() not in item.title.lower():
                continue
            if category and category.lower() not in item.category.lower():
                continue
            if max_price and item.price_jpy > max_price:
                continue
            results.append(item.model_dump())
        return results

    def get_listing(self, item_id: str) -> Optional[Dict[str, Any]]:
        item = self._items.get(item_id)
        return item.model_dump() if item else None

    def update_price(self, item_id: str, new_price_jpy: int) -> Dict[str, Any]:
        if item_id not in self._items:
            raise ValueError(f"Item {item_id} not found")
        item = self._items[item_id]
        old_price = item.price_jpy
        item.price_jpy = new_price_jpy
        return {
            "item_id": item_id,
            "old_price_jpy": old_price,
            "new_price_jpy": new_price_jpy,
            "pct_change": round(((new_price_jpy - old_price) / old_price) * 100, 2),
            "status": "UPDATED",
        }

    def flag_item(self, item_id: str, violation_type: str, justification: str) -> Dict[str, Any]:
        if item_id not in self._items:
            raise ValueError(f"Item {item_id} not found")
        item = self._items[item_id]
        item.status = "SUSPENDED"
        item.flags.append(f"{violation_type}: {justification}")
        return {
            "item_id": item_id,
            "status": "SUSPENDED",
            "violation": violation_type,
            "justification": justification,
        }

    def notify_seller(self, seller_id: str, message: str) -> Dict[str, Any]:
        notification = {
            "seller_id": seller_id,
            "message": message,
            "delivered": True,
        }
        self._notifications.append(notification)
        return notification
