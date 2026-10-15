from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base, Bay
from .protocol import BAY_COUNT


def make_engine(url: str) -> Engine:
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", lambda conn, _rec: conn.execute("PRAGMA foreign_keys=ON"))
    return engine


def init_db(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        existing = set(session.scalars(select(Bay.id)))
        session.add_all(Bay(id=i) for i in range(1, BAY_COUNT + 1) if i not in existing)
    return sessions
