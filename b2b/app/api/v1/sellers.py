from fastapi import APIRouter
from app.core.dependencies import CurrentSeller

router = APIRouter()


@router.get("/me")
async def get_my_profile(seller: CurrentSeller) -> dict:
    return {
        "id": str(seller.id),
        "email": seller.email,
        "company_name": seller.company_name,
    }
