"""Internal extraction DTOs, not replacements for app/common HTTP models."""

from dataclasses import asdict, dataclass
from typing import Literal

DocumentType = Literal["passport", "bank_statement", "insurance"]
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_PIXELS = 20_000_000
MAX_TEXT_CHARS = 100_000
DOCUMENT_TYPES = ("passport", "bank_statement", "insurance")


@dataclass(frozen=True)
class SourceLine:
    text: str
    page: int
    kind: Literal["pdf_text", "ocr"]
    confidence: float | None = None


@dataclass(frozen=True)
class CandidateField:
    key: str
    value: str | None
    confidence: float | None
    source_page: int
    source_kind: Literal["pdf_text", "ocr"]
    user_confirmed: bool = False
    confirmed_revision: int | None = None


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    field_key: str | None = None
    source_page: int | None = None


@dataclass(frozen=True)
class ExtractionResult:
    document_type: DocumentType
    extraction_state: Literal["uploaded", "extracted", "needs_input", "failed"]
    fields: tuple[CandidateField, ...] = ()
    quality_issues: tuple[QualityIssue, ...] = ()
    mime_type: str | None = None
    page_count: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ExtractionResult":
        return cls(
            document_type=value["document_type"],
            extraction_state=value["extraction_state"],
            fields=tuple(CandidateField(**field) for field in value["fields"]),
            quality_issues=tuple(QualityIssue(**issue) for issue in value["quality_issues"]),
            mime_type=value["mime_type"], page_count=value["page_count"],
        )


class ExtractionError(Exception):
    """Safe technical error; never include a file path or raw OCR content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
