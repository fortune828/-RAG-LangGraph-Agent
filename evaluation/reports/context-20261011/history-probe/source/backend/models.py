"""Validated JSON boundaries. Camel-case names retain the existing UI contract."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Section = Literal['judgment', 'line', 'tuan', 'xiang', 'line_xiang', 'special']
Layer = Literal['classic', 'commentary', 'subcommentary', 'modern_editorial']
Names = Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=100)]], Field(min_length=1, max_length=12)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requestId: UUID
    question: Text


class TurnRequest(CreateRequest):
    retry: bool = False


class Filters(StrictModel):
    authorIds: Names | None = None
    workIds: Names | None = None
    editionIds: Names | None = None
    anchorIds: Names | None = None
    layers: Annotated[list[Layer], Field(min_length=1, max_length=4)] | None = None


class PassageArgs(Filters):
    hexagramId: int = Field(ge=1, le=64)
    sections: list[Section] = Field(min_length=1, max_length=6)
    lineIndices: Annotated[list[Annotated[int, Field(ge=1, le=7)]], Field(min_length=1, max_length=7)] | None = None


class SearchArgs(Filters):
    query: str = Field(min_length=1, max_length=160)
    hexagramId: int | None = Field(default=None, ge=1, le=64)
    topK: int = Field(default=4, ge=1, le=6)


class Citation(StrictModel):
    evidenceId: str
    quote: str = Field(min_length=2, max_length=600)
    claim: str = Field(min_length=1, max_length=8000)


class Paragraph(StrictModel):
    text: str = Field(min_length=1, max_length=16000)
    citations: list[Citation] = Field(default_factory=list, max_length=5)


class Answer(StrictModel):
    status: Literal['answered', 'insufficient']
    paragraphs: list[Paragraph] = Field(min_length=1)
    limitations: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=8)
