"""Minimal dependency-free PDF writer: one or more pages of positioned Helvetica text.

Enough for synthetic invoices that `pdftotext` / pypdf can read. Output is byte-deterministic.
"""

from __future__ import annotations

# (x, y, font size, bold, text), origin bottom-left, A4 = 595 x 842 pt
TextItem = tuple[int, int, int, bool, str]


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def render_pdf(items: list[TextItem], title: str = "Document") -> bytes:
    content = bytearray()
    for x, y, size, bold, text in items:
        font = b"/F2" if bold else b"/F1"
        content += b"BT " + font + b" %d Tf %d %d Td (" % (size, x, y) + _escape(text) + b") Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 6 0 R "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + bytes(content) + b"endstream",
        b"<< /Title (" + _escape(title) + b") /Producer (house synthetic data) >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 7 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
