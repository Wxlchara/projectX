"""Runnable package 5 composition using package 1's public extension point.

uvicorn app.documents.application:create_app --factory --workers 1
Matvey can instead pass router to his own create_app(feature_routers={5: router}).
"""

from app.common.config import Settings
from app.main import create_app as create_backend
from .router import router
from .service import DocumentArchive


def create_app(settings: Settings | None = None, *, extractor=None, rebuilder=None):
    application = create_backend(settings, feature_routers={5: router})
    application.state.document_archive = DocumentArchive(
        application.state.database, application.state.settings,
        extractor=extractor, rebuilder=rebuilder,
    )
    application.description = (
        "Локальное демо VisaPilot с аккаунтами и личным архивом (пакеты 1/11/5). "
        "Только вымышленные документы. AI по умолчанию выключен; остальные пакеты пока недоступны."
    )
    return application
