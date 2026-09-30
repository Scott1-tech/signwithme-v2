from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BBoxModel(BaseModel):
    """Canonical bounding box: PDF points, origin top-left."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class TaskAccepted(BaseModel):
    task_id: str | None = None
    status: str


class Message(BaseModel):
    message: str
