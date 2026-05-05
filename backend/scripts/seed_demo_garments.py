"""
Seed demo garment images into the database.

Usage (from backend/):
    python -m scripts.seed_demo_garments --source /path/to/demo_garments --storage /data/wardrobe

The script copies images into the storage directory and inserts ClothingItem rows
with is_demo=True so the 小镜 agent can recommend them.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.item import ClothingItem, ItemStatus


async def seed(source_dir: Path, storage_path: Path, database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    image_files = sorted(
        f for f in source_dir.iterdir()
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        and not f.name.startswith("_")
    )

    if not image_files:
        print(f"No images found in {source_dir}")
        return

    dest_dir = storage_path / "demo_garments"
    dest_dir.mkdir(parents=True, exist_ok=True)

    async with SessionFactory() as db:
        # Check existing demo items
        existing = await db.execute(select(ClothingItem).where(ClothingItem.is_demo == True))  # noqa: E712
        existing_paths = {item.image_path for item in existing.scalars().all()}

        inserted = 0
        for img_path in image_files:
            dest_file = dest_dir / img_path.name
            rel_path = str(dest_file.relative_to(storage_path))

            if rel_path in existing_paths:
                print(f"  skip (already seeded): {img_path.name}")
                continue

            shutil.copy2(str(img_path), str(dest_file))

            # Infer category from filename heuristic
            name_lower = img_path.stem.lower()
            if any(k in name_lower for k in ("pant", "skirt", "trouser", "jean", "short", "lower")):
                item_type = "lower"
            elif any(k in name_lower for k in ("dress",)):
                item_type = "dress"
            else:
                item_type = "lower"  # default for demo_garments (mostly lower-body)

            item = ClothingItem(
                id=uuid.uuid4(),
                # Use a synthetic user_id for demo items (not associated with any real user)
                user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                image_path=rel_path,
                type=item_type,
                name=img_path.stem.replace("_", " ").title(),
                is_demo=True,
                source="demo",
                style=[],
                colors=[],
                season=[],
                tags={},
                status=ItemStatus.ready,
                ai_processed=False,
            )
            db.add(item)
            inserted += 1
            print(f"  seeded: {img_path.name} → {rel_path} (type={item_type})")

        await db.commit()
        print(f"\nDone. {inserted} items seeded.")

    await engine.dispose()


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Seed demo garments into the database")
    parser.add_argument("--source", default="../demo_garments", help="Directory of demo images")
    parser.add_argument("--storage", default=settings.storage_path, help="Storage root path")
    parser.add_argument("--db", default=str(settings.database_url), help="Database URL")
    args = parser.parse_args()

    asyncio.run(seed(Path(args.source).resolve(), Path(args.storage).resolve(), args.db))


if __name__ == "__main__":
    main()
