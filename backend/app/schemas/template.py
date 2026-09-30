from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.common import BBoxModel

FieldType = Literal["text_line", "checkbox", "checkbox_group", "signature", "date", "table"]


class CheckboxOption(BaseModel):
    id: str
    label: str
    bbox: BBoxModel
    region_bbox: BBoxModel | None = None


class FieldOut(BaseModel):
    field_id: str
    label: str
    field_type: FieldType
    page: int
    bbox: BBoxModel
    required: bool = True
    options: list[CheckboxOption] | None = None
    validation_rule: str | None = None
    paired_date_field_id: str | None = None
    detector: str | None = None


class PageSchemaOut(BaseModel):
    page_number: int
    width: float
    height: float
    rotation: int = 0
    fields: list[FieldOut] = []


class TemplateSchemaOut(BaseModel):
    pages: list[PageSchemaOut] = []
    field_count: int = 0


class SlotFillCount(BaseModel):
    filled: int
    total: int


class PrefilledSummary(BaseModel):
    """How many signature/date slots the template FILE itself already fills.
    `warning` when more than half of either kind is filled: a signed package
    was most likely uploaded where a blank form was expected."""

    signatures: SlotFillCount
    dates: SlotFillCount
    warning: bool


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    page_count: int
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # Computed at read time from the stored template_baseline; None before the
    # template has one.
    prefilled_summary: PrefilledSummary | None = None


class TemplateDetailOut(TemplateOut):
    field_schema: dict


class TemplateCreateResponse(BaseModel):
    template_id: UUID
    task_id: str | None
    status: str


class FieldUpdate(BaseModel):
    """Manual correction of an auto-detected field."""

    label: str | None = None
    field_type: FieldType | None = None
    bbox: BBoxModel | None = None
    required: bool | None = None
    validation_rule: str | None = None
    paired_date_field_id: str | None = None
    options: list[CheckboxOption] | None = None
