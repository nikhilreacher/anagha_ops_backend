
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


raw_database_url = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.ncqspuxvxkqvuhbdazba:AnaghaOPS2210@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres"
)
DATABASE_SCHEMA = os.getenv("DATABASE_SCHEMA", "ops-schema")


def normalize_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme.startswith("postgres"):
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("sslmode", "require")
        return urlunparse(parsed._replace(query=urlencode(query)))
    return database_url


DATABASE_URL = normalize_database_url(raw_database_url)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={
        "options": f'-csearch_path="{DATABASE_SCHEMA}",public',
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
