import os
import re
import json
import fitz  # PyMuPDF
import requests
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rapidfuzz import fuzz

app = FastAPI(title="JobMatcher MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COMMON_SKILLS = [
    "python", "fastapi", "sql", "postgresql", "docker", "aws", 
    "react", "javascript", "typescript", "node.js", "git", "rest api",
    "product management", "product strategy", "agile", "scrum", 
    "roadmapping", "jira", "analytics", "user research", "a/b testing",
    "wireframing", "product lifecycle", "kpis", "stakeholder management"
]

def fetch_adzuna_jobs(query: str, location: str = ""):
    """Queries Adzuna API with keyword query and optional location."""
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []

    # Defaulting to India ('in') endpoint. Change 'in' to 'us' if needed.
    url = f"https://api.adzuna.com/v1/api/jobs/in/search/1"
    
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json",
        "what": query
    }
    if location:
        params["where"] = location

    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception:
        return []

    jobs = []
    for item in data.get("results", []):
        jobs.append({
            "id": str(item.get("id")),
            "title": item.get("title", ""),
            "company": item.get("company", {}).get("display_name", ""),
            "location": item.get("location", {}).get("display_name", ""),
            "salary": float(item.get("salary_min", 0.0)),
            "url": item.get("redirect_url"),
            "description": item.get("description", "")
        })
    return jobs

@app.get("/")
def root():
    return {"status": "running"}

# --- Exact endpoint required by Lovable ---
@app.post("/upload-and-search")
async def upload_and_search(
    resume: UploadFile = File(...),
    roles: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    work_types: Optional[str] = Form(None),
    experience: Optional[str] = Form(None)
):
    # 1. Parse PDF text
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    # 2. Extract Skills from PDF
    extracted_skills = [
        s for s in COMMON_SKILLS 
        if re.search(r'\b' + re.escape(s) + r'\b', text)
    ]

    # 3. Determine Search Query
    target_role = "Product Manager"
    if roles:
        try:
            parsed_roles = json.loads(roles)
            if isinstance(parsed_roles, list) and len(parsed_roles) > 0:
                target_role = parsed_roles[0]
        except Exception:
            target_role = roles

    search_query = f"{target_role} " + " ".join(extracted_skills[:3])

    # 4. Query Adzuna API
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=location or "")

    # Fallback if no specific location results are returned
    if not raw_jobs and location:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location="")

    # 5. Score and Rank Jobs
    skills_str = " ".join(extracted_skills) if extracted_skills else target_role
    ranked_jobs = []

    for job in raw_jobs:
        target_text = f"{job['title']} {job['description']}"
        score = fuzz.token_set_ratio(skills_str, target_text)
        
        # Format keys exactly as Lovable expects
        job["match_score"] = round(score, 1)
        job["matchScore"] = round(score, 1)
        ranked_jobs.append(job)

    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)

    # 6. Return response to Lovable
    return {
        "extracted_skills": extracted_skills,
        "total_matches": len(ranked_jobs),
        "jobs": ranked_jobs
    }
