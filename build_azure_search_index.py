import base64
import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from pypdf import PdfReader


SERVICE_NAME = os.getenv("AZURE_SEARCH_SERVICE_NAME", "YOUR-SEARCH-SERVICE")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "YOUR-RESOURCE-GROUP")
INDEX_NAME = "imac-guidance-poc"
API_VERSION = "2023-11-01"
ENDPOINT = f"https://{SERVICE_NAME}.search.windows.net"


WEB_SOURCES = [
    {
        "url": "https://www.tewhatuora.govt.nz/for-health-professionals/clinical-guidance/immunisation-handbook",
        "title": "Health NZ Immunisation Handbook",
        "source_file": "Health NZ Immunisation Handbook page",
        "source_type": "official_guidance",
    },
    {
        "url": "https://static.info.content.health.nz/docs/health-pros/topics/immunisations/immunisation-handbook-2026-v2.pdf",
        "title": "Immunisation Handbook 2026 v2 PDF",
        "source_file": "immunisation-handbook-2026-v2.pdf",
        "source_type": "official_guidance_pdf",
    },
    {
        "url": "https://www.healthnz.govt.nz/health-professionals/guidance-standards/topic/immunisation/vaccine-safety-information",
        "title": "Health NZ Vaccine safety information",
        "source_file": "Health NZ vaccine safety information",
        "source_type": "official_guidance",
    },
    {
        "url": "https://www.immune.org.nz/",
        "title": "IMAC website",
        "source_file": "IMAC website",
        "source_type": "imac_public_guidance",
    },
    {
        "url": "https://www.pharmac.govt.nz/medicine-funding-and-supply/what-you-need-to-know-about-medicines/vaccines",
        "title": "Pharmac vaccines role",
        "source_file": "Pharmac vaccines role",
        "source_type": "funding_information",
    },
    {
        "url": "https://www.medsafe.govt.nz/Regulatory/flu.asp",
        "title": "Medsafe influenza vaccine composition",
        "source_file": "Medsafe influenza vaccine composition",
        "source_type": "regulatory_information",
    },
]


LOCAL_SOURCES = [
    {
        "path": Path(r"C:\Users\惠普\Desktop\evaluation_cases.txt"),
        "title": "Anonymised evaluation cases",
        "source_file": "evaluation_cases.txt",
        "source_type": "anonymised_call_question_set",
    },
    {
        "path": Path(r"C:\Users\惠普\Downloads\IMAC_Hackathon_Brief_Immunisation_Adviser_Agent (2).pdf"),
        "title": "IMAC Hackathon Brief",
        "source_file": "IMAC_Hackathon_Brief_Immunisation_Adviser_Agent (2).pdf",
        "source_type": "project_brief",
    },
    {
        "path": Path(r"C:\Users\惠普\Downloads\Immunisation_Guidelines_Adviser_Agent (2).pdf"),
        "title": "Immunisation Guidelines Adviser Agent use case",
        "source_file": "Immunisation_Guidelines_Adviser_Agent (2).pdf",
        "source_type": "project_use_case",
    },
]


def auth_header():
    token = os.getenv("AZURE_SEARCH_BEARER_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    if os.getenv("AZURE_SEARCH_ADMIN_KEY"):
        return {"api-key": os.getenv("AZURE_SEARCH_ADMIN_KEY").strip()}
    cmd = [
        "az", "search", "admin-key", "show",
        "--service-name", SERVICE_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--query", "primaryKey",
        "-o", "tsv",
    ]
    return {"api-key": subprocess.check_output(cmd, text=True).strip()}


def search_request(method, path, auth, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{ENDPOINT}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **auth},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def create_index(auth):
    schema = {
        "name": INDEX_NAME,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "source_file", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
            {"name": "source_type", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "url", "type": "Edm.String", "filterable": True},
            {"name": "chunk_id", "type": "Edm.Int32", "filterable": True, "sortable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
        ],
        "semantic": {
            "configurations": [{
                "name": "default",
                "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [{"fieldName": "source_file"}],
                },
            }]
        },
    }
    search_request("PUT", f"/indexes/{INDEX_NAME}?api-version={API_VERSION}", auth, schema)


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "IMAC-Adviser-PoC/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read(), resp.headers.get("content-type", "")


def html_to_text(raw):
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def pdf_to_text_bytes(raw):
    import io

    reader = PdfReader(io.BytesIO(raw))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
        if text:
            parts.append(f"Page {i}: {text}")
    return "\n".join(parts)


def local_pdf_to_text(path):
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
        if text:
            parts.append(f"Page {i}: {text}")
    return "\n".join(parts)


def chunk_text(text, size=2800, overlap=280):
    text = re.sub(r"\s+", " ", text or "").strip()
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield text[start:end]
        if end == len(text):
            break
        start = max(0, end - overlap)


def safe_id(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:900]


def make_doc(source, text, chunk_id, chunk):
    raw_id = base64.urlsafe_b64encode(f"{source['source_file']}#{chunk_id}".encode()).decode().rstrip("=")
    return {
        "@search.action": "upload",
        "id": safe_id(raw_id),
        "title": source["title"],
        "source_file": source["source_file"],
        "source_type": source["source_type"],
        "url": source.get("url", ""),
        "chunk_id": chunk_id,
        "content": chunk,
    }


def build_documents():
    docs = []
    for source in WEB_SOURCES:
        raw, content_type = fetch_url(source["url"])
        is_pdf = source["url"].lower().endswith(".pdf") or "pdf" in content_type.lower()
        text = pdf_to_text_bytes(raw) if is_pdf else html_to_text(raw)
        for chunk_id, chunk in enumerate(chunk_text(text), start=1):
            docs.append(make_doc(source, text, chunk_id, chunk))

    for source in LOCAL_SOURCES:
        path = source["path"]
        if not path.exists():
            continue
        if path.suffix.lower() == ".pdf":
            text = local_pdf_to_text(path)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        for chunk_id, chunk in enumerate(chunk_text(text), start=1):
            docs.append(make_doc(source, text, chunk_id, chunk))
    return docs


def upload_documents(auth, docs):
    for i in range(0, len(docs), 100):
        search_request(
            "POST",
            f"/indexes/{INDEX_NAME}/docs/index?api-version={API_VERSION}",
            auth,
            {"value": docs[i:i + 100]},
        )


def main():
    auth = auth_header()
    create_index(auth)
    docs = build_documents()
    upload_documents(auth, docs)
    print(json.dumps({"service": SERVICE_NAME, "index": INDEX_NAME, "documents": len(docs)}, indent=2))


if __name__ == "__main__":
    main()
