from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.session import get_db
from app.models import orm
from app.schemas.schemas import AgentRunRequest, DemoLoadRequest, TaskCreateRequest
from app.services import agent, api_router as api_router_service, evaluator, planner
from app.services.llm import llm_client

router = APIRouter()


# --------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------

@router.post("/tasks")
def create_task(body: TaskCreateRequest, db: Session = Depends(get_db)):
    understanding, produced_by = planner.understand_task(body.text)
    task = orm.Task(
        raw_text=body.text, goal=understanding.goal, entities=understanding.entities,
        constraints=understanding.constraints, expected_output=understanding.expected_output,
    )
    db.add(task)
    db.commit()
    return {
        "id": task.id, "raw_text": task.raw_text, "goal": task.goal,
        "entities": task.entities, "constraints": task.constraints,
        "expected_output": task.expected_output, "understood_by": produced_by,
    }


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(orm.Task).order_by(orm.Task.created_at.desc()).limit(100).all()
    return [
        {"id": t.id, "raw_text": t.raw_text, "goal": t.goal, "created_at": t.created_at.isoformat()}
        for t in tasks
    ]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(orm.Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {
        "id": task.id, "raw_text": task.raw_text, "goal": task.goal, "entities": task.entities,
        "constraints": task.constraints, "expected_output": task.expected_output,
        "created_at": task.created_at.isoformat(),
    }


# --------------------------------------------------------------------
# Agent run / stop
# --------------------------------------------------------------------

@router.post("/agent/run")
async def agent_run(body: AgentRunRequest, db: Session = Depends(get_db)):
    execution = await agent.run_agent(db, body.text)
    return _serialize_execution(execution, include_steps=True)


@router.post("/agent/stop")
def agent_stop():
    # Design note (documented in README "Known limitations"): each /agent/run
    # call executes synchronously to completion within a single request/response
    # cycle rather than as a long-lived background job, so there is no
    # in-flight execution to cancel. This endpoint exists to satisfy the API
    # surface and reports that explicitly instead of pretending to stop
    # something that already finished.
    return {
        "status": "not_applicable",
        "detail": "Executions in this build run synchronously to completion within a single "
                   "/agent/run request, so there is nothing in-flight to stop.",
    }


# --------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------

def _serialize_execution(e: orm.Execution, include_steps: bool = False):
    data = {
        "id": e.id, "task_id": e.task_id, "plan_id": e.plan_id, "status": e.status,
        "strategy": e.strategy, "started_at": e.started_at.isoformat(),
        "finished_at": e.finished_at.isoformat() if e.finished_at else None,
        "success": e.success, "result": e.result, "error": e.error,
        "retries_used": e.retries_used, "replans_used": e.replans_used,
        "api_calls": e.api_calls, "browser_actions": e.browser_actions,
        "task_text": e.task.raw_text if e.task else None,
    }
    if include_steps:
        data["steps"] = [_serialize_step(s) for s in e.steps]
    return data


def _serialize_step(s: orm.ExecutionStep):
    return {
        "id": s.id, "step_label": s.step_label, "action": s.action, "target": s.target,
        "tool": s.tool, "execution_method": s.execution_method,
        "validation_result": s.validation_result, "status": s.status,
        "duration_ms": s.duration_ms, "error": s.error, "retry_count": s.retry_count,
        "timestamp": s.timestamp.isoformat(),
    }


@router.get("/executions")
def list_executions(db: Session = Depends(get_db)):
    executions = db.query(orm.Execution).order_by(orm.Execution.started_at.desc()).limit(100).all()
    return [_serialize_execution(e) for e in executions]


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, db: Session = Depends(get_db)):
    e = db.get(orm.Execution, execution_id)
    if not e:
        raise HTTPException(404, "Execution not found")
    return _serialize_execution(e, include_steps=True)


@router.get("/executions/{execution_id}/trace")
def get_execution_trace(execution_id: str, db: Session = Depends(get_db)):
    e = db.get(orm.Execution, execution_id)
    if not e:
        raise HTTPException(404, "Execution not found")
    return {"execution_id": e.id, "status": e.status, "steps": [_serialize_step(s) for s in e.steps]}


