"""Audit — content gate contracts.

Verdicts are fail-closed: the only way a stage passes is an explicit
`ok`. `warn` and `fail` block; a judge that returns invalid JSON, an
unknown status, or no response at all is treated as `fail` (never as
`ok`).
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

VerdictStatus = Literal["ok", "warn", "fail"]


class AuditVerdict(BaseModel):
    status: VerdictStatus = "fail"  # fail-closed default
    notes: str = ""
    cuteness: str = ""  # informational note (e.g. for kids-style cuteness)
    rewrite_suggestion: str = ""  # single-sentence fix for regeneration


class _AuditReportBase(BaseModel):
    verdicts: list[AuditVerdict] = Field(default_factory=list)
    vision_model: str = ""
    verdict: str = "fail"  # derived: "pass" only if every verdict is "ok"

    @model_validator(mode="after")
    def _derive_verdict(self) -> "_AuditReportBase":
        # Fail-closed: an empty verdict list (e.g. zero pages) is a failure,
        # never a pass. The audit-disabled path returns explicit ok verdicts.
        self.verdict = "pass" if self.verdicts and all(v.status == "ok" for v in self.verdicts) else "fail"
        return self


class PromptAuditReport(_AuditReportBase):
    """One verdict per page; same order/index as the input ProductPlan."""


class AssetAuditReport(_AuditReportBase):
    """One verdict per asset; same order/index as the input AssetPlan."""
