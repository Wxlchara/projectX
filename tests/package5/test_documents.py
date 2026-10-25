"""Content-driven extraction tests; OCR fixtures never stand in for live OCR."""

import asyncio
import io
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.documents import DocumentExtractor
from app.documents.fields import parse_fields
from app.documents.readers import ocr_lines
from app.documents.types import MAX_FILE_BYTES, SourceLine

SAMPLES = Path(__file__).resolve().parents[2] / "demo" / "documents"


def extract(data, kind="passport", **kwargs):
    return asyncio.run(DocumentExtractor(**kwargs).extract(data, kind))


@pytest.mark.parametrize("variant", ["good", "bad", "corrected"])
@pytest.mark.parametrize("kind", ["passport", "bank_statement", "insurance"])
def test_real_pdf_content_and_unconfirmed_provenance(kind, variant):
    result = extract((SAMPLES / f"{variant}_{kind}.pdf").read_bytes(), kind)
    assert result.extraction_state == "extracted"
    assert result.mime_type == "application/pdf"
    assert result.page_count == 1
    assert all(field.confidence is None and field.source_kind == "pdf_text" for field in result.fields)
    assert all(not field.user_confirmed and field.confirmed_revision is None for field in result.fields)
    values = {field.key: field.value for field in result.fields}
    if kind == "passport":
        assert values["expires_on"] == {"good": "2032-04-15", "bad": "2025-04-15", "corrected": "2033-04-15"}[variant]
    elif kind == "bank_statement":
        assert values["balance"] == {"good": "3000.00", "bad": "100.00", "corrected": "4000.00"}[variant]
    else:
        assert values["territory"] == ("Brazil" if variant == "bad" else "Schengen")


def test_renaming_files_does_not_change_results(tmp_path):
    # Put bad bytes under a deliberately misleading good filename.
    misleading = tmp_path / "good_passport.pdf"
    misleading.write_bytes((SAMPLES / "bad_passport.pdf").read_bytes())
    result = extract(misleading.read_bytes())
    assert next(field.value for field in result.fields if field.key == "expires_on") == "2025-04-15"


@pytest.mark.parametrize("data,code", [
    (b"", "invalid_file"), (b"arbitrary text", "unsupported_type"),
    (b"%PDF-1.7\ncorrupt", "invalid_pdf"),
    (b"x" * (MAX_FILE_BYTES + 1), "file_too_large"),
])
def test_invalid_inputs(data, code):
    result = extract(data)
    assert result.extraction_state == "failed"
    assert result.quality_issues[0].code == code


def test_encrypted_and_excess_pages_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("password")
    stream = io.BytesIO()
    writer.write(stream)
    assert extract(stream.getvalue()).quality_issues[0].code == "encrypted_pdf"
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    assert extract(stream.getvalue()).quality_issues[0].code == "page_limit"


def test_image_pixel_limit():
    stream = io.BytesIO()
    with Image.new("1", (4500, 4500)) as image:
        image.save(stream, format="PNG")
    assert extract(stream.getvalue()).quality_issues[0].code == "image_too_large"


