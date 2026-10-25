"""Rebuild fictional SAMPLE inputs; never imported by the extraction module.

Run from the repository root: python demo/generate_documents.py
Requires reportlab, Pillow and pypdfium2, only for generating test documents.
"""

import json
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent / "documents"
BASE = {
    "passport": [
        ("Given name", "ALEXEI"), ("Surname", "SAMPLEOV"),
        ("Date of birth", "1996-03-14"), ("Passport kind", "biometric"),
        ("Date of issue", "2022-04-15"), ("Date of expiry", "2032-04-15"),
    ],
    "bank_statement": [
        ("Holder name", "ALEXEI SAMPLEOV"), ("Statement date", "2026-11-20"),
        ("Balance", "3000.00"), ("Currency", "EUR"),
    ],
    "insurance": [
        ("Holder name", "ALEXEI SAMPLEOV"), ("Valid from", "2026-12-01"),
        ("Valid until", "2027-01-31"), ("Territory", "Schengen"),
        ("Coverage amount", "50000.00"), ("Currency", "EUR"),
    ],
}
CHANGES = {
    "good": {},
    "bad": {
        "passport": {"Date of expiry": "2025-04-15"},
        "bank_statement": {"Holder name": "OTHER PERSON", "Statement date": "2024-01-01", "Balance": "100.00", "Currency": "XYZ"},
        "insurance": {"Valid from": "2026-01-01", "Valid until": "2026-01-31", "Territory": "Brazil", "Coverage amount": "100.00", "Currency": "USD"},
    },
    "corrected": {
        "passport": {"Date of expiry": "2033-04-15"},
        "bank_statement": {"Balance": "4000.00"},
        "insurance": {"Valid until": "2027-02-28"},
    },
}


def build_pdf(path: Path, document_type: str, rows: list[tuple[str, str]]) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.setTitle("VisaPilot SAMPLE - " + document_type)
    width, height = A4
    pdf.setFillColor(HexColor("#123E45"))
    pdf.rect(0, height - 155, width, 155, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#BCEBE1"))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(48, height - 48, "VISAPILOT / FICTIONAL TEST INPUT")
    pdf.setFillColor(HexColor("#FFFFFF"))
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawString(48, height - 94, "SAMPLE")
    pdf.setFont("Helvetica", 15)
    pdf.drawString(48, height - 126, document_type.replace("_", " ").upper())
    pdf.setFillColor(HexColor("#172F34"))
    pdf.setFont("Helvetica", 14)
    for index, (label, value) in enumerate(rows):
        pdf.drawString(48, height - 207 - index * 39, f"{label}: {value}")
    pdf.setStrokeColor(HexColor("#BED2D1"))
    pdf.line(48, 144, width - 48, 144)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(48, 118, "SAMPLE - NOT A VALID DOCUMENT")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(48, 98, "All names, dates and amounts are invented for local testing.")
    pdf.drawString(48, 82, "No issuer, account, passport number or real personal data.")
    pdf.showPage()
    pdf.save()


def render_png(pdf_path: Path, png_path: Path) -> None:
    with pdfium.PdfDocument(pdf_path) as pdf:
        page = pdf[0]
        try:
            bitmap = page.render(scale=2)
            try:
                with bitmap.to_pil().copy() as image:
                    image.save(png_path)
            finally:
                bitmap.close()
        finally:
            page.close()


def main() -> None:
    ROOT.mkdir(exist_ok=True)
    manifest = {"synthetic": True, "reference_trip": {"starts_on": "2026-12-01", "ends_on": "2026-12-10"}, "sets": {}}
    for variant, changes in CHANGES.items():
        manifest["sets"][variant] = {}
        for document_type, original in BASE.items():
            rows = [(label, changes.get(document_type, {}).get(label, value)) for label, value in original]
            stem = f"{variant}_{document_type}"
            build_pdf(ROOT / f"{stem}.pdf", document_type, rows)
            render_png(ROOT / f"{stem}.pdf", ROOT / f"{stem}.png")
            manifest["sets"][variant][document_type] = dict(rows)
    # An image-only PDF really exercises the PDFium -> OCR path.
    scan = canvas.Canvas(str(ROOT / "scan_passport.pdf"), pagesize=A4, invariant=1)
    scan.drawImage(str(ROOT / "good_passport.png"), 0, 0, width=A4[0], height=A4[1])
    scan.showPage()
    scan.save()
    with Image.open(ROOT / "good_passport.png") as image:
        image.convert("RGB").save(ROOT / "photo_passport.jpg", quality=90)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
