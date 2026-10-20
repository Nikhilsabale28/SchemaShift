"""Infer a relational schema proposal from an inspection report.

Rules (all deterministic and documented in docs/inference.md):
  * every collection -> one table; `_id` -> primary key `id` VARCHAR(24)
  * nested object (scalar leaves) -> flattened columns `parent_child`
  * array of objects -> child table `<parent>_<field>` with FK to parent
  * array of scalars  -> child table `<parent>_<field>` with a `value` column
  * field named `<x>_id`/`<x>Id` whose value type is objectid and whose
    plural collection exists -> foreign key (otherwise UNRESOLVED -> review)
  * mixed types -> TEXT, flagged for review (never silently coerced)
"""
from __future__ import annotations
from ..core.types import dominant_sql_type
from ..matching.normalize import normalize_name

def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word

def _find_ref_collection(field_name: str, collections: set):
    base = normalize_name(field_name)
    if base.endswith("_id"):
        base = base[:-3]
    for c in sorted(collections):
        if _singular(normalize_name(c)) == base or normalize_name(c) == base:
            return c
    return None

def _col(path: str) -> str:
    return normalize_name(path.replace("[]", "").replace(".", "_"))

def infer_schema(report: dict) -> dict:
    colls = set(report["collections"])
    tables: dict = {}
    for cname in sorted(colls):
        fields = report["collections"][cname]["fields"]
        t_main = normalize_name(cname)
        tables[t_main] = {"source_collection": cname, "kind": "root", "parent": None,
                          "columns": {}, "primary_key": ["id"], "foreign_keys": [],
                          "unique": [], "array_path": None}
        _fill(tables, t_main, cname, fields, "", colls, prefix_len=0)
    return {"tables": tables}

def _fill(tables, table, cname, fields, array_path, colls, prefix_len):
    tbl = tables[table]
    for path, info in fields.items():
        in_array = "[]" in path
        if array_path == "":
            if in_array:
                continue  # handled by child tables
            rel = path
        else:
            if not path.startswith(array_path + "[]"):
                continue
            rel = path[len(array_path) + 2:].lstrip(".")
            if rel == "":
                continue
            if "[]" in rel:
                continue
        if info["is_array"]:
            _make_child(tables, table, cname, path, fields, colls)
            continue
        if info["is_object"]:
            continue  # flattened via its leaves
        types = info["types"]
        if rel == "_id":
            tbl["columns"]["id"] = _column(path, types, info, False, True)
            tbl["columns"]["id"]["sql_type"] = "VARCHAR(24)"
            continue
        col = _col(rel)
        entry = _column(path, types, info, False, False)
        if "objectid" in types and col != "id":
            ref = _find_ref_collection(rel.split(".")[-1], colls)
            entry["sql_type"] = "VARCHAR(24)"
            if ref:
                entry["references"] = f"{normalize_name(ref)}.id"
                tbl["foreign_keys"].append({"column": col, "ref_table": normalize_name(ref),
                                            "ref_column": "id", "resolved": True})
            else:
                entry["unresolved_fk"] = True
                tbl["foreign_keys"].append({"column": col, "ref_table": None,
                                            "ref_column": None, "resolved": False})
        tbl["columns"][col] = entry

def _column(path, types, info, is_array_child, is_pk):
    sql, mixed = dominant_sql_type(types)
    return {"source_path": path, "source_types": types, "sql_type": sql,
            "nullable": (not is_pk) and (info["has_null"] or info["sometimes_absent"]),
            "mixed_types": mixed, "unresolved_fk": False,
            "transform": "flatten" if "." in path else "direct"}

def _make_child(tables, parent, cname, arr_path, fields, colls):
    name = normalize_name(parent + "_" + _col(arr_path))
    sub = {p: i for p, i in fields.items() if p.startswith(arr_path + "[]")}
    leaf_object = any(p.startswith(arr_path + "[].") for p in sub)
    tables[name] = {"source_collection": cname, "kind": "array_child", "parent": parent,
                    "array_path": arr_path, "primary_key": ["id"],
                    "columns": {"id": {"source_path": None, "source_types": {}, "sql_type": "BIGSERIAL",
                                       "nullable": False, "mixed_types": False, "unresolved_fk": False,
                                       "transform": "generated"},
                                f"{_singular(parent)}_id": {"source_path": "_id", "source_types": {"objectid": 1},
                                                           "sql_type": "VARCHAR(24)", "nullable": False,
                                                           "mixed_types": False, "unresolved_fk": False,
                                                           "transform": "parent_key"},
                                "position": {"source_path": None, "source_types": {}, "sql_type": "BIGINT",
                                             "nullable": False, "mixed_types": False, "unresolved_fk": False,
                                             "transform": "array_index"}},
                    "unique": [[f"{_singular(parent)}_id", "position"]],
                    "foreign_keys": [{"column": f"{_singular(parent)}_id", "ref_table": parent,
                                      "ref_column": "id", "resolved": True}]}
    if leaf_object:
        _fill(tables, name, cname, fields, arr_path, colls, 0)
    else:
        info = fields[arr_path + "[]"]
        tables[name]["columns"]["value"] = _column(arr_path + "[]", info["types"], info, True, False)
