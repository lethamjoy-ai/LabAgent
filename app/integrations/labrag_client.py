"""Local LabRAG API boundary. Retrieval and model credentials stay in LabRAG."""
from typing import Literal
from uuid import UUID
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CATALOG = {"CBC":"Complete blood count", "GLU":"Glucose", "A1C":"Hemoglobin A1c",
           "BUN":"Blood urea nitrogen", "CALCIUM":"Calcium", "C-PEPTIDE":"C-Peptide"}


class Citation(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    source_id: str | None = None
    document_id: str | None = None
    document_title: str | None = None
    provider: str | None = None
    test_code: str | None = None
    test_name: str | None = None
    printed_catalog_page: str | None = None
    pdf_page_1_based: str | None = None
    original_pdf_page_1_based: str | None = None
    section: str | None = None
    chunk_id: str | None = None
    source_url: str | None = None
    citation_role: str | None = None

    def display(self, index):
        fields = [("Provider",self.provider),("Test",self.test_name or self.test_code),
            ("Printed catalog page",self.printed_catalog_page),("PDF page (1-based)",self.pdf_page_1_based),
            ("Original PDF page (1-based)",self.original_pdf_page_1_based),("Section",self.section),
            ("URL",self.source_url)]
        return f"[{index}] {self.document_title or 'Source reference'}" + ''.join(f"; {k}: {v}" for k,v in fields if v)


class MatchedTest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    test_code: str | None = None
    test_name: str | None = None


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    status: Literal["answered","needs_clarification","insufficient_evidence"]
    answer: str
    citations: list[Citation]
    matched_tests: list[MatchedTest]
    request_id: str

    def display(self):
        prefix = "LabRAG needs clarification.\n" if self.status == "needs_clarification" else (
            "LabRAG found insufficient evidence in its indexed references.\n" if self.status == "insufficient_evidence" else "")
        text = prefix + (self.answer.strip() or "Please provide more laboratory test details.")
        if self.matched_tests:
            text += "\nMatched tests: " + "; ".join(t.test_name or t.test_code or "Unspecified test" for t in self.matched_tests)
        if self.citations:
            text += "\n\nSources:\n" + "\n".join(c.display(i) for i,c in enumerate(self.citations,1))
        elif self.status == "answered":
            text += "\nSources: Missing — the API returned no citations; verify this answer with the laboratory."
        return text


class LabRAGError(Exception):
    def __init__(self, code, message, http_status=None, request_id=None, api_error=None):
        super().__init__(message)
        self.code, self.http_status = code, http_status
        self.request_id, self.api_error = request_id, api_error


class HttpLabRAGClient:
    def __init__(self, base_url="http://127.0.0.1:8000", timeout=90, transport=None):
        self.base_url = base_url.rstrip('/')
        self.timeout, self.transport = float(timeout), transport
        if self.timeout <= 0:
            raise ValueError("LABRAG_TIMEOUT_SECONDS must be positive.")

    def query(self, test_id, question):
        # test context has already been included in the safe question by the tool.
        if not isinstance(question,str) or not 1 <= len(question.strip()) <= 3000:
            raise LabRAGError("invalid_question","Enter a laboratory question between 1 and 3000 characters.")
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout,connect=min(5,self.timeout)),transport=self.transport,follow_redirects=False) as client:
                response = client.post(self.base_url+'/api/v1/query',json={"question":question})
        except httpx.TimeoutException as exc:
            raise LabRAGError("client_timeout","Lab Agent timed out waiting for LabRAG. No automatic retry was made; the API may still be processing the request.") from exc
        except httpx.ConnectError as exc:
            raise LabRAGError("connection_failed","Cannot connect to LabRAG. Start its API on port 8000 and try again explicitly.") from exc
        except httpx.RequestError as exc:
            raise LabRAGError("transport_error","Communication with LabRAG failed. No automatic retry was made.") from exc
        errors = {422:("http_422","LabRAG rejected the query (HTTP 422). Check the laboratory question."),
            503:("http_503","LabRAG returned HTTP 503 and could not complete the query. No laboratory answer was returned. Check LabRAG's server diagnostics before retrying."),
            504:("http_504","LabRAG reported an upstream timeout (HTTP 504). No automatic retry was made.")}
        if response.status_code != 200:
            code,message = errors.get(response.status_code,("http_error",f"LabRAG returned HTTP {response.status_code}. No answer was accepted."))
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body,dict):
                body = {}
            request_id = None
            for value in (body.get("request_id"),response.headers.get("X-Request-ID")):
                try:
                    if isinstance(value,str) and len(value)<=128:
                        request_id = str(UUID(value))
                        break
                except ValueError:
                    pass
            # Keep only the local API's known categorical tags. Do not expose raw
            # provider messages, error bodies, credentials, or echoed questions.
            tag = body.get("error")
            api_error = tag if isinstance(tag,str) and tag in {"invalid_request","upstream_timeout","provider_rate_limited","service_unavailable"} else None
            if response.status_code == 503 and api_error == "provider_rate_limited":
                message = "LabRAG reports its model provider is rate-limited (HTTP 503). No laboratory answer was returned. Wait for the provider limit to reset before explicitly retrying."
            raise LabRAGError(code,message,response.status_code,request_id,api_error)
        try:
            result = QueryResponse.model_validate(response.json())
            if result.status == "answered" and not result.answer.strip():
                raise ValueError("Empty answered response")
            return result
        except (ValidationError,ValueError) as exc:
            raise LabRAGError("malformed_response","LabRAG returned a malformed response. No answer could be verified.") from exc


# Explicit offline fixtures retained for tests only; bootstrap never selects them.
class Source(BaseModel):
    reference: str
    page: str | None = None


class TestInformation(BaseModel):
    test_id: str
    test_name: str
    answer: str
    specimen: str | None = None
    container: str | None = None
    preparation: str | None = None
    sources: list[Source] = Field(default_factory=list)
    mock: bool = False

    def display(self):
        return ('SYNTHETIC MOCK — not clinical instructions\n' if self.mock else '') + self.test_name + '\n' + self.answer + '\n' + '\n'.join(
            f"{k}: {v or 'Not provided by LabRAG; verify with the laboratory.'}" for k,v in [('Specimen',self.specimen),('Container',self.container),('Preparation',self.preparation)]) + '\nSources: ' + ('; '.join(s.reference for s in self.sources) or 'Missing — this answer is unverified.')


class MockLabRAGClient:
    def query(self,test_id,question):
        # Offline fixture resolver only. Runtime knowledge queries have no catalog gate.
        if test_id not in CATALOG:
            import re
            matches=[key for key,name in CATALOG.items() if re.search(r'\b'+re.escape(key)+r'\b',question,re.I) or name.lower() in question.lower()]
            if not matches:
                return QueryResponse(status="needs_clarification",answer="Which test? Please specify its exact catalog name.",citations=[],matched_tests=[],request_id="offline-fixture")
            test_id=matches[-1]
        return TestInformation(test_id=test_id,test_name=CATALOG[test_id],answer="Synthetic course fixture only. No live retrieval was performed.",
            specimen="Demo specimen",container="Demo container",sources=[Source(reference="Synthetic LabAssist fixture, v1")],mock=True)
