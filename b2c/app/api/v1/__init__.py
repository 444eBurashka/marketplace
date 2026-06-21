from fastapi import APIRouter

import app.api.v1.auth as auth
import app.api.v1.catalog as catalog
import app.api.v1.favorites as favorites
import app.api.v1.subscriptions as subscriptions
import app.api.v1.cart as cart
import app.api.v1.home as home
import app.api.v1.orders as orders

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(catalog.router, prefix="", tags=["Catalog"])
api_router.include_router(favorites.router, prefix="", tags=["Favorites"])
api_router.include_router(subscriptions.router, prefix="", tags=["Subscriptions"])
api_router.include_router(cart.router, prefix="", tags=["Cart"])
api_router.include_router(home.router, prefix="", tags=["Home"])
api_router.include_router(orders.router, prefix="", tags=["Orders"])
api_router.include_router(orders.events_router, prefix="", tags=["Events"])
