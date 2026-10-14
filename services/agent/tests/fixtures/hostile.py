"""Hostile files (owner decision R4), generated here; each must end failed with its reason while
the platform goes on. Fake content only (G3). Also run as a script by smoke, which uploads
them through the gateway:

    python tests/fixtures/hostile.py <out dir>
"""

import io
import sys
import zipfile
import zlib
from pathlib import Path

from tests.fixtures.documents import docx_bytes, xlsx_bytes

# What each file must fail with (config/docs.yaml limits).
EXPECTED = {
    "zip-bomb.docx": "archive_unsafe",
    "entity-expansion.docx": "xml_unsafe",
    "external-entity.docx": "xml_unsafe",
    "oversized-stream.pdf": "stream_too_large",
    "fifty-thousand-rows.xlsx": "text_too_long",
}


def _replacing(data: bytes, name: str, content: bytes) -> bytes:
    """`data`, a ZIP, with entry `name` replaced by `content`."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            target.writestr(entry, content if entry.filename == name else source.read(entry))
    return out.getvalue()


def zip_bomb_docx() -> bytes:
    """A real DOCX with a 200 MB part of spaces that compresses to about 200 KB."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(docx_bytes([("مذكرة", "p")]))) as source,
        zipfile.ZipFile(out, "w") as target,
    ):
        for entry in source.infolist():
            target.writestr(entry, source.read(entry))
        padding = zipfile.ZipInfo("word/padding.xml")
        padding.compress_type = zipfile.ZIP_DEFLATED
        with target.open(padding, "w", force_zip64=True) as stream:
            block = b" " * 1_000_000
            for _ in range(200):
                stream.write(block)
    return out.getvalue()


def entity_expansion_docx() -> bytes:
    """A billion-laughs DTD in word/document.xml."""
    entities = '<!ENTITY lol "lol">' + "".join(
        f'<!ENTITY lol{i} "{("&lol" + (str(i - 1) if i > 1 else "") + ";") * 10}">' for i in range(1, 10)
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!DOCTYPE w:document [{entities}]>"
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&lol9;</w:t></w:r></w:p></w:body></w:document>"
    )
    return _replacing(docx_bytes([("مذكرة", "p")]), "word/document.xml", document.encode())


def external_entity_docx() -> bytes:
    """An XXE: an entity that names a file of the parsing host."""
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE w:document [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>"
    )
    return _replacing(docx_bytes([("مذكرة", "p")]), "word/document.xml", document.encode())


def oversized_stream_pdf() -> bytes:
    """One page whose content stream inflates to 80 MB (the limit is 50 MB)."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, StreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    stream = StreamObject()
    stream.set_data(zlib.compress(b"q Q\n" * 20_000_000, 9))
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def fifty_thousand_rows_xlsx() -> bytes:
    """50 000 rows of a leave register: more text than one file may hold."""
    rows = [["الاسم", "الإدارة", "الرصيد", "ملاحظة"]] + [
        [f"موظف رقم {i}", "إدارة التخطيط والمتابعة", 30 - i % 7, "رصيد الإجازة السنوية المتبقي"]
        for i in range(50_000)
    ]
    return xlsx_bytes({"أرصدة": rows})


BUILDERS = {
    "zip-bomb.docx": zip_bomb_docx,
    "entity-expansion.docx": entity_expansion_docx,
    "external-entity.docx": external_entity_docx,
    "oversized-stream.pdf": oversized_stream_pdf,
    "fifty-thousand-rows.xlsx": fifty_thousand_rows_xlsx,
}


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, build in BUILDERS.items():
        (out / name).write_bytes(build())
        print(name)
