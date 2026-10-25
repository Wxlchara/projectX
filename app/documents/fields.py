"""Conservative labelled-field parser. Unknown formats stay unknown.

No filename, trip rule, AI response or expected fixture values are inputs.
Dates and amounts are normalized, but not assessed for visa suitability here.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .types import CandidateField, DocumentType, ExtractionResult, QualityIssue, SourceLine

# Proposed package-local keys, to be mapped to the shared Field model by Matvey.
LABELS = {
    "given_name": ("given name", "given names", "имя"),
    "surname": ("surname", "фамилия"),
    "birth_date": ("date of birth", "birth date", "дата рождения"),
    "passport_kind": ("passport kind", "тип паспорта"),
    "issued_on": ("date of issue", "issued on", "дата выдачи"),
    "expires_on": ("date of expiry", "expires on", "действителен до"),
    "holder_name": ("holder name", "account holder", "insured person", "владелец", "застрахованный"),
    "statement_on": ("statement date", "дата выписки"),
    "balance": ("balance", "остаток"),
    "currency": ("currency", "валюта"),
    "starts_on": ("valid from", "действует с"),
    "ends_on": ("valid until", "действует до"),
    "territory": ("territory", "территория"),
    "coverage_amount": ("coverage amount", "сумма покрытия"),
}
FIELD_KEYS = {
    "passport": ("given_name", "surname", "birth_date", "passport_kind", "issued_on", "expires_on"),
    "bank_statement": ("holder_name", "statement_on", "balance", "currency"),
    "insurance": ("holder_name", "starts_on", "ends_on", "territory", "coverage_amount", "currency"),
}
DATES = {"birth_date", "issued_on", "expires_on", "statement_on", "starts_on", "ends_on"}
LOW_CONFIDENCE = 0.80


def normalize(key: str, value: str) -> str | None:
    value = " ".join(value.split())
    if not value or len(value) > 300:
        return None
    if key in {"given_name", "surname", "holder_name"}:
        # Do not silently turn OCR punctuation/digits into letters in a name.
        if not any(char.isalpha() for char in value) or any(
            not char.isalpha() and char not in " -.'’" for char in value
        ):
            return None
        return value
    if key in DATES:
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return date.fromisoformat(value).isoformat()
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
                return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
        except ValueError:
            pass
        return None
    if key in {"balance", "coverage_amount"}:
        # No guess about thousands separators, currency conversion or exponent notation.
        if not re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", value):
            return None
        try:
            return format(Decimal(value.replace(",", ".")), "f")
        except InvalidOperation:
            return None
    if key == "currency":
        # A 3-letter candidate is not proof of a recognized/comparable ISO currency.
        return value.upper() if re.fullmatch(r"[A-Za-z]{3}", value) else None
    if key == "passport_kind":
        return {
            "biometric": "biometric", "биометрический": "biometric",
            "non_biometric": "non_biometric", "non-biometric": "non_biometric",
            "небиометрический": "non_biometric",
        }.get(value.casefold())
    return value


def parse_fields(document_type: DocumentType, lines: list[SourceLine], *,
                 mime_type: str, page_count: int,
                 issues: list[QualityIssue] | None = None) -> ExtractionResult:
    findings = list(issues or [])
    fields = []
    for key in FIELD_KEYS[document_type]:
        matches = []
        labels = "|".join(re.escape(label) for label in LABELS[key])
        pattern = re.compile(rf"^\s*(?:{labels})\s*:\s*(.*?)\s*$", re.IGNORECASE)
        for line in lines:
            match = pattern.fullmatch(line.text)
            if match:
                matches.append((normalize(key, match[1]), line))
        if not matches:
            findings.append(QualityIssue("field_missing", "Заполните и подтвердите поле.", key))
            continue
        value, source = matches[0]
        if any(item[0] != value for item in matches):
            value = None
            findings.append(QualityIssue("field_conflict", "Найдены разные значения поля.", key))
        elif value is None:
            findings.append(QualityIssue("field_invalid", "Формат значения требует ручной проверки.", key))
        confidence = None if source.kind == "pdf_text" else source.confidence
        if source.kind == "ocr" and (confidence is None or confidence < LOW_CONFIDENCE):
            findings.append(QualityIssue("low_confidence", "Низкая уверенность OCR; сверьте поле с оригиналом.", key, source.page))
        fields.append(CandidateField(key, value, confidence, source.page, source.kind))
    return ExtractionResult(
        document_type, "needs_input" if findings else "extracted",
        tuple(fields), tuple(findings), mime_type, page_count,
    )
