from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

if settings.database_url.startswith("postgres"):
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            connection.execute("select 1")
    except Exception:
        engine = create_engine("sqlite:///./jobmatcher.db")
else:
    engine = create_engine(settings.database_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
