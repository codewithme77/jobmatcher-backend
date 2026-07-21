import io
import zipfile
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_matching_endpoint_returns_scores_for_a_job():
    job_response = client.post(
        "/api/v1/jobs/",
        json={
            "title": "Backend Python Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Build APIs with Python",
            "required_skills": ["python", "fastapi", "sqlalchemy"],
        },
    )
    assert job_response.status_code == 201
    job = job_response.json()

    candidate_response = client.post(
        "/api/v1/candidates/",
        json={
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "desired_title": "Backend Engineer",
            "years_experience": 6,
            "skills": ["python", "fastapi", "sqlalchemy"],
        },
    )
    assert candidate_response.status_code == 201
    candidate = candidate_response.json()

    match_response = client.get(f"/api/v1/matches/{job['id']}")
    assert match_response.status_code == 200
    payload = match_response.json()
    assert payload["job_id"] == job["id"]
    assert any(item["candidate_id"] == candidate["id"] for item in payload["results"])


def test_upload_resume_accepts_docx_file():
    content_types = {
        "word/document.xml": "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>Senior Python Engineer</w:t></w:r></w:p><w:p><w:r><w:t>Experience with FastAPI and SQLAlchemy</w:t></w:r></w:p></w:body></w:document>"
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in content_types.items():
            archive.writestr(name, text)
    buffer.seek(0)

    response = client.post(
        "/upload-resume",
        files={"file": ("resume.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "resume.docx"
    assert "Senior Python Engineer" in payload["extracted_text"]


def test_upload_resume_rejects_oversized_files():
    response = client.post(
        "/upload-resume",
        files={"file": ("large.pdf", b"x" * (6 * 1024 * 1024), "application/pdf")},
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()
