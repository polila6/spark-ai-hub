"""Diagnostic job used by acceptance tests for queueing, reservations and cancellation."""
from __future__ import annotations

import time

from ...services.scheduler import Requirement
from ..context import JobContext
from . import Handler, register


@register
class DiagnosticSleep(Handler):
    type = "diagnostic_sleep"

    def requirements(self, db, job):
        return Requirement(memory_gb=float(job.params.get("memory_gb", 0)), note="diagnostic reservation")

    def run(self, ctx: JobContext) -> None:
        total = int(ctx.params.get("seconds", 20))
        for i in range(total):
            ctx.raise_if_cancelled()
            ctx.progress((i + 1) / total, f"{i + 1}/{total} s")
            time.sleep(1)
        ctx.set_result({"slept_s": total})
