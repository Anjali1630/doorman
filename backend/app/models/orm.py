import datetime
import uuid

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import relationship

from app.database.session import Base


def gen_id() -> str:
    return uuid.uuid4().hex[:16]


def utcnow():
    return datetime.datetime.utcnow()


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_id)
    raw_text = Column(Text, nullable=False)
    goal = Column(String, nullable=True)
    entities = Column(JSON, default=list)
    constraints = Column(JSON, default=list)
    expected_output = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)

    plans = relationship("Plan", back_populates="task")
    executions = relationship("Execution", back_populates="task")


class Plan(Base):
    __tablename__ = "plans"
    id = Column(String, primary_key=True, default=gen_id)
    task_id = Column(String, ForeignKey("tasks.id"))
    strategy = Column(String, default="unknown")  # api_first / browser_fallback / hybrid
    created_by = Column(String, default="deterministic_fallback")  # or openrouter
    created_at = Column(DateTime, default=utcnow)

    task = relationship("Task", back_populates="plans")
    steps = relationship("PlanStep", back_populates="plan", order_by="PlanStep.step_id")


class PlanStep(Base):
    __tablename__ = "plan_steps"
    id = Column(String, primary_key=True, default=gen_id)
    plan_id = Column(String, ForeignKey("plans.id"))
    step_id = Column(Integer)
    action = Column(String)
    target = Column(JSON, default=dict)
    value = Column(String, nullable=True)
    preconditions = Column(JSON, default=list)
    expected_result = Column(String, nullable=True)
    execution_method = Column(String, default="browser")  # api / browser

    plan = relationship("Plan", back_populates="steps")


class Execution(Base):
    __tablename__ = "executions"
    id = Column(String, primary_key=True, default=gen_id)
    task_id = Column(String, ForeignKey("tasks.id"))
    plan_id = Column(String, ForeignKey("plans.id"), nullable=True)
    status = Column(String, default="IDLE")  # state machine states
    strategy = Column(String, default="unknown")
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    success = Column(Boolean, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    retries_used = Column(Integer, default=0)
    replans_used = Column(Integer, default=0)
    api_calls = Column(Integer, default=0)
    browser_actions = Column(Integer, default=0)

    task = relationship("Task", back_populates="executions")
    steps = relationship("ExecutionStep", back_populates="execution", order_by="ExecutionStep.timestamp")


class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    id = Column(String, primary_key=True, default=gen_id)
    execution_id = Column(String, ForeignKey("executions.id"))
    step_label = Column(String)
    action = Column(String, nullable=True)
    target = Column(JSON, nullable=True)
    tool = Column(String, nullable=True)
    execution_method = Column(String, nullable=True)  # api / browser / planning / validation
    validation_result = Column(String, nullable=True)  # passed / rejected / n/a
    status = Column(String, default="success")  # success / failed / skipped
    duration_ms = Column(Float, default=0.0)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    screenshot_path = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utcnow)

    execution = relationship("Execution", back_populates="steps")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id = Column(String, primary_key=True, default=gen_id)
    execution_id = Column(String, ForeignKey("executions.id"))
    tool_name = Column(String)
    arguments = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)
    success = Column(Boolean, default=True)
    duration_ms = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=utcnow)


class BrowserObservation(Base):
    __tablename__ = "browser_observations"
    id = Column(String, primary_key=True, default=gen_id)
    execution_id = Column(String, ForeignKey("executions.id"))
    url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    elements = Column(JSON, default=list)
    timestamp = Column(DateTime, default=utcnow)


class Integration(Base):
    __tablename__ = "integrations"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, unique=True)
    kind = Column(String)  # llm / api / browser
    status = Column(String, default="unknown")
    capabilities = Column(JSON, default=list)
    last_tested_at = Column(DateTime, nullable=True)
    last_test_result = Column(String, nullable=True)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id = Column(String, primary_key=True, default=gen_id)
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    total_tasks = Column(Integer, default=0)
    metrics = Column(JSON, default=dict)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id = Column(String, primary_key=True, default=gen_id)
    run_id = Column(String, ForeignKey("evaluation_runs.id"))
    case_name = Column(String)
    case_kind = Column(String)  # demo_task / adversarial
    success = Column(Boolean)
    unsafe_action_blocked = Column(Boolean, default=False)
    recovered = Column(Boolean, default=False)
    steps_taken = Column(Integer, default=0)
    duration_ms = Column(Float, default=0.0)
    detail = Column(Text, nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    id = Column(String, primary_key=True, default=gen_id)
    execution_id = Column(String, ForeignKey("executions.id"))
    kind = Column(String)  # download / screenshot / extraction
    path = Column(String, nullable=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
