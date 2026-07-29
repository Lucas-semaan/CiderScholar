"""Page-traceable article fragment model."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str | None = None
    subsection: str | None = None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    token_count: int = Field(gt=0)
    embedding_status: str = "pending"

    @model_validator(mode="after")
    def validate_pages(self) -> "Chunk":
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        return self
