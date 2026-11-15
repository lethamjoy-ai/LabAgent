"""Catalogue and isolated synthetic appointment agent tools."""
import logging
import os
import secrets
from uuid import uuid4
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from app.bootstrap import build_labrag_client
from app.agents.voice_session import PERSONAL_COMMAND
from app.integrations.lab_question import lab_question, LabQuestionPrivacyError
from app.integrations.elevenlabs_voice import spoken_excerpt
from app.integrations.labrag_client import LabRAGError, Citation
from app.appointments.voice_demo import ReferenceRequest, CreateRequest, OverviewRequest, configured

logger = logging.getLogger("labassist.lookup")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


class LookupQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    question: str = Field(min_length=1,max_length=3000)

    @field_validator("question")
    @classmethod
    def nonempty(cls,value):
        if not value.strip():
            raise ValueError("Empty question")
        return value.strip()


class LookupReply(BaseModel):
    answer: str
    evidence_status: Literal["answered","needs_clarification","insufficient_evidence"]
    sources: list[Citation]


def create_app(labrag=None, secret=None, voice_appointments=None):
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    api = FastAPI(title="LabAssist demo tools")
    api.state.labrag = labrag or build_labrag_client()
    api.state.lookup_completed = 0
    configured_secret = secret if secret is not None else os.getenv("LABASSIST_WEBHOOK_SECRET","")

    async def authenticate(x_labassist_webhook_secret: str | None = Header(default=None)):
        if len(configured_secret) < 32:
            raise HTTPException(503,"Webhook authentication is not configured.")
        supplied = x_labassist_webhook_secret or ""
        if not secrets.compare_digest(supplied.encode(),configured_secret.encode()):
            raise HTTPException(401,"Unauthorized.")

    @api.middleware("http")
    async def bound_tool_request(request,call_next):
        if request.url.path in {"/api/v1/tools/lookup_lab_test", "/api/v1/tools/get_appointment_details", "/api/v1/tools/create_appointment", "/api/v1/tools/get_daily_overview"} and request.method=="POST":
            try:
                await authenticate(request.headers.get("x-labassist-webhook-secret"))
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code,content={"detail":exc.detail})
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>16384:
                    return JSONResponse(status_code=413,content={"detail":"Request too large."})
            request._body=bytes(data)
        return await call_next(request)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Never echo input values, headers, or provider errors in validation.
        if request.url.path.endswith("/get_daily_overview"):
            detail = "day is required and must be today or tomorrow. No other fields are accepted."
        elif request.url.path.endswith("/get_appointment_details"):
            detail = "booking_reference is required: a 1-80 character string containing only letters, digits, hyphens or underscores. No other fields are accepted."
        elif request.url.path.endswith("/create_appointment"):
            detail = "Offer requires action=offer, test_id, date (YYYY-MM-DD), time (HH:MM). Confirm requires action=confirm, confirmation_token and confirmed=true only. Invalid dates and unexpected fields are rejected."
        else:
            detail = "Expected only a nonempty question string of at most 3000 characters."
        return JSONResponse(status_code=422,content={"detail":detail})

    @api.get("/health")
    def health():
        return {"status":"ok"}

    @api.post("/api/v1/tools/lookup_lab_test",dependencies=[Depends(authenticate)],response_model=LookupReply)
    async def lookup_lab_test(body: LookupQuestion):
        if PERSONAL_COMMAND.search(body.question):
            raise HTTPException(422,"Only general laboratory catalogue questions are supported; use text chat for patient or appointment actions.")
        try:
            question = lab_question(body.question)
        except LabQuestionPrivacyError:
            raise HTTPException(422,"Remove patient names, dates of birth and identifiers; synthetic general catalogue questions only.") from None
        call_id = uuid4().hex
        try:
            result = await run_in_threadpool(api.state.labrag.query,None,question)
        except LabRAGError as exc:
            code = 504 if exc.code in ("client_timeout","http_504") else 503 if exc.http_status==503 else 422 if exc.http_status==422 else 502
            logger.info("lookup_lab_test failed call_id=%s http_status=%s",call_id,code)
            raise HTTPException(code,"Laboratory evidence could not be retrieved. Do not infer an answer.") from None
        except Exception:
            logger.info("lookup_lab_test failed call_id=%s http_status=502",call_id)
            raise HTTPException(502,"Laboratory evidence could not be retrieved. Do not infer an answer.") from None
        answer = spoken_excerpt(result.answer)
        # Live sessions do not copy a full transcript into Streamlit chat.
        if answer.startswith("The full laboratory answer and sources are available in chat."):
            answer = "The retrieved instructions are too detailed to summarize safely. Consult the returned sources or ask laboratory staff."
        else:
            answer = answer.replace("Read the full answer and sources in chat.","Consult the returned sources for the complete instructions.")
        if result.status=="answered" and not result.citations:
            answer = "The API supplied no sources; verify with the laboratory. " + answer
        api.state.lookup_completed += 1
        # Only opaque IDs and a bounded status enum are logged, never questions.
        logger.info("lookup_lab_test completed call_id=%s evidence_status=%s",call_id,result.status)
        return {"answer":answer,"evidence_status":result.status,
                "sources":[citation.model_dump() for citation in result.citations]}

    def appointments():
        try:
            return voice_appointments if voice_appointments is not None else configured(configured_secret)
        except Exception:
            raise HTTPException(503,"Synthetic voice appointments are not configured. Use text chat.") from None

    def appointment_call(operation):
        try:
            return operation(appointments())
        except PermissionError:
            raise HTTPException(503,"Synthetic voice authorization expired; contact the demo administrator.") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503,"Synthetic appointment service unavailable. Do not announce a booking; retry confirmation with the same token.") from None

    @api.post("/api/v1/tools/get_appointment_details",dependencies=[Depends(authenticate)])
    def get_appointment_details(body: ReferenceRequest):
        return appointment_call(lambda service: service.lookup(body.booking_reference))

    @api.post("/api/v1/tools/create_appointment",dependencies=[Depends(authenticate)])
    def create_appointment(body: CreateRequest):
        return appointment_call(lambda service: service.create(body))

    @api.post("/api/v1/tools/get_daily_overview",dependencies=[Depends(authenticate)])
    def get_daily_overview(body: OverviewRequest):
        return appointment_call(lambda service: service.overview(body.day))

    return api


app = create_app()
