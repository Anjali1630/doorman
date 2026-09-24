"""
Structured schemas used throughout the agent. The LLM (or deterministic
fallback) is required to produce output that validates against these
models - free-form text is never trusted directly.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# Task understanding
# --------------------------------------------------------------------------

class TaskUnderstanding(BaseModel):
    goal: str
    entities: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    expected_output: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

class ToolName(str, Enum):
    navigate = "navigate"
    click = "click"
    type = "type"
    select = "select"
    press = "press"
    wait = "wait"
    extract = "extract"
    inspect_page = "inspect_page"
    screenshot = "screenshot"
    download = "download"
    api_request = "api_request"
    go_back = "go_back"
    go_forward = "go_forward"


class ElementTarget(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    test_id: Optional[str] = None
    css: Optional[str] = None
    xpath: Optional[str] = None


class PlanStepSchema(BaseModel):
    step_id: int
    action: ToolName
    target: Optional[ElementTarget] = None
    value: Optional[str] = None
    preconditions: List[str] = Field(default_factory=list)
    expected_result: Optional[str] = None
    execution_method: str = "browser"  # "api" or "browser"
    api_meta: Optional[Dict[str, Any]] = None  # method/path/params for api_request steps

    @field_validator("execution_method")
    @classmethod
    def _valid_method(cls, v):
        if v not in ("api", "browser"):
            raise ValueError("execution_method must be 'api' or 'browser'")
        return v


class ActionPlan(BaseModel):
    strategy: str  # api_first / browser_fallback / hybrid
    steps: List[PlanStepSchema]


# --------------------------------------------------------------------------
# API request/response bodies
# --------------------------------------------------------------------------

class TaskCreateRequest(BaseModel):
    text: str


class AgentRunRequest(BaseModel):
    text: str
    allow_browser: bool = True
    allow_api: bool = True


class ClarificationResponse(BaseModel):
    execution_id: str
    choice: str


class DemoLoadRequest(BaseModel):
    reset: bool = True
