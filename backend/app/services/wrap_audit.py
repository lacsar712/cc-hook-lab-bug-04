"""死信计数回绕读轨审计。"""
from __future__ import annotations

from app.services import wrap as core
from app.services import wrap_cutover as cutover
from app.services import wrap_dualwrite as dual


def audit_summary() -> dict:
    return {
        "title": "死信计数回绕审计",
        "core": core.dashboard(),
        "cutover": cutover.board(),
        "dualwrite": dual.dual_board(),
        "half": core.half_fix_display(),
    }
