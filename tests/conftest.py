# SPDX-License-Identifier: AGPL-3.0-only
"""A synthetic section drawing at 1:2, made for the tests (no manufacturer's drawing is committed)."""
import base64

import pymupdf
import pytest

PT_PER_MM = 72 / 25.4
SCALE = 2                       # 1:2
K = PT_PER_MM / SCALE           # pt per real mm on the sheet
X0, Y0 = 100.0, 300.0           # profile's bottom-left on the page (pt, y down)


def mm(x, y):
    """Real profile mm (y up) to page pt."""
    return pymupdf.Point(X0 + x * K, Y0 - y * K)


@pytest.fixture(scope='session')
def drawing_pdf():
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    # a hollow frame profile 100 x 50 mm with a 40 x 20 mm chamber, drawn as outlines
    page.draw_rect(pymupdf.Rect(mm(0, 50), mm(100, 0)), color=(0, 0, 0), width=1.2)
    page.draw_rect(pymupdf.Rect(mm(30, 35), mm(70, 15)), color=(0, 0, 0), width=1.2)
    # a 4 mm glass pane above it, 40 mm tall
    page.draw_rect(pymupdf.Rect(mm(10, 100), mm(14, 60)), color=(0, 0, 0), width=0.6)
    # dimension lines with their printed values: 100 (horizontal, below) and 50 (vertical, right)
    page.draw_line(mm(0, -15), mm(100, -15), color=(0, 0, 0), width=0.4)
    page.insert_text(mm(46, -12), '100', fontsize=9)
    page.draw_line(mm(115, 0), mm(115, 50), color=(0, 0, 0), width=0.4)
    page.insert_text(mm(118, 23), '50', fontsize=9, rotate=90)
    return doc.tobytes()


@pytest.fixture(scope='session')
def pdf_b64(drawing_pdf):
    return base64.b64encode(drawing_pdf).decode()


@pytest.fixture(scope='session')
def profile_clip():
    a, b = mm(-5, 105), mm(105, -5)
    return [round(a.x, 2), round(a.y, 2), round(b.x, 2), round(b.y, 2)]


@pytest.fixture(scope='session')
def scale_clip():
    a, b = mm(-10, 110), mm(130, -25)
    return [round(a.x, 2), round(a.y, 2), round(b.x, 2), round(b.y, 2)]
