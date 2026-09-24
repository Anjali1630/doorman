# Evaluations

This top-level folder is kept for parity with the required project structure. The actual
evaluation suite - the 5 demo-task cases and the 6 adversarial hallucination-guardrail cases,
plus the code that runs them and computes metrics - lives in
[`backend/app/services/evaluator.py`](../backend/app/services/evaluator.py), since it's tightly
coupled to the agent's internal APIs (`agent.run_agent`, `validator.validate_step`) and is
exposed through the backend as `POST /api/evaluations/run` / `GET /api/evaluations`.

See [`docs/evaluation.md`](../docs/evaluation.md) for what each case and metric means, and the
Evaluation page in the frontend (or the endpoints above) to actually run it.
