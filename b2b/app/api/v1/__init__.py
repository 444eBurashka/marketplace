from fastapi import APIRouter
import app.api.v1.auth as auth
import app.api.v1.sellers as sellers



api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(sellers.router, prefix="/sellers", tags=["Sellers"])
