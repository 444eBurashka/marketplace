from typing import Annotated

from fastapi import Header

from shared.errors.http import ForbiddenError


def verify_service_key(expected_key: str):
    """Фабрика dependency, принимающая ожидаемый ключ."""
    async def _verify(x_service_key: Annotated[str | None, Header()] = None) -> None:
        if x_service_key != expected_key:
            raise ForbiddenError(detail="Invalid service key")
    return _verify
