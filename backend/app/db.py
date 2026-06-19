import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url

from .config import settings
from .logging_utils import log_event


def _build_engine(url, *, autocommit: bool = False):
    connect_args = {
        # Prefer connecting to a writable primary in HA clusters
        "target_session_attrs": "read-write",
        # TCP keepalives to detect broken connections and failover faster
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    engine_kwargs = {
        "pool_size": 20,
        "max_overflow": 30,
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 60,
        "connect_args": connect_args,
    }
    if autocommit:
        engine_kwargs["isolation_level"] = "AUTOCOMMIT"
    return create_engine(url, **engine_kwargs)


def ensure_postgres_database(database_url: str) -> None:
    """Ensure the target Postgres database exists; create it if missing.

    This connects to the server's "postgres" database with the same credentials
    and issues a CREATE DATABASE if the target DB is not found.
    """
    try:
        url = make_url(database_url)
    except Exception:
        # Malformed URL; let normal engine creation raise later
        return

    backend = url.get_backend_name()
    if backend not in ("postgresql", "postgresql+psycopg2"):
        # Only supported for Postgres
        return

    target_database = url.database
    if not target_database:
        return

    admin_url = url.set(database="postgres")

    admin_engine = None
    # Try postgres then template1 to maximize compatibility
    for admin_db in ("postgres", "template1"):
        try:
            admin_engine = _build_engine(url.set(database=admin_db), autocommit=True)
            with admin_engine.connect() as connection:
                # If we landed on a replica, this will be 'on'; skip to next candidate
                ro = connection.execute(text("SHOW transaction_read_only")).scalar()
                if str(ro).lower() in ("on", "true", "1"):
                    raise RuntimeError("connected to read-only server")

                exists = connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                    {"dbname": target_database},
                ).scalar() is not None

                if not exists:
                    # Quote identifier safely by doubling internal quotes
                    safe_dbname = target_database.replace('"', '""')
                    connection.execute(text(f'CREATE DATABASE "{safe_dbname}"'))
            log_event("db.ensure.success", admin_db=admin_db, database=target_database)
            break
        except Exception as ex:
            logging.warning("DB bootstrap on %s failed: %s", admin_db, ex)
            log_event("db.ensure.error", admin_db=admin_db, error=str(ex))
            continue
        finally:
            if admin_engine is not None:
                try:
                    admin_engine.dispose()
                except Exception:
                    pass


# Ensure DB exists before creating the main engine
ensure_postgres_database(settings.database_url)

engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
