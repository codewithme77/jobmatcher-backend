import os
import re
import json
import fitz  # PyMuPDF
import requests
import concurrent.futures
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

# Expanded skills list for better extraction
COMMON_SKILLS = [
    "python", "fastapi", "sql", "postgresql", "docker", "aws", "gcp", "azure",
    "react", "javascript", "typescript", "node.js", "git", "rest api", "graphql",
    "product management", "product strategy", "agile", "scrum", "kanban",
    "roadmapping", "jira", "analytics", "user research", "a/b testing",
    "wireframing", "product lifecycle", "kpis", "stakeholder management",
    "java", "c++", "c#", "ruby", "php", "go", "rust", "swift", "kotlin",
    "machine learning", "data science", "ai", "nlp", "ci/cd", "kubernetes",
    "html", "css", "tailwind", "figma", "ui/ux", "devops", "marketing", "sales",
    "tableau", "power bi", "excel", "nosql", "mongodb", "redis"
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
    action: str 

# --- Helper Functions ---

def parse_form_field(field_data: Optional[str]) -> str:
    """Safely extracts form data whether it's plain text or a JSON stringified array."""
    if not field_data:
        return ""
    try:
        parsed = json.loads(field_data)
        if isinstance(parsed, list) and len(parsed) > 0:
            return str(parsed[0]) # Extract first selected filter
        elif isinstance(parsed, list):
            return ""
        return str(parsed)
    except Exception:
        return field_data

def fetch_adzuna_jobs(query: str, location: str = "") -> List[Dict]:
    """Queries Adzuna API with keyword query and optional location."""
    app_id = os.getenv("ADZUNA_APP_ID", "your_app_id") 
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
            "url": item.get("redirect_url"), 
            "description": item.get("description", "")
        })
    return jobs

def resolve_job_url(job: Dict) -> Dict:
    """Follows the Adzuna redirect link to find the direct job board application URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        # Follow the redirect to get the final destination
        res = requests.get(job["url"], headers=headers, allow_redirects=True, timeout=5)
        job["url"] = res.url
    except Exception:
        pass # Fallback to Adzuna link if timeout or error occurs
    return job

def calculate_job_scores(job: Dict, target_role: str, extracted_skills: List[str], 
                         target_location: str, target_work_type: str, 
                         target_experience: str) -> Dict:
    """Calculates granular match scores with exact camelCase keys for Lovable UI."""
    title = job.get('title', '').lower()
    desc = job.get('description', '').lower()
    job_loc = job.get('location', '').lower()
    
    # 1. Role Score
    role_score = fuzz.partial_ratio(target_role.lower(), title)
    if not target_role: role_score = 50
    
    # 2. Skill Score 
    if extracted_skills:
        matched_skills = [skill for skill in extracted_skills if skill.lower() in desc]
        skill_score = int((len(matched_skills) / len(extracted_skills)) * 100)
    else:
        skill_score = 50 
        
    # 3. Location Score
    if target_location:
        location_score = fuzz.partial_ratio(target_location.lower(), job_loc)
    else:
        location_score = 100 
        
    # 4. Work Type Score
    work_type_score = 80
    if target_work_type:
        wt_lower = target_work_type.lower()
        if wt_lower in desc or wt_lower in title:
            work_type_score = 100
        elif "remote" in desc and "remote" in wt_lower:
            work_type_score = 100
        else:
            work_type_score = 50
            
    # 5. Experience Score
    experience_score = 80
    if target_experience:
        exp_lower = target_experience.lower()
        if "senior" in title and "senior" not in exp_lower:
            experience_score = 40
        elif "junior" in title and "senior" in exp_lower:
            experience_score = 40
            
    # Overall Composite Score 
    overall_score = (role_score * 0.35) + (skill_score * 0.30) + (location_score * 0.15) + (experience_score * 0.10) + (work_type_score * 0.10)
    
    # Merging exact camelCase keys expected by frontend UI
    job.update({
        "matchScore": round(overall_score), 
        "roleScore": round(role_score),
        "skillScore": round(skill_score),
        "locationScore": round(location_score),
        "workTypeScore": round(work_type_score),
        "experienceScore": round(experience_score)
    })
    
    return job

# --- API Endpoints ---

@app.get("/")
def root():
    return {"status": "JobMatcher API is running"}

@app.post("/upload-and-search")
async def upload_and_search(
    resume: UploadFile = File(...),
    roles: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    work_types: Optional[str] = Form(None),
    experience: Optional[str] = Form(None)
):
    """Combined upload, extract, filter, match, and URL-resolve logic."""
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

    # 3. Parse user filters from Form Data
    target_role = parse_form_field(roles) or "Software Engineer"
    target_location = parse_form_field(location)
    target_work_type = parse_form_field(work_types)
    target_experience = parse_form_field(experience)

    # 4. Build Query using Role + Top Skills + Filters
    search_query = f"{target_role} " + " ".join(extracted_skills[:3])
    
    # 5. Query Adzuna
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=target_location)
    if not raw_jobs and target_location:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location="")

    # 6. Score and Rank
    ranked_jobs = []
    for job in raw_jobs:
        scored_job = calculate_job_scores(
            job=job, 
            target_role=target_role, 
            extracted_skills=extracted_skills, 
            target_location=target_location, 
            target_work_type=target_work_type, 
            target_experience=target_experience
        )
        ranked_jobs.append(scored_job)

    # Sort descending by best match
    ranked_jobs.sort(key=lambda x: x["matchScore"], reverse=True)
    
    # Limit to top 20 for performance, then resolve direct URLs concurrently
    top_jobs = ranked_jobs[:20]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        top_jobs = list(executor.map(resolve_job_url, top_jobs))

    return {
        "extracted_skills": extracted_skills,
        "total_matches": len(top_jobs),
        "jobs": top_jobs
    }
