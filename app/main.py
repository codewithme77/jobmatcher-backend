import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, engine, get_db
from app.crud import create_resume, create_search, create_user, get_resumes_for_user, get_searches_for_user
from app.job_sources import JobAggregator
from app.models import Candidate, Job
from app.schemas import (CandidateCreate, CandidateRead, JobCreate, JobRead,
                         MatchResponse, MatchResult, ResumeUploadResponse)
from app.semantic_matching import MatchScorer
from app.services import build_matches

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
scorer = MatchScorer()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/jobs/", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> Job:
    job = Job(**payload.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@app.post("/api/v1/candidates/", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
def create_candidate(payload: CandidateCreate, db: Session = Depends(get_db)) -> Candidate:
    existing_candidate = db.query(Candidate).filter(Candidate.email == str(payload.email)).first()
    if existing_candidate is not None:
        return existing_candidate

    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


@app.get("/api/v1/jobs/sources", response_model=list[dict])
def list_source_jobs() -> list[dict]:
    aggregator = JobAggregator()
    jobs = aggregator.fetch_all_jobs()
    return [
        {
            "source": job.source,
            "external_id": job.external_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
            "url": job.url,
            "remote": job.remote,
        }
        for job in jobs
    ]


@app.post("/api/v1/users")
def create_user_endpoint(email: str, full_name: str | None = None, db: Session = Depends(get_db)) -> dict:
    user = create_user(db=db, email=email, full_name=full_name)
    return {"id": str(user.id), "email": user.email, "full_name": user.full_name}


@app.post("/api/v1/resumes")
def create_resume_endpoint(user_id: str, file_name: str, storage_path: str, extracted_text: str | None = None, db: Session = Depends(get_db)) -> dict:
    resume = create_resume(db=db, user_id=user_id, file_name=file_name, storage_path=storage_path, extracted_text=extracted_text)
    return {"id": str(resume.id), "user_id": str(resume.user_id), "file_name": resume.file_name}


@app.post("/api/v1/searches")
def create_search_endpoint(user_id: str, query: str, location: str | None = None, db: Session = Depends(get_db)) -> dict:
    search = create_search(db=db, user_id=user_id, query=query, location=location)
    return {"id": str(search.id), "user_id": str(search.user_id), "query": search.query, "location": search.location}


@app.get("/api/v1/users/{user_id}/resumes")
def list_user_resumes(user_id: str, db: Session = Depends(get_db)) -> list[dict]:
    resumes = get_resumes_for_user(db=db, user_id=user_id)
    return [{"id": str(item.id), "file_name": item.file_name, "storage_path": item.storage_path} for item in resumes]


@app.get("/api/v1/users/{user_id}/searches")
def list_user_searches(user_id: str, db: Session = Depends(get_db)) -> list[dict]:
    searches = get_searches_for_user(db=db, user_id=user_id)
    return [{"id": str(item.id), "query": item.query, "location": item.location} for item in searches]


@app.post("/api/v1/semantic-match")
def semantic_match(
    resume_text: str,
    job_description: str,
    required_skills: list[str],
    years_experience: int,
    location: str,
    resume_location: str,
) -> dict[str, float]:
    score = scorer.score_job(
        resume_text=resume_text,
        job_description=job_description,
        required_skills=required_skills,
        years_experience=years_experience,
        location=location,
        resume_location=resume_location,
    )
    return {"score": score}


@app.get("/api/v1/matches/{job_id}", response_model=MatchResponse)
def get_matches(job_id: int, db: Session = Depends(get_db)) -> MatchResponse:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    matches = build_matches(db, job_id)
    results = [
        MatchResult(
            candidate_id=match.candidate_id,
            candidate_name=db.query(Candidate).filter(Candidate.id == match.candidate_id).first().full_name,
            score=match.score,
        )
        for match in matches
    ]
    return MatchResponse(job_id=job.id, results=results)


@app.post("/upload-resume", response_model=ResumeUploadResponse)
async def upload_resume(file: UploadFile = File(...)) -> ResumeUploadResponse:
    if file.filename is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    if file.size and file.size > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large. Maximum size is 5MB")

    extension = Path(file.filename).suffix.lower()
    allowed_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    if extension not in allowed_types:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")

    if file.content_type and file.content_type not in {allowed_types[extension], "application/octet-stream"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large. Maximum size is 5MB")

    try:
        if file.content_type == "application/pdf":
            text = extract_pdf_text(contents)
        else:
            text = extract_docx_text(contents)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unable to parse file: {exc}") from exc

    return ResumeUploadResponse(filename=file.filename, extracted_text=text)


def extract_pdf_text(contents: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is not installed") from exc

    with pdfplumber.open(io.BytesIO(contents)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(page for page in pages if page).strip()


def extract_docx_text(contents: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        return extract_docx_text_fallback(contents)

    document = Document(io.BytesIO(contents))
    return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text).strip()


def extract_docx_text_fallback(contents: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs).strip()
