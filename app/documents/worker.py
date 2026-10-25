"""Private subprocess entry point. Parent owns and removes the temporary directory."""

import json
import sys
import time
from pathlib import Path

from .fields import parse_fields
from .readers import read_file
from .types import DOCUMENT_TYPES, ExtractionError, ExtractionResult, MAX_FILE_BYTES, QualityIssue


def main() -> None:
    document_type, directory, timeout = sys.argv[1:]
    if document_type not in DOCUMENT_TYPES:
        raise SystemExit(2)
    try:
        mime, count, lines, issues = read_file(
            sys.stdin.buffer.read(MAX_FILE_BYTES + 1), Path(directory),
            time.monotonic() + float(timeout),
        )
        result = parse_fields(document_type, lines, mime_type=mime, page_count=count, issues=issues)
    except ExtractionError as exc:
        result = ExtractionResult(document_type, "failed", quality_issues=(QualityIssue(exc.code, exc.message),))
    except Exception:
        result = ExtractionResult(document_type, "failed", quality_issues=(
            QualityIssue("processing_failed", "Обработка недоступна; заполните поля вручную."),))
    print(json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
