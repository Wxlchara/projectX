"""File decoders used only in the disposable worker process."""

import csv
import io
import math
import subprocess
import threading
import time
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf import filters

from .types import (ExtractionError, MAX_FILE_BYTES, MAX_PAGES, MAX_PIXELS,
                    MAX_TEXT_CHARS, QualityIssue, SourceLine)

# Every PDFium lifetime, including object destruction, stays inside this mutex.
PDFIUM_MUTEX = threading.Lock()
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
filters.ZLIB_MAX_OUTPUT_LENGTH = 8 * 1024 * 1024


def ocr_lines(image: Image.Image, page: int, workdir: Path,
              deadline: float) -> list[SourceLine]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExtractionError("processing_timeout", "Время обработки истекло; заполните поля вручную.")
    source = workdir / f"ocr-{page}.png"
    image.save(source, format="PNG")
    try:
        # TSV provides actual word confidence; PDF text never receives OCR confidence.
        result = subprocess.run(
            ["tesseract", str(source), "stdout", "-l", "rus+eng", "--psm", "6",
             "-c", "tessedit_create_tsv=1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            timeout=remaining, shell=False,
        )
    except FileNotFoundError as exc:
        raise ExtractionError("ocr_unavailable", "Установите Tesseract с языками rus и eng или заполните поля вручную.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError("processing_timeout", "Время OCR истекло; заполните поля вручную.") from exc
    finally:
        source.unlink(missing_ok=True)
    if result.returncode:
        raise ExtractionError("ocr_unavailable", "OCR недоступен; проверьте Tesseract и языки rus/eng.")
    if len(result.stdout) > 2_000_000:
        raise ExtractionError("text_limit", "OCR-текст превышает лимит обработки.")
    groups: dict[tuple, list[tuple[str, float | None]]] = {}
    try:
        rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t")
        for row in rows:
            text = row["text"].strip()
            if row["level"] != "5" or not text:
                continue
            raw_conf = float(row["conf"])
            conf = raw_conf / 100 if math.isfinite(raw_conf) and 0 <= raw_conf <= 100 else None
            key = (row["block_num"], row["par_num"], row["line_num"])
            groups.setdefault(key, []).append((text, conf))
    except (KeyError, ValueError, UnicodeError, AttributeError) as exc:
        raise ExtractionError("ocr_invalid_output", "Не удалось прочитать результат OCR.") from exc
    return [SourceLine(" ".join(word for word, _ in words), page, "ocr",
                       min(conf for _, conf in words) if all(conf is not None for _, conf in words) else None)
            for words in groups.values()]


def render_page(data: bytes, index: int) -> Image.Image:
    with PDFIUM_MUTEX:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(data) as pdf:
            page = pdf[index]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(n) and n > 0 for n in (width, height)):
                    raise ExtractionError("invalid_pdf", "Некорректный размер PDF-страницы.")
                # 144 dpi at most; leave room for integer rounding of dimensions.
                scale = min(2.0, math.sqrt((MAX_PIXELS - 20_000) / (width * height)))
                if math.ceil(width * scale) * math.ceil(height * scale) > MAX_PIXELS:
                    raise ExtractionError("image_too_large", "Страница превышает 20 мегапикселей.")
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().copy()
                finally:
                    bitmap.close()
            finally:
                page.close()


def inspect_file(data: bytes, *, max_pages: int = MAX_PAGES,
                 max_pixels: int = MAX_PIXELS) -> tuple[str, int]:
    """Validate actual bytes before archiving, without running OCR."""
    if not data:
        raise ExtractionError("invalid_file", "Файл пуст.")
    if len(data) > MAX_FILE_BYTES:
        raise ExtractionError("file_too_large", "Максимальный размер файла — 10 MiB.")
    if data.startswith(b"%PDF-"):
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractionError("encrypted_pdf", "Зашифрованный PDF не поддерживается.")
            count = len(reader.pages)
            if not 1 <= count <= max_pages:
                raise ExtractionError("page_limit", f"Максимум страниц PDF: {max_pages}.")
            for page in reader.pages:
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
                    raise ExtractionError("text_limit", "Содержимое PDF-страницы превышает лимит.")
            return "application/pdf", count
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("invalid_pdf", "Не удалось прочитать PDF.") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"} or getattr(image, "n_frames", 1) != 1:
                    raise ExtractionError("unsupported_type", "Поддерживаются PDF, JPEG и PNG без анимации.")
                if image.width * image.height > max_pixels:
                    raise ExtractionError("image_too_large", "Изображение превышает лимит пикселей.")
                mime = "image/png" if image.format == "PNG" else "image/jpeg"
                image.load()
                return mime, 1
    except ExtractionError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ExtractionError("image_too_large", "Изображение превышает лимит пикселей.") from exc
    except UnidentifiedImageError as exc:
        raise ExtractionError("unsupported_type", "Фактический формат файла не поддерживается.") from exc
    except Exception as exc:
        raise ExtractionError("invalid_image", "Изображение повреждено.") from exc


def read_file(data: bytes, workdir: Path, deadline: float, *, max_pages: int = MAX_PAGES,
              max_pixels: int = MAX_PIXELS) -> tuple[str, int, list[SourceLine], list[QualityIssue]]:
    inspect_file(data, max_pages=max_pages, max_pixels=max_pixels)
    lines: list[SourceLine] = []
    issues: list[QualityIssue] = []
    if data.startswith(b"%PDF-"):
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractionError("encrypted_pdf", "Зашифрованный PDF не поддерживается.")
            count = len(reader.pages)
            if count == 0 or count > MAX_PAGES:
                raise ExtractionError("page_limit", "PDF должен содержать от 1 до 10 страниц.")
            for index, page in enumerate(reader.pages):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
                    raise ExtractionError("text_limit", "Содержимое PDF-страницы превышает лимит.")
                text = page.extract_text() or ""
                if text.strip():
                    lines.extend(SourceLine(line, index + 1, "pdf_text") for line in text.splitlines())
                else:
                    try:
                        image = render_page(data, index)
                        try:
                            lines.extend(ocr_lines(image, index + 1, workdir, deadline))
                        finally:
                            image.close()
                    except ExtractionError as exc:
                        issues.append(QualityIssue(exc.code, exc.message, source_page=index + 1))
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("invalid_pdf", "Не удалось прочитать PDF.") from exc
        mime = "application/pdf"
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as opened:
                    if opened.format not in {"PNG", "JPEG"}:
                        raise ExtractionError("unsupported_type", "Поддерживаются PDF, JPEG и PNG.")
                    mime = "image/png" if opened.format == "PNG" else "image/jpeg"
                    if opened.width * opened.height > MAX_PIXELS:
                        raise ExtractionError("image_too_large", "Изображение превышает 20 мегапикселей.")
                    if getattr(opened, "n_frames", 1) != 1:
                        raise ExtractionError("unsupported_type", "Анимированные изображения не поддерживаются.")
                    opened.load()
                    with ImageOps.exif_transpose(opened) as oriented:
                        with oriented.convert("RGB") as image:
                            try:
                                lines.extend(ocr_lines(image, 1, workdir, deadline))
                            except ExtractionError as exc:
                                issues.append(QualityIssue(exc.code, exc.message, source_page=1))
        except ExtractionError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ExtractionError("image_too_large", "Изображение превышает 20 мегапикселей.") from exc
        except UnidentifiedImageError as exc:
            raise ExtractionError("unsupported_type", "Фактический формат файла не поддерживается.") from exc
        except Exception as exc:
            raise ExtractionError("invalid_image", "Изображение повреждено.") from exc
        count = 1
    if sum(len(line.text) for line in lines) > MAX_TEXT_CHARS:
        raise ExtractionError("text_limit", "Текст превышает лимит обработки.")
    return mime, count, lines, issues
