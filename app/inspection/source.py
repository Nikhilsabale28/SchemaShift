"""Document sources: real MongoDB and an in-memory stand-in (for tests/demo without a database)."""
from __future__ import annotations

class SourceError(Exception):
    pass

class InMemorySource:
    def __init__(self, collections: dict, database: str = "memory"):
        self._c = {k: list(v) for k, v in collections.items()}
        self.database = database
    def ping(self): return True
    def collection_names(self): return sorted(self._c)
    def documents(self, name):
        if name not in self._c: raise SourceError(f"collection {name!r} not found")
        return list(self._c[name])
    def iter_documents(self, name): return iter(self.documents(name))

class MongoSource:
    def __init__(self, uri: str, database: str, collections: tuple, timeout_ms: int = 5000):
        try:
            from pymongo import MongoClient
        except ImportError as e:
            raise SourceError("pymongo is not installed: pip install -r requirements.txt") from e
        self.database = database
        self._names = list(collections)
        self._client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        self._db = self._client[database]
    def ping(self):
        try:
            self._client.admin.command("ping")
        except Exception as e:
            raise SourceError(f"cannot reach MongoDB: {type(e).__name__}. Is the MongoDB service running "
                              f"and MONGO_URI correct?") from e
        return True
    def collection_names(self):
        existing = set(self._db.list_collection_names())
        missing = [c for c in self._names if c not in existing]
        if missing:
            raise SourceError(f"collections not found in database {self.database!r}: {missing}. Run the seed step.")
        return sorted(self._names)
    def documents(self, name): return list(self.iter_documents(name))
    def iter_documents(self, name): return self._db[name].find().sort("_id", 1)
