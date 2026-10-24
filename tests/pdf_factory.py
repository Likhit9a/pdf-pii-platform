"""Builds synthetic PDFs for tests. All names, numbers and addresses are fake."""
from __future__ import annotations

import io

import pymupdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# Fake values used across tests
EMAIL = "jane.doe@example.com"
PHONE = "+1 415 555 0132"
NAME = "Jane Doe"
SSN = "123-45-6789"
AADHAAR = "2345 6789 0123"
PAN = "ABCDE1234F"
CARD = "4111 1111 1111 1111"  # standard Luhn-valid test number
ADDRESS = "221 Baker Street, London"


def text_pdf(lines_per_page: list[list[str]], font_size: int = 12) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for lines in lines_per_page:
        y = 740
        for line in lines:
            c.setFont("Helvetica", font_size)
            c.drawString(72, y, line)
            y -= 22
        c.showPage()
    c.save()
    return buf.getvalue()


def simple_pii_pdf() -> bytes:
    return text_pdf([[
        "Account summary",
        f"Name: {NAME}",
        f"Email: {EMAIL}",
        f"Phone: {PHONE}",
        f"SSN: {SSN}",
        "Notes: quarterly review completed.",
    ]])


def multi_pii_pdf() -> bytes:
    return text_pdf([[
        "Application Form",
        f"Name: {NAME}",
        f"Email: {EMAIL}",
        f"Phone: {PHONE}",
        f"SSN: {SSN}",
        f"Aadhaar: {AADHAAR}",
        f"PAN: {PAN}",
        f"Card: {CARD}",
        f"Address: {ADDRESS}",
    ]])


def no_pii_pdf() -> bytes:
    return text_pdf([[
        "Quarterly Report",
        "Revenue increased steadily across all regions.",
        "The committee approved the proposal unanimously.",
    ]])


def repeated_value_pdf() -> bytes:
    """Same email on three pages, plus one with different casing."""
    return text_pdf([
        [f"Contact: {EMAIL}", "Page one body text."],
        ["Second page body text.", f"Reach us at {EMAIL} any time."],
        ["Third page.", f"Escalate to {EMAIL.upper()} for urgent cases."],
    ])


def wrapped_value_pdf() -> bytes:
    """A phone number split over two lines."""
    return text_pdf([[
        "Please call us on +1 415",
        "555 0132 during office hours.",
        "Thank you.",
    ]])


def pdf_with_leaks() -> bytes:
    """PII in visible text AND in metadata, an annotation, an embedded file and hidden text."""
    base = pymupdf.open(stream=simple_pii_pdf(), filetype="pdf")
    base.set_metadata({"author": NAME, "title": f"Record for {EMAIL}", "subject": PHONE})
    page = base[0]
    page.add_text_annot((300, 100), f"Call {PHONE}")
    base.embfile_add("notes.txt", f"{EMAIL} {SSN}".encode())
    out = base.tobytes()
    base.close()
    return out


def hidden_text_pdf() -> bytes:
    """Invisible (render mode 3) text layer carrying an email address."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 740, "Visible heading only")
    t = c.beginText(72, 700)
    t.setTextRenderMode(3)
    t.textLine(f"Hidden contact {EMAIL}")
    c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()


def encrypted_pdf() -> bytes:
    doc = pymupdf.open(stream=simple_pii_pdf(), filetype="pdf")
    out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()
    return out


def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    out = doc.tobytes()
    doc.close()
    return out


def many_pages_pdf(n: int) -> bytes:
    return text_pdf([[f"Page {i + 1} filler text."] for i in range(n)])


def rotated_pdf() -> bytes:
    doc = pymupdf.open(stream=simple_pii_pdf(), filetype="pdf")
    doc[0].set_rotation(90)
    out = doc.tobytes()
    doc.close()
    return out


def scanned_pdf(lines: list[str]) -> bytes:
    """An image-only page (no text layer), like a scan."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1700, 600), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((60, y), line, fill="black", font=font)
        y += 90
    png = io.BytesIO()
    img.save(png, format="PNG")

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=220)
    page.insert_image(page.rect, stream=png.getvalue())
    out = doc.tobytes()
    doc.close()
    return out
