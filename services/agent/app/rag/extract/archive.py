"""DOCX and XLSX are ZIP archives: checked before any parser opens them (owner decision R4).
Refused as `archive_unsafe`: more entries, more uncompressed bytes or a higher compression ratio
than config/docs.yaml `archive` allows (a zip bomb), or an archive inside (a nested archive,
by its name or its ZIP signature). Refused as `xml_unsafe`: an XML part that declares a DTD
or an entity, which no Office Open XML part needs (entity expansion, external entities).
Entries are read as streams and counted as they inflate, so an entry whose header understates
its size is caught too."""

import io
import re
import zipfile

from app.rag.config import Archive
from app.rag.extract.base import ExtractionFailed

_NESTED = (".zip", ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".jar", ".gz", ".tgz", ".7z", ".rar", ".tar")
_ZIP_SIGNATURE = b"PK\x03\x04"
_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)
_RATIO_FROM = 1_000_000  # bytes: small entries compress as they like
_BLOCK = 1 << 16


def check_archive(data: bytes, limits: Archive) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > limits.max_entries:
            raise ExtractionFailed("archive_unsafe", detail="entries")
        if sum(e.file_size for e in entries) > limits.max_uncompressed_bytes:
            raise ExtractionFailed("archive_unsafe", detail="uncompressed size")
        inflated = 0
        for entry in entries:
            name = entry.filename.lower()
            if name.endswith(_NESTED):
                raise ExtractionFailed("archive_unsafe", detail="nested archive")
            if entry.file_size > _RATIO_FROM and entry.file_size > limits.max_ratio * max(
                entry.compress_size, 1
            ):
                raise ExtractionFailed("archive_unsafe", detail="ratio")
            inflated += _scan(archive, entry, name, limits.max_uncompressed_bytes - inflated)


def _scan(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, name: str, budget: int) -> int:
    """Bytes the entry inflated to; XML parts are read whole for a DTD, others for a signature."""
    xml = name.endswith((".xml", ".rels", ".vml"))
    read = 0
    tail = b""
    try:
        with archive.open(entry) as stream:
            while True:
                block = stream.read(_BLOCK)
                if not block:
                    break
                if read == 0 and block.startswith(_ZIP_SIGNATURE):
                    raise ExtractionFailed("archive_unsafe", detail="nested archive")
                read += len(block)
                if read > budget:
                    raise ExtractionFailed("archive_unsafe", detail="inflated size")
                if xml and _DTD.search(tail + block):
                    raise ExtractionFailed("xml_unsafe", detail="DTD or entity")
                tail = block[-16:]
                if not xml:
                    break
    except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError) as exc:
        raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
    return read if xml else 0
