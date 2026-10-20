"""Pydantic request/response models for the REST API."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

class Decision(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    action: Literal["accept", "reject", "override"]
    sql_type: Optional[str] = None
    new_name: Optional[str] = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    ref_table: Optional[str] = None
    ref_column: str = "id"
    by: str = "api-user"

class ApproveRequest(BaseModel):
    decisions: list[Decision] = Field(min_length=1)

class FreezeRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=100)

class InferRequest(BaseModel):
    force: bool = False

class ExplainRequest(BaseModel):
    table: str
    column: str
