from fastapi import APIRouter
import app.api.v1.auth as auth
import app.api.v1.sellers as sellers
import app.api.v1.products as products
import app.api.v1.skus as skus
import app.api.v1.patch_endpoints as patch_endpoints
import app.api.v1.invoices as invoices
import app.api.v1.moderation_events as moderation_events
import app.api.v1.inventory as inventory


api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(sellers.router, prefix="/sellers", tags=["Sellers"])
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(skus.router, prefix="", tags=["SKUs"])
api_router.include_router(patch_endpoints.router, prefix="", tags=["Edit"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
api_router.include_router(moderation_events.router, prefix="/moderation/events", tags=["Moderation Events"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])