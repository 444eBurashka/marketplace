import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Category, Image, ImageEntityType, Product, ProductCharacteristic, ProductStatus, SKU
from app.schemas.products import ProductCreateRequest


def _slugify(text: str) -> str:
    """Генерирует slug из произвольного текста."""
    text = text.lower().strip()
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


def _load_product_query(product_id: uuid.UUID):
    """Общий select с нужными selectinload для сериализации."""
    return (
        select(Product)
        .where(Product.id == product_id, Product.deleted == False)  # noqa: E712
        .options(
            selectinload(Product.images),
            selectinload(Product.characteristics),
            selectinload(Product.skus).selectinload(SKU.images),
            selectinload(Product.skus).selectinload(SKU.attributes),
        )
    )


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
    await db.flush()

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

    result = await db.execute(_load_product_query(product.id))
    return result.scalar_one()


async def get_product(
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> Product:
    result = await db.execute(_load_product_query(product_id))
    product = result.scalar_one_or_none()

    # Чужой товар и несуществующий — оба 404 (не раскрываем факт существования)
    if product is None or product.seller_id != seller_id:
        raise LookupError("Product not found")

    return product

async def list_products(
    seller_id: uuid.UUID,
    db: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[dict], int]:
    """Возвращает список товаров продавца с агрегатами."""
    from sqlalchemy import func, case
    from app.models import SKU, Image, ImageEntityType

    # Базовый фильтр — только свои товары (IDOR-защита на уровне queryset)
    conditions = [Product.seller_id == seller_id]

    if not include_deleted:
        conditions.append(Product.deleted == False)  # noqa: E712

    if status is not None:
        try:
            conditions.append(Product.status == ProductStatus(status))
        except ValueError:
            raise ValueError(f"Invalid status: {status}")

    if search is not None and search.strip():
        conditions.append(Product.title.ilike(f"%{search.strip()}%"))

    # Подзапрос: min цена SKU
    min_price_sq = (
        select(func.min(SKU.price))
        .where(SKU.product_id == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )

    # Подзапрос: обложка (первое изображение с ordering=0 или наименьшим)
    cover_sq = (
        select(Image.url)
        .where(
            Image.entity_type == ImageEntityType.PRODUCT,
            Image.entity_id == Product.id,
        )
        .order_by(Image.ordering)
        .limit(1)
        .correlate(Product)
        .scalar_subquery()
    )

    # Считаем total
    count_q = select(func.count()).select_from(Product).where(*conditions)
    total = await db.scalar(count_q) or 0

    # Основной запрос
    q = (
        select(
            Product,
            min_price_sq.label("min_price"),
            cover_sq.label("cover_image"),
        )
        .where(*conditions)
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(q)).all()

    items = []
    for product, min_price, cover_image in rows:
        items.append({
            "id": product.id,
            "title": product.title,
            "slug": product.slug,
            "status": product.status,
            "category_id": product.category_id,
            "deleted": product.deleted,
            "created_at": product.created_at,
            "min_price": min_price,
            "cover_image": cover_image,
        })

    return items, total