# RideCast contributor notes

Read `docs/HANDOFF.md`, `docs/CONTRACT.md` and `docs/MODELING.md` before changing domain behavior.

* Use Python 3.11/3.12. Install `pip install -e '.[ml,dev]'`. Run `pytest -q`, `ruff check .`, `pip check`.
* Keep TensorFlow/Keras/NumPy/PyArrow pins compatible. TensorFlow 2.20 + PyArrow reproduced a native crash on macOS arm64; the validated stack is in pyproject and requirements.lock.txt.
* Demand is requests per zone/bucket; completed-trip imports have separate target_semantics. Do not infer unobserved cancellations or supply from zero placeholders.
* Preserve absolute-time seeded world repeatability, including overlapping/partitioned extractions. Version map/evolution changes and rerun affected experiments.
* Forecast features must not access observations at/after their origin. Fit preprocessing on training windows only; keep target windows within chronological partitions. Use validation for selection, never chase held-out test scores.
* Keep heavy work outside HTTP. Register immutable artifacts and job success in one ownership-fenced transaction. Persist structured status and curves for dashboards.
* Keep OpenAPI, frontend/data guides and response examples in sync. Real HTTP check: start API and worker, run `python scripts/verify_http.py`.
* Keep large generated datasets/models and private data under ignored `var/`. Commit code/docs/small evidence reports, never API keys. Claim Docker/remote CI/real-world accuracy only when actually verified.
