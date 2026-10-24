import pymupdf

import pdf_factory as F
from pii_platform.detection import detect_pii, luhn_ok
from pii_platform.extraction import extract_document


def _detect(settings, data, **kw):
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = extract_document(doc, settings).pages
    doc.close()
    return detect_pii(pages, settings, **kw)


def _cats(dets):
    return {d.category: d.value for d in dets}


def test_all_supported_categories_detected(settings):
    found = _cats(_detect(settings, F.multi_pii_pdf()))
    assert found["EMAIL"] == F.EMAIL
    assert found["PHONE"] == F.PHONE
    assert found["PERSON"] == F.NAME
    assert found["SSN"] == F.SSN
    assert found["AADHAAR"] == F.AADHAAR
    assert found["PAN"] == F.PAN
    assert found["CREDIT_CARD"] == F.CARD
    assert found["ADDRESS"].startswith("221 Baker Street")


def test_no_pii_document_has_no_detections(settings):
    assert _detect(settings, F.no_pii_pdf()) == []


def test_luhn_rejects_random_16_digit_numbers():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_invalid_card_number_not_flagged(settings):
    data = F.text_pdf([["Order reference 4111 1111 1111 1112 shipped."]])
    assert "CREDIT_CARD" not in _cats(_detect(settings, data))


def test_dates_and_short_numbers_not_flagged_as_phone(settings):
    data = F.text_pdf([["Invoice 2024-03-15 total 12345 due 15/03/2024."]])
    assert _detect(settings, data) == []


def test_value_split_across_lines_gets_one_rect_per_line(settings):
    dets = _detect(settings, F.wrapped_value_pdf(), categories={"PHONE"})
    assert len(dets) == 1 and len(dets[0].rects) == 2


def test_category_toggle_respected(settings):
    dets = _detect(settings, F.multi_pii_pdf(), categories={"EMAIL"})
    assert {d.category for d in dets} == {"EMAIL"}


def test_custom_pattern_detected(settings):
    settings.custom_patterns = {"EMPLOYEE_ID": r"EMP-\d{6}"}
    data = F.text_pdf([["Staff member EMP-123456 joined in May."]])
    found = _cats(_detect(settings, data, categories={"EMPLOYEE_ID"}))
    assert found == {"EMPLOYEE_ID": "EMP-123456"}


def test_detection_has_page_and_location(settings):
    dets = _detect(settings, F.repeated_value_pdf(), categories={"EMAIL"})
    assert sorted(d.page for d in dets) == [1, 2, 3]
    assert all(d.rects for d in dets)
