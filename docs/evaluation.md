# Evaluation

`POST /api/evaluations/run` (or the "Run Evaluation Suite" button on the Evaluation page)
runs two kinds of cases for real and computes every metric from the actual results - nothing
in `app/services/evaluator.py` is hardcoded.

## Demo tasks (5)

Each of the 5 required end-to-end tasks is run through the real `agent.run_agent()` - real
task understanding, real planning, real API/Playwright execution. Success is
`execution.success`; steps and duration come from the real `ExecutionStep` rows and wall-clock
timing.

## Adversarial hallucination cases (6)

Directly exercise `validator.validate_step()` (see `docs/guardrails.md` for the table).
These don't need a browser - they check the validator's decision against a constructed fake
DOM observation, which is what makes them fast and deterministic to run on every evaluation
pass.

## Metrics computed

| Metric | Definition |
|---|---|
| `task_success_rate_pct` | % of the 5 demo tasks that completed successfully |
| `adversarial_pass_rate_pct` | % of adversarial cases where the validator's decision matched the expected (safe) outcome |
| `false_action_rate_pct` | % of adversarial cases where the validator made the *wrong* call (100 − pass rate) |
| `validation_rejection_rate_pct` | % of adversarial cases where an unsafe action was actually blocked |
| `recovery_rate_pct` | % of adversarial cases where a safe retarget was found after an initial rejection |
| `average_steps_per_task` | Mean `ExecutionStep` count across the 5 demo tasks |
| `average_execution_time_ms` | Mean wall-clock duration across the 5 demo tasks |

All of this is persisted (`evaluation_runs`, `evaluation_results` tables) so
`GET /api/evaluations` can show run history, not just the latest result.

## Re-running

The evaluation suite can be re-run at any time (e.g. after toggling the demo site's API
switch, or after editing a goal handler) and will reflect the current, real behavior of the
agent - there's no cached or precomputed metric anywhere in this path.
