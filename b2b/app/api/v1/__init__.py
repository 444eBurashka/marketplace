from fastapi import APIRouter
import app.api.v1.auth as auth
import app.api.v1.sellers as sellers
import app.api.v1.products as products
import app.api.v1.skus as skus



api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(sellers.router, prefix="/sellers", tags=["Sellers"])
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(skus.router, prefix="", tags=["SKUs"])