@router.get("/executions/{execution_id}/steps")
def get_execution_steps(execution_id: str, db: Session = Depends(get_db)):
    steps = db.query(orm.ExecutionStep).filter_by(execution_id=execution_id).order_by(
        orm.ExecutionStep.timestamp).all()
    return [_serialize_step(s) for s in steps]


# --------------------------------------------------------------------
# Browser inspector
# --------------------------------------------------------------------

@router.get("/browser/state")
def browser_state(execution_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(orm.BrowserObservation)
    if execution_id:
        q = q.filter_by(execution_id=execution_id)
    obs = q.order_by(orm.BrowserObservation.timestamp.desc()).first()
    if not obs:
        return {"available": False, "detail": "No browser observation recorded yet. Run a task first."}
    return {
        "available": True, "execution_id": obs.execution_id, "url": obs.url, "title": obs.title,
        "element_count": len(obs.elements or []), "timestamp": obs.timestamp.isoformat(),
    }


@router.get("/browser/dom")
def browser_dom(execution_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(orm.BrowserObservation)
    if execution_id:
        q = q.filter_by(execution_id=execution_id)
    obs = q.order_by(orm.BrowserObservation.timestamp.desc()).first()
    if not obs:
        return {"available": False, "elements": []}
    return {
        "available": True, "url": obs.url, "title": obs.title, "elements": obs.elements,
        "timestamp": obs.timestamp.isoformat(),
    }


# --------------------------------------------------------------------
# Integrations
# --------------------------------------------------------------------

def _ensure_seed_integrations(db: Session):
    names = {i.name for i in db.query(orm.Integration).all()}
    seeds = [
        ("OpenRouter", "llm", ["task_understanding", "planning", "replanning"]),
        ("Invoice API", "api", ["get_latest_invoice", "list_invoices", "download_invoice", "get_customer"]),
        ("Browser Automation", "browser", ["navigate", "click", "type", "extract", "download"]),
    ]
    for name, kind, caps in seeds:
        if name not in names:
            db.add(orm.Integration(name=name, kind=kind, status="unknown", capabilities=caps))
    db.commit()


@router.get("/integrations")
def list_integrations(db: Session = Depends(get_db)):
    _ensure_seed_integrations(db)
    integrations = db.query(orm.Integration).all()
    result = []
    for i in integrations:
        if i.name == "OpenRouter":
            status = "connected" if llm_client.is_available else "no_api_key"
        elif i.name == "Invoice API":
            status = "available" if api_router_service.is_api_available() else "unavailable"
        else:
            status = "active"
        result.append({
            "id": i.id, "name": i.name, "kind": i.kind, "status": status,
            "capabilities": i.capabilities,
            "last_tested_at": i.last_tested_at.isoformat() if i.last_tested_at else None,
            "last_test_result": i.last_test_result,
        })
    return result


@router.post("/integrations/{integration_id}/test")
def test_integration(integration_id: str, db: Session = Depends(get_db)):
    integration = db.get(orm.Integration, integration_id)
    if not integration:
        raise HTTPException(404, "Integration not found")

    if integration.name == "OpenRouter":
        if not llm_client.is_available:
            result = "No OPENROUTER_API_KEY configured - deterministic fallback is active."
            ok = False
        else:
            try:
                llm_client.chat_json(
                    "Respond with only {\"ok\": true} as JSON.", "ping",
                )
                result, ok = "OpenRouter responded successfully.", True
            except Exception as e:  # noqa: BLE001
                result, ok = f"OpenRouter test call failed: {e}", False
    elif integration.name == "Invoice API":
        ok = api_router_service.is_api_available()
        result = "Invoice API is reachable and enabled." if ok else "Invoice API is unavailable (check demo site / toggle)."
    else:
        try:
            from playwright.async_api import async_playwright
            import asyncio

            async def _check():
                async with async_playwright() as p:
                    b = await p.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
                    await b.close()
            asyncio.run(_check())
            result, ok = "Chromium launched and closed successfully.", True
        except Exception as e:  # noqa: BLE001
            result, ok = f"Playwright browser check failed: {e}", False

    integration.status = "connected" if ok else "error"
    integration.last_test_result = result
    from app.models.orm import utcnow
    integration.last_tested_at = utcnow()
    db.commit()
    return {"ok": ok, "detail": result}


# --------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------

@router.get("/evaluations")
def list_evaluations(db: Session = Depends(get_db)):
    runs = db.query(orm.EvaluationRun).order_by(orm.EvaluationRun.started_at.desc()).limit(20).all()
    return [
        {
            "id": r.id, "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "total_tasks": r.total_tasks, "metrics": r.metrics,
        }
        for r in runs
    ]


@router.post("/evaluations/run")
async def run_evaluations(db: Session = Depends(get_db)):
    run = await evaluator.run_evaluation(db)
    results = db.query(orm.EvaluationResult).filter_by(run_id=run.id).all()
    return {
        "id": run.id, "metrics": run.metrics,
        "results": [
            {
                "case_name": r.case_name, "case_kind": r.case_kind, "success": r.success,
                "unsafe_action_blocked": r.unsafe_action_blocked, "recovered": r.recovered,
                "steps_taken": r.steps_taken, "duration_ms": r.duration_ms, "detail": r.detail,
            }
            for r in results
        ],
    }


# --------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------

@router.get("/analytics")
def analytics(db: Session = Depends(get_db)):
    executions = db.query(orm.Execution).all()
    total = len(executions)
    completed = [e for e in executions if e.status == "COMPLETED"]
    failed = [e for e in executions if e.status == "FAILED"]
    total_api_calls = sum(e.api_calls for e in executions)
    total_browser_actions = sum(e.browser_actions for e in executions)
    total_retries = sum(e.retries_used for e in executions)
    total_replans = sum(e.replans_used for e in executions)

    return {
        "total_executions": total,
        "completed": len(completed),
        "failed": len(failed),
        "success_rate_pct": round(100 * len(completed) / total, 1) if total else 0.0,
        "total_api_calls": total_api_calls,
        "total_browser_actions": total_browser_actions,
        "api_usage_rate_pct": round(
            100 * total_api_calls / max(total_api_calls + total_browser_actions, 1), 1
        ),
        "total_retries": total_retries,
        "total_replans": total_replans,
        "llm_mode": settings.LLM_MODE,
    }


# --------------------------------------------------------------------
# Demo mode
# --------------------------------------------------------------------

@router.post("/demo/load")
def demo_load(body: DemoLoadRequest, db: Session = Depends(get_db)):
    _ensure_seed_integrations(db)
    if body.reset:
        _reset_demo_site()
    return {
        "status": "loaded",
        "demo_site_url": settings.DEMO_SITE_BASE_URL,
        "demo_credentials": {"username": settings.DEMO_SITE_USERNAME, "password": settings.DEMO_SITE_PASSWORD},
        "sample_tasks": evaluator.DEMO_TASKS,
    }


@router.post("/demo/reset")
def demo_reset(db: Session = Depends(get_db)):
    db.query(orm.ExecutionStep).delete()
    db.query(orm.ToolCall).delete()
    db.query(orm.BrowserObservation).delete()
    db.query(orm.Artifact).delete()
    db.query(orm.Execution).delete()
    db.query(orm.PlanStep).delete()
    db.query(orm.Plan).delete()
    db.query(orm.Task).delete()
    db.query(orm.EvaluationResult).delete()
    db.query(orm.EvaluationRun).delete()
    db.commit()
    _reset_demo_site()
    return {"status": "reset"}


def _reset_demo_site():
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(f"{settings.DEMO_SITE_BASE_URL}/api/demo/reset")
    except httpx.RequestError:
        pass  # Demo site may not be running yet; not fatal for a DB reset.
