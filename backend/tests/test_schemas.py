import pytest
from pydantic import ValidationError

from app.schemas.schemas import ActionPlan, PlanStepSchema, TaskUnderstanding, ElementTarget


def test_task_understanding_requires_goal():
    with pytest.raises(ValidationError):
        TaskUnderstanding()  # goal is required


def test_task_understanding_defaults():
    u = TaskUnderstanding(goal="get_latest_invoice_info")
    assert u.entities == []
    assert u.constraints == []
    assert u.expected_output == []


def test_plan_step_rejects_invalid_execution_method():
    with pytest.raises(ValidationError):
        PlanStepSchema(step_id=1, action="click", execution_method="carrier_pigeon")


def test_plan_step_rejects_invalid_action_name():
    with pytest.raises(ValidationError):
        PlanStepSchema(step_id=1, action="delete_database", execution_method="browser")


def test_plan_step_accepts_valid_shape():
    step = PlanStepSchema(
        step_id=4,
        action="type",
        target=ElementTarget(role="textbox", name="Username"),
        value="demo",
        preconditions=["element_exists", "element_visible", "element_enabled"],
        expected_result="username entered",
        execution_method="browser",
    )
    assert step.action == "type"
    assert step.target.role == "textbox"


def test_action_plan_holds_ordered_steps():
    plan = ActionPlan(strategy="api_first", steps=[
        PlanStepSchema(step_id=1, action="api_request", execution_method="api"),
        PlanStepSchema(step_id=2, action="extract", execution_method="api"),
    ])
    assert len(plan.steps) == 2
    assert plan.steps[0].step_id == 1
