import os
import re
import json
import fitz  # PyMuPDF
import requests
from typing import List, Optional, Dict
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
    "wireframing", "product lifecycle", "kpis", "stakeholder management",
    "go-to-market", "gtm", "okr", "data analysis", "tableau",
    "salesforce", "crm", "machine learning", "ai", "generative ai",
    "growth", "monetization", "saas", "b2b", "b2c", "figma",
]

# Maps location keywords → Adzuna country code + canonical where-param
# Adzuna country codes: gb, us, au, ca, de, fr, in, ae, sg, nz, za, nl, pl, ru, br
LOCATION_MAP = {
    "abu dhabi": ("ae", "Abu Dhabi"),
    "dubai": ("ae", "Dubai"),
    "uae": ("ae", ""),
    "united arab emirates": ("ae", ""),
    "sharjah": ("ae", "Sharjah"),
    "ajman": ("ae", "Ajman"),
    "remote": ("ae", ""),          # default remote to UAE since user is targeting UAE
    "bangalore": ("in", "Bangalore"),
    "bengaluru": ("in", "Bangalore"),
    "mumbai": ("in", "Mumbai"),
    "delhi": ("in", "Delhi"),
    "hyderabad": ("in", "Hyderabad"),
    "pune": ("in", "Pune"),
    "india": ("in", ""),
    "london": ("gb", "London"),
    "new york": ("us", "New York"),
    "singapore": ("sg", ""),
}

def get_country_and_where(location: str):
    """Return (adzuna_country_code, where_param) from free-text location."""
    if not location:
        return "ae", ""   # default to UAE (user's target market)
    loc_lower = location.lower().strip()
    for keyword, (code, where) in LOCATION_MAP.items():
        if keyword in loc_lower:
            return code, where
    # Unknown location — pass as-is to India endpoint as last resort
    return "in", location


class MatchRequest(BaseModel):
    extracted_skills: List[str]
    target_role: str
    location: Optional[str] = None
    work_types: Optional[str] = None
    experience: Optional[str] = None


class SaveRequest(BaseModel):
    job_id: str
    action: str


