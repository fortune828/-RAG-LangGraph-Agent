"""Frozen v14 answer schema for archive metrics only; not a generation fallback."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Citation(StrictModel):
    evidenceId: str
    quote: str = Field(min_length=2, max_length=600)


class Paragraph(StrictModel):
    kind: Literal['interpretation', 'reflection']
    text: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(default_factory=list, max_length=5)


class Answer(StrictModel):
    status: Literal['answered', 'insufficient']
    summary: str = Field(min_length=1, max_length=600)
    paragraphs: list[Paragraph] = Field(max_length=6)
    limitations: list[Annotated[str, Field(max_length=600)]] = Field(default_factory=list, max_length=8)
