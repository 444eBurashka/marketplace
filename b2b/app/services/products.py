import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Category, Image, ImageEntityType, Product, ProductCharacteristic, ProductStatus, SKU
from app.schemas.products import ProductCreateRequest


def _slugify(text: str) -> str:
    """Генерирует slug из произвольного текста."""
    text = text.lower().strip()
    # Транслитерация базовых русских символов
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    text = "".join(translit.get(c, c) for c in text)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text


async def _ensure_unique_slug(slug: str, db: AsyncSession) -> str:
    """Добавляет суффикс, если slug уже занят."""
    base = slug
    counter = 1
    while True:
        existing = await db.scalar(select(Product.id).where(Product.slug == slug))
        if existing is None:
            return slug
        slug = f"{base}-{counter}"
        counter += 1


async def create_product(
    body: ProductCreateRequest,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> Product:
    # 1. Проверяем категорию
    category = await db.scalar(
        select(Category).where(Category.id == body.category_id, Category.is_active == True)  # noqa: E712
    )
    if category is None:
        raise ValueError("Category not found")

    # 2. Определяем slug
    raw_slug = body.slug if body.slug else _slugify(body.title)
    if not raw_slug:
        raw_slug = str(uuid.uuid4())
    slug = await _ensure_unique_slug(raw_slug, db)

    # 3. Создаём товар
    product = Product(
        seller_id=seller_id,
        category_id=body.category_id,
        title=body.title,
        description=body.description,
        slug=slug,
        status=ProductStatus.CREATED,
        deleted=False,
        blocked=False,
    )
    db.add(product)
    await db.flush()  # получаем product.id

    # 4. Изображения
    for img in body.images:
        db.add(Image(
            entity_type=ImageEntityType.PRODUCT,
            entity_id=product.id,
            url=img.url,
            ordering=img.ordering,
        ))

    # 5. Характеристики
    for char in body.characteristics:
        db.add(ProductCharacteristic(
            product_id=product.id,
            name=char.name,
            value=char.value,
        ))

    await db.flush()

    # 6. Перезагружаем с relationship-ами для сериализации
    result = await db.execute(
        select(Product)
        .where(Product.id == product.id)
        .options(
            selectinload(Product.images),
            selectinload(Product.characteristics),
            selectinload(Product.skus),
        )
    )
    return result.scalar_one()