def fetch_adzuna_jobs(query: str, location: str = "") -> List[Dict]:
    """Fetch jobs from correct Adzuna country endpoint based on location."""
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        print("Warning: Adzuna API keys not set.")
        return []

    country, where_param = get_country_and_where(location)
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 20,   # reduced from 50 → faster response
        "what": query,
    }
    if where_param:
        params["where"] = where_param

    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            print(f"Adzuna {country} returned {res.status_code}")
            return []
        data = res.json()
    except Exception as e:
        print(f"Adzuna API Error: {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        # redirect_url IS the apply link — pass directly, open in new tab on frontend
        apply_url = item.get("redirect_url", "")
        jobs.append({
            "id": str(item.get("id", "")),
            "title": item.get("title", ""),
            "company": item.get("company", {}).get("display_name", ""),
            "location": item.get("location", {}).get("display_name", ""),
            "salary": float(item.get("salary_min", 0) or 0),
            "url": apply_url,        # used by frontend for job detail click
            "apply_url": apply_url,  # explicit field for Apply button
            "description": item.get("description", ""),
        })
    return jobs


def calculate_job_scores(
    job: Dict,
    target_role: str,
    extracted_skills: List[str],
    target_location: Optional[str],
    target_work_type: Optional[str],
    target_experience: Optional[str],
) -> Dict:
    title = job.get("title", "").lower()
    desc = job.get("description", "").lower()
    job_loc = job.get("location", "").lower()

    # 1. Role fit
    role_fit = fuzz.partial_ratio(target_role.lower(), title)

    # 2. Skills / domain fit
    if extracted_skills:
        matched_skills = [s for s in extracted_skills if s.lower() in desc]
        domain_score = int((len(matched_skills) / len(extracted_skills)) * 100)
    else:
        matched_skills = []
        domain_score = 50

    # 3. Location fit
    if target_location:
        _, canonical = get_country_and_where(target_location)
        loc_to_match = canonical if canonical else target_location
        location_score = fuzz.partial_ratio(loc_to_match.lower(), job_loc)
    else:
        location_score = 100

    # 4. Work type fit
    work_type_score = 80
    if target_work_type:
        wt = target_work_type.lower()
        if wt in desc or wt in title:
            work_type_score = 100
        else:
            work_type_score = 50

    # 5. Experience fit
    experience_score = 80
    if target_experience:
        exp = target_experience.lower()
        if "senior" in title and "senior" not in exp:
            experience_score = 40
        elif "junior" in title and "senior" in exp:
            experience_score = 40

    overall = (
        role_fit * 0.35
        + domain_score * 0.30
        + location_score * 0.15
        + experience_score * 0.10
        + work_type_score * 0.10
    )

    # Use EXACT field names the Lovable frontend reads (from score breakdown UI)
    job.update({
        "match_score": round(overall),
        "matchScore": round(overall),
        # Score breakdown fields — matching what Lovable renders
        "role_fit": round(role_fit),
        "roleFit": round(role_fit),
        "experience": round(experience_score),
        "experienceScore": round(experience_score),
        "location": round(location_score),          # NOTE: overwrites location string!
        "locationScore": round(location_score),
        "work_type": round(work_type_score),
        "workType": round(work_type_score),
        "domain": round(domain_score),
        "domainScore": round(domain_score),
        "matched_skills": matched_skills,
        "matchedSkills": matched_skills,
        # Preserve location string separately so UI can still show city
        "job_location": job.get("location", ""),
    })
    return job


@app.get("/")
def root():
    return {"status": "JobMatcher API is running"}


@app.post("/upload")
async def upload_resume(resume: UploadFile = File(...)):
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    extracted_skills = [
        s for s in COMMON_SKILLS
        if re.search(r"\b" + re.escape(s) + r"\b", text)
    ]
    return {"message": "Resume processed successfully", "extracted_skills": extracted_skills}


@app.get("/jobs")
def get_jobs(query: str = "Product Manager", location: str = ""):
    jobs = fetch_adzuna_jobs(query=query, location=location)
    return {"total": len(jobs), "jobs": jobs}


@app.post("/match")
def match_jobs(req: MatchRequest):
    search_query = f"{req.target_role} " + " ".join(req.extracted_skills[:3])
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=req.location or "")
    if not raw_jobs:
        raw_jobs = fetch_adzuna_jobs(query=req.target_role, location=req.location or "")

    ranked = [
        calculate_job_scores(j, req.target_role, req.extracted_skills,
                             req.location, req.work_types, req.experience)
        for j in raw_jobs
    ]
    ranked.sort(key=lambda x: x["match_score"], reverse=True)
    return {"total_matches": len(ranked), "jobs": ranked}


@app.post("/filter")
def filter_jobs(req: MatchRequest):
    return match_jobs(req)


@app.post("/save")
def save_results(req: SaveRequest):
    return {"message": f"Job {req.job_id} saved with action {req.action}"}


@app.post("/upload-and-search")
async def upload_and_search(
    resume: UploadFile = File(...),
    roles: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    work_types: Optional[str] = Form(None),
    experience: Optional[str] = Form(None),
):
    # 1. Parse PDF
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    # 2. Extract skills
    extracted_skills = [
        s for s in COMMON_SKILLS
        if re.search(r"\b" + re.escape(s) + r"\b", text)
    ]

    # 3. Target role
    target_role = "Product Manager"
    if roles:
        try:
            parsed = json.loads(roles)
            target_role = parsed[0] if isinstance(parsed, list) and parsed else str(parsed)
        except Exception:
            target_role = roles.strip()

    # 4. Fetch: role + top 3 skills (keep query tight for speed)
    search_query = f"{target_role} " + " ".join(extracted_skills[:3])
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=location or "")
    if not raw_jobs:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location=location or "")

    # 5. Score + rank
    ranked = [
        calculate_job_scores(
            job=j,
            target_role=target_role,
            extracted_skills=extracted_skills,
            target_location=location,
            target_work_type=work_types,
            target_experience=experience,
        )
        for j in raw_jobs
    ]
    ranked.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "extracted_skills": extracted_skills,
        "target_role": target_role,
        "total_matches": len(ranked),
        "jobs": ranked,
    }
