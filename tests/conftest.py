import fakeredis
import pytest
import redis


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Every app created in a test talks to an in-memory Redis (one fresh server per test)."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, "from_url",
                        classmethod(lambda cls, url, **kw: fakeredis.FakeRedis(server=server, **kw)))
    return server
