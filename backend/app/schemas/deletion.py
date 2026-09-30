"""Response shape for permanent (admin) deletions."""
from __future__ import annotations

from pydantic import BaseModel


class DeletionOut(BaseModel):
    id: str
    deleted: bool = True
