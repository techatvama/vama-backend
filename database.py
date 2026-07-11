from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# NullPool: return connections to Neon's PgBouncer immediately after each request.
# The DATABASE_URL already points to Neon's pooler endpoint (-pooler. hostname),
# so PgBouncer handles connection reuse. SQLAlchemy's internal pool is redundant
# and prevents Neon from auto-suspending between traffic bursts.
engine = create_engine(DATABASE_URL, poolclass=NullPool)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
