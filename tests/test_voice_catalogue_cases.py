"""User-reported catalogue cases: transport tests, not evidence/clinical validation."""
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.integrations.labrag_client import QueryResponse, Citation

QUESTIONS = [
    "What specimen is required for BUN?",
    "How long can BUN serum be refrigerated or frozen?",
    "What specimen and volume are required for Bone Alkaline Phosphatase, Serum?",
    "How should a patient prepare for C-Peptide, Serum?",
    "What specimen and minimum volume are required for CRP serum?",
    "What is the minimum specimen volume for Calcium, Total, Serum?",
    "What collection instructions apply to Calcium, Ionized, Serum?",
    "How should a calprotectin fecal specimen be collected and stored?",
    "What specimen types are accepted for Bordetella pertussis and parapertussis PCR?",
    "Can Bone Marrow Aspirate (Bill Only) be ordered as a laboratory test?",
    "What is the difference between Calcium, Total, Serum and Calcium, Ionized, Serum collection requirements?",
]


@pytest.mark.parametrize("question",QUESTIONS)
@pytest.mark.parametrize("status",["answered","needs_clarification","insufficient_evidence"])
def test_reported_questions_reach_labrag_unchanged(question,status):
    client=Mock()
    citation=Citation(document_title="Offline fixture",printed_catalog_page="7",pdf_page_1_based="9",
                      section="Specimen",source_url="https://example.test/fixture.pdf")
    client.query.return_value=QueryResponse(status=status,answer="Offline response fixture.",
        citations=[citation],matched_tests=[],request_id="offline-fixture")
    secret="offline-catalogue-secret-"*3
    api=TestClient(create_app(labrag=client,secret=secret))
    result=api.post("/api/v1/tools/lookup_lab_test",json={"question":question},
                    headers={"X-LabAssist-Webhook-Secret":secret})
    assert result.status_code==200
    client.query.assert_called_once_with(None,question)
    body=result.json()
    assert body["evidence_status"]==status
    assert body["answer"]=="Offline response fixture."
    assert body["sources"][0]["section"]=="Specimen"
    assert body["sources"][0]["printed_catalog_page"]=="7"
    assert body["sources"][0]["pdf_page_1_based"]=="9"
