"""Local package 5/6 entry point, without modifying Matvey's composition root.

python -m uvicorn app.checks.application:create_app --factory --workers 1
"""

from app.common.config import Settings
from app.documents.router import router as documents_router
from app.documents.service import DocumentArchive
from app.main import create_app as create_backend
from .router import router
from .service import ChecksService


def create_app(settings: Settings | None = None, *, checker=None, rebuilder=None, clock=None):
    app = create_backend(settings, feature_routers={5: documents_router, 6: router})
    app.state.document_archive = DocumentArchive(app.state.database, app.state.settings, rebuilder=rebuilder)
    app.state.checks_service = ChecksService(
        app.state.database, app.state.rule_registry, app.state.document_archive,
        checker=checker, rebuilder=rebuilder, clock=clock,
    )
    app.description = (
        "VisaPilot: пакеты 1/11/2/3/5/6. Проверки используют реальные правила и подтверждённые поля. "
        "Полнота сценариев частичная; CRUD дел ожидает пакет 4, AI не вызывается."
    )
    return app
