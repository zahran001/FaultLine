# FaultLine — Claude Guidance

## Import convention
`src/` has no `__init__.py`. Always use flat imports:
```python
from calibration import CALIBRATION   # correct
from src.calibration import ...        # wrong — never do this
```
pytest is configured with `pythonpath = ["src"]` so flat imports resolve in tests too.

## Project layout
- `src/` — library modules (no package init)
- `tests/` — pytest suite
- `scripts/` — one-off analysis scripts
- `docs/` — design docs
- `data/` — NASA dataset CSVs (git-ignored, not committed)

## Dev setup
```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
pytest
```
