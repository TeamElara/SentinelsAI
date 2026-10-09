"""Exporter registry — maps a format_id string to the `Exporter` that
handles it. Adding a format is one new file plus one line here."""
from __future__ import annotations

from typing import Optional

from report.base import Exporter
from report.json_export import JsonExporter
from report.markdown import MarkdownExporter
from report.pdf import PdfExporter
from report import pdf

_EXPORTERS: dict[str, Exporter] = {
    exporter.format_id: exporter
    for exporter in (PdfExporter(), JsonExporter(), MarkdownExporter())
}


def get_exporter(format_id: str) -> Optional[Exporter]:
    return _EXPORTERS.get(format_id)


def list_formats() -> list[dict[str, str]]:
    return [
        {
            "format_id": exporter.format_id,
            "media_type": exporter.media_type,
            "extension": exporter.extension,
        }
        for exporter in _EXPORTERS.values()
        if exporter.format_id != "pdf" or pdf.pdf_enabled()
    ]
