"""Target sinks: PostgreSQL (SQLAlchemy 2.x) and an in-memory stand-in with the same contract."""
from __future__ import annotations
from contextlib import contextmanager
from ..generation.ddl import quote_ident, dependency_order
from ..etl.transform import insert_columns, conflict_columns

class SinkError(Exception):
    pass

def split_statements(sql: str) -> list:
    stmts, cur = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        cur.append(line)
        if line.rstrip().endswith(";"):
            stmts.append("\n".join(cur).strip()); cur = []
    if "".join(cur).strip():
        stmts.append("\n".join(cur).strip())
    return stmts

class PostgresSink:
    def __init__(self, url: str, schema: str, timeout_s: int = 10):
        try:
            from sqlalchemy import create_engine
        except ImportError as e:
            raise SinkError("SQLAlchemy is not installed: pip install -r requirements.txt") from e
        self.schema = schema
        self._qs = quote_ident(schema)
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": timeout_s})
        self._conn = None

    def ping(self):
        from sqlalchemy import text
        try:
            with self.engine.connect() as c:
                c.execute(text("SELECT 1"))
        except Exception as e:
            raise SinkError(f"cannot connect to PostgreSQL ({type(e).__name__}). Check that the service is "
                            f"running and POSTGRES_URL (user, password, database name) is correct.") from e
        return True

    def apply_schema(self, sm: dict, ddl_sql: str):
        from sqlalchemy import text
        with self.engine.begin() as c:           # DDL is transactional in PostgreSQL
            for stmt in split_statements(ddl_sql):
                c.execute(text(stmt))

    def execute_script(self, sql: str):
        from sqlalchemy import text
        with self.engine.begin() as c:
            for stmt in split_statements(sql):
                c.execute(text(stmt))

    @contextmanager
    def transaction(self):
        with self.engine.begin() as conn:
            self._conn = conn
            try:
                yield self
            finally:
                self._conn = None

    def _q(self, table): return f"{self._qs}.{quote_ident(table)}"

    def count(self, table: str) -> int:
        from sqlalchemy import text
        sql = text(f"SELECT count(*) FROM {self._q(table)}")
        if self._conn is not None:
            return int(self._conn.execute(sql).scalar_one())
        with self.engine.connect() as c:
            return int(c.execute(sql).scalar_one())

    def insert_rows(self, table: str, spec: dict, rows: list) -> int:
        from sqlalchemy import text
        if not rows:
            return 0
        if self._conn is None:
            raise SinkError("insert_rows must run inside sink.transaction()")
        cols = insert_columns(spec)
        conf = conflict_columns(spec)
        sql = (f"INSERT INTO {self._q(table)} ({', '.join(quote_ident(c) for c in cols)}) "
               f"VALUES ({', '.join(':' + c for c in cols)}) "
               f"ON CONFLICT ({', '.join(quote_ident(c) for c in conf)}) DO NOTHING")
        before = self.count(table)
        self._conn.execute(text(sql), rows)
        return self.count(table) - before

    def fetch_rows(self, table: str, spec: dict) -> list:
        from sqlalchemy import text
        cols = insert_columns(spec)
        sql = text(f"SELECT {', '.join(quote_ident(c) for c in cols)} FROM {self._q(table)}")
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(sql)]

    def existing_tables(self) -> set:
        from sqlalchemy import text
        with self.engine.connect() as c:
            r = c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                          {"s": self.schema})
            return {x[0] for x in r}

class InMemorySink:
    """Enforces the same rules as the generated DDL (PK/UNIQUE conflict policy, FK existence, NOT NULL)."""
    def __init__(self):
        self.specs, self.data, self._snapshot, self.in_tx = {}, {}, None, False
        self.schema_sql = ""
    def ping(self): return True
    def apply_schema(self, sm, ddl_sql):
        self.schema_sql = ddl_sql
        for n, t in sm["tables"].items():
            self.specs[n] = t
            self.data.setdefault(n, {})
    def execute_script(self, sql):
        if "DROP TABLE" in sql:
            for n in list(self.data):
                self.data.pop(n); self.specs.pop(n, None)
    @contextmanager
    def transaction(self):
        self._snapshot = {t: dict(r) for t, r in self.data.items()}
        self.in_tx = True
        try:
            yield self
        except BaseException:
            self.data = self._snapshot
            raise
        finally:
            self.in_tx, self._snapshot = False, None
    def count(self, table): return len(self.data.get(table, {}))
    def _key(self, spec, row): return tuple(row.get(c) for c in conflict_columns(spec))
    def insert_rows(self, table, spec, rows):
        if not self.in_tx:
            raise SinkError("insert_rows must run inside sink.transaction()")
        inserted = 0
        for row in rows:
            for c, v in spec["columns"].items():
                if v.get("transform") == "generated":
                    continue
                if row.get(c) is None and not v["nullable"]:
                    raise SinkError(f"NOT NULL violation {table}.{c}")
            for fk in spec["foreign_keys"]:
                val = row.get(fk["column"])
                if val is not None and not any(r.get(fk["ref_column"]) == val
                                               for r in self.data.get(fk["ref_table"], {}).values()):
                    raise SinkError(f"FOREIGN KEY violation {table}.{fk['column']}={val!r}")
            k = self._key(spec, row)
            if k in self.data[table]:
                continue
            self.data[table][k] = dict(row); inserted += 1
        return inserted
    def fetch_rows(self, table, spec):
        cols = insert_columns(spec)
        return [{c: r.get(c) for c in cols} for r in self.data.get(table, {}).values()]
    def existing_tables(self): return set(self.data)
