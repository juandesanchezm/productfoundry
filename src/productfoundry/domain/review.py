"""Review — quality gate report."""
from typing import Literal
from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    criterion: str
    severity: Literal["error", "warning"]
    detail: str


class ReviewReport(BaseModel):
    verdict: Literal["pass", "fail"] = "pass"
    issues: list[ReviewIssue] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
