"""Bounded permit and inspection-document links from a Norske utslipp factsheet."""
from __future__ import annotations
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from bs4 import BeautifulSoup
from .changes import SnapshotSource, document, integer, public_url
from .common import SourceError

HOST = "www.norskeutslipp.no"
PDF_PATH = "/WebHandlers/PDFDocumentHandler.ashx"

class IndustrialDocumentsSource(SnapshotSource):
    """Monitor published document links for one explicitly selected facility."""
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.complete or "removed" in self.events:
            raise ValueError("Factsheet is not a complete document register; removals are unsupported")
        self.company_id = config.options.get("company_id", 5447)
        if type(self.company_id) is not int or self.company_id <= 0:
            raise ValueError("company_id must be a positive integer")
        if len(config.urls) > 1:
            raise ValueError("industrial_documents accepts zero or one factsheet URL")
        self.url = config.urls[0] if config.urls else (
            "https://www.norskeutslipp.no/no/Diverse/Virksomhet/?" +
            urlencode({"CompanyID": self.company_id, "ComponentPageID": 180})
        )
        parsed = urlparse(self.url)
        factsheet_q = parse_qs(parsed.query)
        if (parsed.scheme != "https" or parsed.hostname != HOST or
                parsed.path != "/no/Diverse/Virksomhet/" or
                factsheet_q.get("CompanyID") != [str(self.company_id)] or
                factsheet_q.get("ComponentPageID") != ["180"]):
            raise ValueError("industrial_documents requires the official company factsheet URL")
        self.label = (config.label or "Norske utslipp virksomhet").strip()
        self.max_records = integer(config.options.get("max_records", 100), "max_records", 1, 1000)
        self.field_labels = {"document_type":"Dokumenttype", "document_year":"Dokumentår", "document_label":"Dokument", "company_id":"Virksomhets-ID", "official_pdf_url":"Offisiell PDF-lenke", **self.field_labels}

    def read_records(self):
        try: soup = BeautifulSoup(document(self, self.url), "html.parser")
        except Exception as exc:
            if isinstance(exc, SourceError): raise
            raise SourceError("Norske utslipp factsheet could not be read") from exc
        rows=[]; seen=set()
        for link in soup.select("a.pdf[href]"):
            href = public_url(urljoin(self.url, link["href"]))
            p=urlparse(href)
            if p.scheme != "https" or p.hostname != HOST or p.path != PDF_PATH:
                raise SourceError("Norske utslipp PDF link is outside the supported handler")
            q=parse_qs(p.query, keep_blank_values=True)
            required = ("documentID", "documentType", "companyID", "aar", "epslanguage")
            if any(len(q.get(name, [])) != 1 for name in required):
                raise SourceError("Norske utslipp document link has missing or repeated query values")
            if q["companyID"] != [str(self.company_id)]:
                continue
            doc_id=q["documentID"][0]; doc_type=q["documentType"][0]
            year=q["aar"][0]
            if not doc_id.isascii() or not doc_id.isdigit() or int(doc_id) <= 0 or doc_type not in {"T","K"} or not year.isascii() or not year.isdigit():
                raise SourceError("Norske utslipp document link has invalid identity")
            year_number = int(year)
            if year_number and not 1900 <= year_number <= datetime.now().year + 1:
                raise SourceError("Norske utslipp document year is outside supported range")
            key=f"{self.company_id}:{doc_id}"
            if key in seen: raise SourceError("Norske utslipp repeated documentID")
            seen.add(key)
            label=" ".join(link.stripped_strings)
            if not label or len(label)>300: raise SourceError("Norske utslipp document label is invalid")
            rows.append({"key":key,"title":f"{self.label} · {label}","url":href,"published":None,"fields":{"company_id":self.company_id,"document_type":doc_type,"document_year":year_number or None,"document_label":label,"official_pdf_url":href}})
        if not rows: raise SourceError("Norske utslipp factsheet returned no selected documents")
        if len(rows)>self.max_records: raise SourceError("Norske utslipp document window exceeds max_records")
        return rows
