from fastapi import APIRouter
import app.api.v1.auth as auth
import app.api.v1.sellers as sellers



api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(sellers.router, prefix="/sellers", tags=["Sellers"])
api_router.include_router(categories.router, prefix="/categories", tags=["Categories"])
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(skus.router, prefix="", tags=["SKUs"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
api_router.include_router(images.router, prefix="/images", tags=["Images"])
api_router.include_router(catalog.router, prefix="/catalog", tags=["Public Catalog"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])
api_router.include_router(moderation_events.router, prefix="/moderation", tags=["Moderation Events"])
