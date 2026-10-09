from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


ID = Annotated[str, Field(min_length=1, max_length=500)]
Title = Annotated[str, Field(min_length=1, max_length=200)]


class Segment(StrictModel):
    source_id: ID
    kind: Literal["text", "image", "file", "transcript", "forward", "unavailable"]
    text: str | None = None
    asset_id: str | None = None
    unavailable_reason: str | None = None
    author: str | None = None
    published_at: datetime | None = None


class InputEnvelope(StrictModel):
    contract_version: Literal[1] = 1
    event_id: ID
    framework_instance_id: ID
    session_key: ID
    actor_key: ID
    message_key: str | None
    received_at: datetime
    source_kind: Literal["direct_request", "manual_notice", "user_forward"]
    segments: list[Segment]
    reply_origin_ref: ID


class Evidence(StrictModel):
    source_id: ID
    location: ID
    quote: Annotated[str, Field(min_length=1, max_length=12000)]


class Target(StrictModel):
    keyword: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    selection_ref: str | None = None
    recent_ref: str | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if sum(x is not None for x in (self.keyword, self.selection_ref, self.recent_ref)) != 1:
            raise ValueError("target requires exactly one locator")
        return self


class Patch(StrictModel):
    title: Title | None = None
    notes: Annotated[str, Field(max_length=10000)] | None = None
    date_text: str | None = None
    time_text: str | None = None
    all_day: bool | None = None
    priority: Literal[0, 1, 3, 5] | None = None
    reminder: str | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("empty patch")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be cleared")
        return self


class CreateItem(StrictModel):
    action: Literal["create"]
    title: Title
    relevance: Literal["applies", "not_applies", "unknown"]
    obligation: Literal["required", "optional", "informational"]
    requirements: list[str]
    project_name: str | None
    attachment_asset_ids: list[str]
    date_text: str | None
    time_text: str | None
    time_kind: Literal["deadline", "event", "none"]
    date_kind: Literal["timed", "date_only", "none", "unresolved"]
    source_evidence: Annotated[list[Evidence], Field(min_length=1)]
    ambiguities: list[str]


class UpdateItem(StrictModel):
    action: Literal["update"]
    target: Target
    patch: Patch
    relevance: Literal["applies", "not_applies", "unknown"]
    obligation: Literal["required", "optional", "informational"]
    source_evidence: Annotated[list[Evidence], Field(min_length=1)]
    ambiguities: list[str]


TaskItem = Annotated[CreateItem | UpdateItem, Field(discriminator="action")]


class PlanBase(StrictModel):
    schema_version: Literal[4]
    ambiguities: Annotated[list[str], Field(max_length=3)]


class NoticePlan(PlanBase):
    intent: Literal["ingest_notice"]
    material_group_id: ID
    notice_summary: str
    source_published_at: str | None
    time_basis: str | None
    tasks: Annotated[list[TaskItem], Field(max_length=10)]
    optional_items: list[str]
    information_only: list[str]


class CreatePlan(PlanBase):
    intent: Literal["create"]
    tasks: Annotated[list[CreateItem], Field(min_length=1, max_length=10)]


class Query(StrictModel):
    scope: Literal["today", "week", "seven_days", "all", "overdue", "undated"] = "all"
    project_name: str | None = None
    keyword: str | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=200)


class QueryPlan(PlanBase):
    intent: Literal["query"]
    query: Query


class UpdatePlan(PlanBase):
    intent: Literal["update"]
    target: Target
    patch: Patch


class CompletePlan(PlanBase):
    intent: Literal["complete"]
    target: Target


class ClarifyPlan(PlanBase):
    intent: Literal["clarify"]
    question_ref: ID
    answer: str


class NoActionPlan(PlanBase):
    intent: Literal["unsupported", "none"]
    reason: str
    safe_summary: str


ModelPlan = Annotated[
    NoticePlan | CreatePlan | QueryPlan | UpdatePlan | CompletePlan | ClarifyPlan | NoActionPlan,
    Field(discriminator="intent"),
]
PLAN_ADAPTER = TypeAdapter(ModelPlan)


class Identity(StrictModel):
    school: str | None = "东北大学"
    college: str | None = None
    major: str | None = None
    entry_year: int | None = None
    current_year: int | None = None
    class_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    confirmed_at: str | None = None


class MaterialBudget(StrictModel):
    file_bytes: int = Field(default=20 * 1024**2, ge=1, le=100 * 1024**2)
    group_files: int = Field(default=20, ge=1, le=100)
    group_bytes: int = Field(default=100 * 1024**2, ge=1, le=1024**3)
    message_characters: int = Field(default=50000, ge=1, le=50000)
    message_nodes: int = Field(default=100, ge=1, le=100)
    document_characters: int = Field(default=50000, ge=1, le=200000)
    pdf_pages: int = Field(default=30, ge=1, le=100)
    screenshots: int = Field(default=5, ge=1, le=40)
    pdf_visuals: int = Field(default=30, ge=1, le=100)
    docx_visuals: int = Field(default=20, ge=1, le=100)
    total_visuals: int = Field(default=40, ge=1, le=100)
    image_pixels: int = Field(default=40000000, ge=1, le=40000000)
    block_characters: int = Field(default=12000, ge=1000, le=12000)
    block_overlap: int = Field(default=500, ge=0, le=500)
    blocks: int = Field(default=8, ge=1, le=16)


class TimeBudget(StrictModel):
    text_seconds: int = Field(default=60, ge=1, le=600)
    material_seconds: int = Field(default=120, ge=1, le=600)
    long_seconds: int = Field(default=180, ge=1, le=600)
    provider_seconds: int = Field(default=30, ge=1, le=120)
    read_seconds: int = Field(default=60, ge=1, le=180)
    group_read_seconds: int = Field(default=180, ge=1, le=600)


class RetentionBudget(StrictModel):
    body_days: int = Field(default=30, ge=1, le=3650)
    summary_days: int = Field(default=90, ge=1, le=3650)
    completed_task_days: int = Field(default=30, ge=1, le=3650)
    dedup_minimum_days: int = Field(default=180, ge=180, le=3650)


class Settings(StrictModel):
    timezone: str = "Asia/Shanghai"
    provider_id: str = ""
    default_project: str | None = None
    allowed_projects: list[str] = Field(default_factory=list)
    project_aliases: dict[str, str] = Field(default_factory=dict)
    identity: Identity = Field(default_factory=Identity)
    account_ref: str | None = None
    account_region: Literal["cn"] = "cn"
    credential_generation: int = 0
    authorization_revision: int = 0
    min_free_bytes: int = Field(default=1024**3, ge=0)
    max_jobs: int = Field(default=100, ge=10, le=1000)
    max_inbox: int = Field(default=1000, ge=10, le=10000)
    silence_seconds: int = Field(default=30, ge=1, le=120)
    window_seconds: int = Field(default=120, ge=1, le=600)
    materials: MaterialBudget = Field(default_factory=MaterialBudget)
    time_budgets: TimeBudget = Field(default_factory=TimeBudget)
    retention: RetentionBudget = Field(default_factory=RetentionBudget)


class OperationResult(StrictModel):
    contract_version: Literal[1] = 1
    operation_id: ID
    kind: Literal["create", "update", "complete", "upload", "delete"]
    status: Literal[
        "succeeded",
        "failed_safe",
        "outcome_unknown",
        "created_unverified",
        "uploaded_unverified",
        "applied_unverified",
    ]
    side_effect: Literal["none", "applied", "unknown"]
    account_ref: ID
    remote_id: str | None = None
    actual_fields: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    error: dict | None = None
