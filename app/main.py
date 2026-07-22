import os
import re
import json
import fitz  # PyMuPDF
import requests
from typing import List, Optional, Dict, Any
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

# --- Pydantic Models for Endpoints ---
class MatchRequest(BaseModel):
    extracted_skills: List[str]
    target_role: str
    location: Optional[str] = None
    work_types: Optional[str] = None
    experience: Optional[str] = None

class SaveRequest(BaseModel):
    job_id: str
    action: str # e.g., "save", "apply"

# --- Helper Functions ---

def fetch_adzuna_jobs(query: str, location: str = "") -> List[Dict]:
    """Queries Adzuna API with keyword query and optional location."""
    app_id = os.getenv("ADZUNA_APP_ID", "your_app_id") # Ensure env vars are set
    app_key = os.getenv("ADZUNA_APP_KEY", "your_app_key")
    
    if not app_id or not app_key:
        print("Warning: Adzuna API keys not found. Returning empty list.")
        return []

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
    except Exception as e:
        print(f"Adzuna API Error: {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        jobs.append({
            "id": str(item.get("id")),
            "title": item.get("title", ""),
            "company": item.get("company", {}).get("display_name", ""),
            "location": item.get("location", {}).get("display_name", ""),
            "salary": float(item.get("salary_min", 0.0)),
            "url": item.get("redirect_url"), # Note: Adzuna only provides redirect URLs natively
            "description": item.get("description", "")
        })
    return jobs

def calculate_job_scores(job: Dict, target_role: str, extracted_skills: List[str], 
                         target_location: Optional[str], target_work_type: Optional[str], 
                         target_experience: Optional[str]) -> Dict:
    """Calculates granular match scores to prevent the 100/100 bug and populate the UI breakdown."""
    title = job.get('title', '').lower()
    desc = job.get('description', '').lower()
    job_loc = job.get('location', '').lower()
    
    # 1. Role Fit: Partial ratio matching user role to job title
    role_fit = fuzz.partial_ratio(target_role.lower(), title)
    
    # 2. Domain / Skills Fit: Percentage of user's skills actually found in the job description
    if extracted_skills:
        matched_skills = [skill for skill in extracted_skills if skill.lower() in desc]
        domain_score = int((len(matched_skills) / len(extracted_skills)) * 100)
    else:
        domain_score = 50 # Default baseline if no skills extracted
        
    # 3. Location Fit
    if target_location:
        location_score = fuzz.partial_ratio(target_location.lower(), job_loc)
    else:
        location_score = 100 # No preference means location is a perfect fit
        
    # 4. Work Type Fit (Heuristic check)
    work_type_score = 80
    if target_work_type:
        wt_lower = target_work_type.lower()
        if wt_lower in desc or wt_lower in title:
            work_type_score = 100
        elif "remote" in desc and "remote" in wt_lower:
            work_type_score = 100
        else:
            work_type_score = 50
            
    # 5. Experience Fit (Heuristic check)
    experience_score = 80
    if target_experience:
        exp_lower = target_experience.lower()
        if "senior" in title and "senior" not in exp_lower:
            experience_score = 40
        elif "junior" in title and "senior" in exp_lower:
            experience_score = 40
            
    # Overall Composite Score (Weighted average)
    overall_score = (role_fit * 0.35) + (domain_score * 0.30) + (location_score * 0.15) + (experience_score * 0.10) + (work_type_score * 0.10)
    
    # Merge new scores into the job dictionary
    job.update({
        "match_score": round(overall_score),
        "matchScore": round(overall_score), # Duplicated for frontend compatibility 
        "role_fit": round(role_fit),
        "domain": round(domain_score),
        "location": round(location_score),
        "work_type": round(work_type_score),
        "experience": round(experience_score)
    })
    
    return job

# --- API Endpoints ---

@app.get("/")
def root():
    return {"status": "JobMatcher API is running"}

@app.post("/upload")
async def upload_resume(resume: UploadFile = File(...)):
    """Extracts text and skills from a PDF upload (Satisfies /upload endpoint)."""
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    extracted_skills = [
        s for s in COMMON_SKILLS 
        if re.search(r'\b' + re.escape(s) + r'\b', text)
    ]
    
    return {"message": "Resume processed successfully", "extracted_skills": extracted_skills}

@app.get("/jobs")
def get_jobs(query: str = "Product Manager", location: str = ""):
    """Fetches raw jobs (Satisfies /jobs endpoint)."""
    jobs = fetch_adzuna_jobs(query=query, location=location)
    return {"total": len(jobs), "jobs": jobs}

@app.post("/match")
def match_jobs(req: MatchRequest):
    """Scores and ranks jobs based on extracted skills (Satisfies /match endpoint)."""
    search_query = f"{req.target_role} " + " ".join(req.extracted_skills[:3])
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=req.location or "")

    if not raw_jobs and req.location:
        raw_jobs = fetch_adzuna_jobs(query=req.target_role, location="")

    ranked_jobs = []
    for job in raw_jobs:
        scored_job = calculate_job_scores(
            job, 
            req.target_role, 
            req.extracted_skills, 
            req.location, 
            req.work_types, 
            req.experience
        )
        ranked_jobs.append(scored_job)

    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)
    return {"total_matches": len(ranked_jobs), "jobs": ranked_jobs}

@app.post("/filter")
def filter_jobs(req: MatchRequest):
    """Placeholder for advanced filtering logic (Satisfies /filter endpoint)."""
    return {"message": "Filter applied", "jobs": []}

@app.post("/save")
def save_results(req: SaveRequest):
    """Placeholder for saving job results to a database (Satisfies /save endpoint)."""
    return {"message": f"Job {req.job_id} saved with action {req.action}"}

# --- Legacy Lovable Endpoint ---
@app.post("/upload-and-search")
async def upload_and_search(
    resume: UploadFile = File(...),
    roles: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    work_types: Optional[str] = Form(None),
    experience: Optional[str] = Form(None)
):
    """Combined upload, match, and return logic for Lovable."""
    # 1. Parse PDF
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    # 2. Extract Skills
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

    # 4. Query API
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=location or "")
    if not raw_jobs and location:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location="")

    # 5. Score and Rank using the new robust logic
    ranked_jobs = []
    for job in raw_jobs:
        scored_job = calculate_job_scores(
            job=job, 
            target_role=target_role, 
            extracted_skills=extracted_skills, 
            target_location=location, 
            target_work_type=work_types, 
            target_experience=experience
        )
        ranked_jobs.append(scored_job)

    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "extracted_skills": extracted_skills,
        "total_matches": len(ranked_jobs),
        "jobs": ranked_jobs
    }
