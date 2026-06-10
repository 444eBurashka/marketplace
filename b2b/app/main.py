from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.session import engine
from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # startup
    yield
    # shutdown
    await engine.dispose()


def create_application() -> FastAPI:
    app = FastAPI(
        title="NeoMarket B2B API",
        description="Seller Cabinet",
        version="1.0.0",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["https://neomarket.local"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(loc) for loc in first.get("loc", []) if loc != "body")
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_ERROR",
                "message": first.get("msg", "Validation error"),
                "details": {"field": field},
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        # Если detail уже dict с code/message — пропускаем как есть
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            content = exc.detail
        else:
            code = "UNAUTHORIZED" if exc.status_code == 401 else "ERROR"
            content = {"code": code, "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict:
        return {"status": "ok", "service": "b2b"}

    return app


app = create_application()