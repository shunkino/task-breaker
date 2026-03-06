from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def get_engine():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return create_engine(
        settings.db_url,
        connect_args={"check_same_thread": False},
    )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _migrate_db(engine):
    """Add new columns to existing tables if they are missing (SQLite-compatible)."""
    with engine.connect() as conn:
        result = conn.execute(__import__("sqlalchemy").text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in result}
        migrations = [
            ("due_date", "ALTER TABLE tasks ADD COLUMN due_date DATETIME"),
            ("daily_focus", "ALTER TABLE tasks ADD COLUMN daily_focus BOOLEAN DEFAULT 0 NOT NULL"),
            ("focus_order", "ALTER TABLE tasks ADD COLUMN focus_order INTEGER"),
            ("ai_context_pending", "ALTER TABLE tasks ADD COLUMN ai_context_pending BOOLEAN DEFAULT 0 NOT NULL"),
        ]
        for col_name, ddl in migrations:
            if col_name not in existing_columns:
                conn.execute(__import__("sqlalchemy").text(ddl))
        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_db(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