def test_blank_scan_never_becomes_valid_document(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    result = extract(stream.getvalue(), temporary_root=tmp_path)
    assert result.extraction_state == "needs_input"
    assert result.fields == ()
    assert "field_missing" in {issue.code for issue in result.quality_issues}
    assert list(tmp_path.iterdir()) == []


def test_image_only_pdf_is_really_a_scan():
    assert not PdfReader(SAMPLES / "scan_passport.pdf").pages[0].extract_text().strip()


def test_timeout_kills_worker_and_cleans_temporary_files(tmp_path):
    data = (SAMPLES / "good_passport.pdf").read_bytes()
    result = extract(data, timeout_seconds=0.001, temporary_root=tmp_path)
    assert result.extraction_state == "needs_input"
    assert result.quality_issues[0].code == "processing_timeout"
    assert list(tmp_path.iterdir()) == []
    assert extract(data).extraction_state == "extracted"


@pytest.mark.parametrize("filename", ["good_passport.png", "photo_passport.jpg", "scan_passport.pdf"])
def test_missing_ocr_is_explicit_needs_input(filename, monkeypatch, tmp_path):
    # Real image/PDF decoding with Tesseract deliberately absent from PATH.
    monkeypatch.setenv("PATH", str(tmp_path))
    result = extract((SAMPLES / filename).read_bytes(), temporary_root=tmp_path)
    assert result.extraction_state == "needs_input"
    assert "ocr_unavailable" in {issue.code for issue in result.quality_issues}
    assert result.fields == ()
    assert not list(tmp_path.iterdir())


def test_cancelled_extraction_cleans_worker_and_releases_lock(tmp_path):
    async def scenario():
        data = (SAMPLES / "good_passport.pdf").read_bytes()
        task = asyncio.create_task(DocumentExtractor(temporary_root=tmp_path).extract(data, "passport"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(tmp_path.iterdir())
        assert (await DocumentExtractor().extract(data, "passport")).extraction_state == "extracted"
    asyncio.run(scenario())


def test_concurrent_extraction_is_busy_and_event_loop_keeps_running():
    async def scenario():
        extractor = DocumentExtractor()
        data = (SAMPLES / "good_passport.pdf").read_bytes()
        first = asyncio.create_task(extractor.extract(data, "passport"))
        await asyncio.sleep(0.01)
        second = await DocumentExtractor().extract(data, "passport")
        assert second.quality_issues[0].code == "processing_busy"
        assert (await first).extraction_state == "extracted"
    asyncio.run(scenario())


def parse(lines, kind="passport"):
    return parse_fields(kind, lines, mime_type="image/png", page_count=1)


def test_low_confidence_missing_invalid_and_conflicting_fields():
    result = parse([
        SourceLine("Given name: ALEXEI", 1, "ocr", 0.25),
        SourceLine("Surname: SAMPLEOV", 1, "ocr", 0.99),
        SourceLine("Date of birth: 1996-02-31", 1, "ocr", 0.95),
        SourceLine("Date of expiry: 2032-04-15", 1, "ocr", 0.95),
        SourceLine("Date of expiry: 2033-04-15", 1, "ocr", 0.95),
    ])
    assert result.extraction_state == "needs_input"
    assert {"field_missing", "field_invalid", "field_conflict", "low_confidence"} <= {issue.code for issue in result.quality_issues}
    fields = {field.key: field for field in result.fields}
    assert fields["given_name"].confidence == 0.25
    assert fields["birth_date"].value is None
    assert fields["expires_on"].value is None


def test_russian_labels_decimal_and_unknown_currency():
    result = parse([
        SourceLine("Владелец: ALEXEI SAMPLEOV", 1, "pdf_text"),
        SourceLine("Дата выписки: 20.11.2026", 1, "pdf_text"),
        SourceLine("Остаток: 100,25", 1, "pdf_text"),
        SourceLine("Валюта: неизвестно", 1, "pdf_text"),
    ], "bank_statement")
    values = {field.key: field.value for field in result.fields}
    assert values["statement_on"] == "2026-11-20"
    assert values["balance"] == "100.25"
    assert values["currency"] is None
    assert result.extraction_state == "needs_input"


def test_ocr_tsv_fixture_preserves_low_word_confidence_and_removes_image(tmp_path):
    # Local TSV parser fixture only. No Tesseract/AI call is asserted by this test.
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95\tSurname:\n"
           "5\t1\t1\t1\t1\t2\t10\t0\t10\t10\t21\tSAMPLEOV\n").encode()
    with patch("app.documents.readers.subprocess.run", return_value=subprocess.CompletedProcess([], 0, tsv)) as runner:
        import time
        with Image.new("RGB", (100, 100)) as image:
            lines = ocr_lines(image, 1, tmp_path, time.monotonic() + 10)
        assert runner.call_args.args[0][4] == "rus+eng"
    assert lines == [SourceLine("Surname: SAMPLEOV", 1, "ocr", 0.21)]
    assert not list(tmp_path.iterdir())


def has_real_ocr():
    if not shutil.which("tesseract"):
        return False
    result = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=False)
    return {"rus", "eng"} <= set(result.stdout.split())


@pytest.mark.skipif(not has_real_ocr(), reason="Real Tesseract rus+eng not installed; TSV fixtures are not live OCR")
@pytest.mark.parametrize("filename", ["good_passport.png", "good_bank_statement.png", "good_insurance.png", "scan_passport.pdf", "photo_passport.jpg"])
def test_real_ocr_when_available(filename):
    kind = "bank_statement" if "bank_statement" in filename else "insurance" if "insurance" in filename else "passport"
    result = extract((SAMPLES / filename).read_bytes(), kind)
    values = {field.key: field.value for field in result.fields}
    assert values.get("given_name", values.get("holder_name")) in {"ALEXEI", "ALEXEI SAMPLEOV"}
    assert result.fields and all(field.source_kind == "ocr" for field in result.fields)
    assert all(not field.user_confirmed for field in result.fields)
