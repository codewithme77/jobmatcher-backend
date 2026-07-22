import os
import re
import fitz  # PyMuPDF
import requests
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rapidfuzz import fuzz
from supabase import create_client, Client

app = FastAPI(title="JobMatcher MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Schemas ---
class Job(BaseModel):
    id: Optional[str] = None
    title: str
    company: str
    location: str
    salary: Optional[float] = 0.0
    url: str
    description: Optional[str] = ""

class MatchRequest(BaseModel):
    skills: List[str]
    jobs: List[Job]

class FilterRequest(BaseModel):
    jobs: List[Job]
    location: Optional[str] = None
    min_salary: Optional[float] = None
    remote_only: Optional[bool] = False

class SaveRequest(BaseModel):
    email: str = "candidate@example.com"
    skills: List[str]
    experience: int
    matched_jobs: List[dict]


# --- API 1: Health ---
@app.get("/")
def root():
    return {"status": "running"}


# --- API 2: Upload Resume ---
COMMON_SKILLS = [
    # Dev & Tech
    "python", "fastapi", "sql", "postgresql", "docker", "aws", 
    "react", "javascript", "typescript", "node.js", "git", "rest api",
    # Product Management & Business
    "product management", "product strategy", "agile", "scrum", 
    "roadmapping", "jira", "analytics", "user research", "a/b testing",
    "wireframing", "product lifecycle", "kpis", "stakeholder management"
]

@app.post("/upload")
async def upload_resume(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    contents = await file.read()
    doc = fitz.open(stream=contents, filetype="pdf")
    text = "\n".join([page.get_text() for page in doc])
    text_lower = text.lower()

    extracted_skills = [
        skill for skill in COMMON_SKILLS 
        if re.search(r'\b' + re.escape(skill) + r'\b', text_lower)
    ]

    exp_matches = re.findall(r'(\d+)\+?\s*years?', text_lower)
    experience = max([int(x) for x in exp_matches], default=3)

    return {
        "name": "Candidate",
        "skills": list(set(extracted_skills)),
        "experience": experience
    }


# --- API 3: Get Jobs (Dynamic Search Enabled) ---
@app.get("/jobs")
def get_jobs(
    what: Optional[str] = None, 
    where: Optional[str] = None, 
    country: Optional[str] = "in"  # Use 'in' for India, 'us' for USA
):
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        return [
            {
                "id": "1",
                "title": "Principal Product Manager — AI Agent Platform",
                "company": "Gupshup",
                "location": "Mumbai",
                "salary": 120000.0,
                "url": "https://example.com/job/1",
                "description": "Product management, AI agent platform, strategy."
            },
            {
                "id": "2",
                "title": "Senior Product Manager",
                "company": "Tech Corp",
                "location": "Remote",
                "salary": 110000.0,
                "url": "https://example.com/job/2",
                "description": "Product roadmaps, agile development, user analytics."
            }
        ]

    # Target country API endpoint dynamically
    target_country = country.lower() if country else "in"
    url = f"https://api.adzuna.com/v1/api/jobs/{target_country}/search/1"
    
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json"
    }
    
    if what:
        params["what"] = what
    if where:
        params["where"] = where

    res = requests.get(url, params=params)
    if res.status_code != 200:
        return []

    data = res.json()
    jobs = []
    for item in data.get("results", []):
        jobs.append({
            "id": str(item.get("id")),
            "title": item.get("title", ""),
            "company": item.get("company", {}).get("display_name", ""),
            "location": item.get("location", {}).get("display_name", ""),
            "salary": float(item.get("salary_min", 0.0)),
            "url": item.get("redirect_url"),  # Direct application link
            "description": item.get("description", "")
        })
    return jobs


# --- API 4: Match Jobs (Preserves Full Job Metadata) ---
@app.post("/match")
def match_jobs(payload: MatchRequest):
    skills_str = " ".join(payload.skills)
    results = []

    for job in payload.jobs:
        target_text = f"{job.title} {job.description}"
        score = fuzz.token_set_ratio(skills_str, target_text)
        
        # Return complete job dictionary so UI card fields don't disappear
        job_dict = job.dict()
        job_dict["score"] = round(score, 1)
        results.append(job_dict)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# --- API 5: Filter Jobs ---
@app.post("/filter")
def filter_jobs(payload: FilterRequest):
    filtered = payload.jobs

    if payload.location:
        filtered = [j for j in filtered if payload.location.lower() in j.location.lower()]
    if payload.min_salary:
        filtered = [j for j in filtered if (j.salary or 0) >= payload.min_salary]
    if payload.remote_only:
        filtered = [j for j in filtered if "remote" in j.location.lower()]

    return filtered


# --- API 6: Save Results ---
@app.post("/save")
def save_results(payload: SaveRequest):
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        return {"status": "skipped", "message": "Supabase credentials not configured."}

    try:
        supabase: Client = create_client(url, key)
        supabase.table("resumes").insert({
            "email": payload.email,
            "skills": payload.skills,
            "experience": payload.experience
        }).execute()
        return {"status": "success", "message": "Saved to Supabase"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
