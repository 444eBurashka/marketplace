"""HTTP-клиент для запросов в B2B. Все запросы идут с X-Service-Key."""
import httpx
from fastapi import HTTPException

from app.core.config import settings

_B2B_URL = settings.b2b_internal_url
_HEADERS = {"X-Service-Key": settings.service_key}


async def get_products(params: dict) -> dict:
    """GET /api/v1/products — каталог с фильтрами."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{_B2B_URL}/api/v1/public/products",
                params=params,
                headers=_HEADERS,
                timeout=10.0,
            )
            if r.status_code == 502 or r.status_code == 503:
                raise HTTPException(status_code=502, detail="B2B service unavailable")
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="B2B service unavailable")


async def get_product(product_id: str) -> dict | None:
    """GET /api/v1/products/{id}."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{_B2B_URL}/api/v1/public/products/{product_id}",
                headers=_HEADERS,
                timeout=10.0,
            )
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                raise HTTPException(status_code=502, detail="B2B service unavailable")
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="B2B service unavailable")


async def get_catalog_facets(params: dict) -> dict:
    """GET /api/v1/catalog/facets."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{_B2B_URL}/api/v1/catalog/facets",
                params=params,
                headers=_HEADERS,
                timeout=10.0,
            )
            if r.status_code >= 500:
                raise HTTPException(status_code=502, detail="B2B service unavailable")
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="B2B service unavailable")


async def get_categories() -> list:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{_B2B_URL}/api/v1/categories",
                headers=_HEADERS,
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="B2B service unavailable")


async def reserve(order_id: str, idempotency_key: str, items: list[dict]) -> dict:
    """POST /api/v1/reservations."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{_B2B_URL}/api/v1/inventory/reserve",
                json={"order_id": order_id, "idempotency_key": idempotency_key, "items": items},
                headers=_HEADERS,
                timeout=15.0,
            )
            return {"status_code": r.status_code, "body": r.json() if r.content else {}}
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="B2B service unavailable")


async def unreserve(order_id: str, items: list[dict]) -> int:
    """POST /api/v1/inventory/unreserve"""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{_B2B_URL}/api/v1/inventory/unreserve",
                json={"order_id": order_id, "items": items},
                headers=_HEADERS,
                timeout=10.0,
            )
            return r.status_code
        except httpx.ConnectError:
            return 503


async def fulfill(order_id: str, items: list[dict]) -> int:
    """POST /api/v1/inventory/fulfill"""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{_B2B_URL}/api/v1/inventory/fulfill",
                json={"order_id": order_id, "items": items},   # ← обязательное тело
                headers=_HEADERS,
                timeout=10.0,
            )
            return r.status_code
        except httpx.ConnectError:
            return 503


async def get_similar_products(product_id: str) -> list:
    """GET /api/v1/public/products/{product_id}/similar"""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{_B2B_URL}/api/v1/public/products/{product_id}/similar",
                headers=_HEADERS,
                timeout=10.0,
            )
            if r.status_code == 404:
                return []
            if r.status_code >= 500:
                raise HTTPException(status_code=502, detail="B2B service unavailable")
            r.raise_for_status()
            return r.json()   # plain array
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="B2B service unavailable")
