"""Job handler registry."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ...models import Job
from ...services.scheduler import Requirement
from ..context import JobContext


class Handler:
    type: str = ""

    def requirements(self, db: Session, job: Job) -> Requirement:
        return Requirement()

    def run(self, ctx: JobContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def cleanup(self, ctx: JobContext) -> None:
        pass


HANDLERS: dict[str, Handler] = {}


def register(cls):
    HANDLERS[cls.type] = cls()
    return cls


from . import audio, bench, cloud, diagnostic, downloads, maintenance_jobs, media, models, science  # noqa: E402,F401
