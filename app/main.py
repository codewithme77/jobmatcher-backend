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

# Expanded skill list for better resume coverage
COMMON_SKILLS = [
    "python", "fastapi", "sql", "postgresql", "docker", "aws",
    "react", "javascript", "typescript", "node.js", "git", "rest api",
    "product management", "product strategy", "agile", "scrum",
    "roadmapping", "jira", "analytics", "user research", "a/b testing",
    "wireframing", "product lifecycle", "kpis", "stakeholder management",
    "go-to-market", "gtm", "okr", "okrs", "data analysis", "tableau",
    "salesforce", "crm", "machine learning", "ai", "llm", "generative ai",
    "cross-functional", "p&l", "growth", "monetization", "api", "saas",
    "b2b", "b2c", "mobile", "ios", "android", "figma", "sketch",
]


# --- Pydantic Models ---
class MatchRequest(BaseModel):
    extracted_skills: List[str]
    target_role: str
    location: Optional[str] = None
    work_types: Optional[str] = None
    experience: Optional[str] = None


class SaveRequest(BaseModel):
    job_id: str
    action: str


# --- Helper: resolve Adzuna redirect to real job URL ---
def resolve_direct_url(redirect_url: str) -> str:
    """
    Follow the Adzuna redirect_url to get the final employer job page URL.
    Returns redirect_url as fallback if resolution fails.
    """
    if not redirect_url:
        return redirect_url
    try:
        resp = requests.head(redirect_url, allow_redirects=True, timeout=8)
        final_url = resp.url
        # Adzuna sometimes lands on their own detail page; skip that too
        if "adzuna." in final_url:
            resp2 = requests.get(redirect_url, allow_redirects=True, timeout=8)
            final_url = resp2.url
        return final_url if final_url else redirect_url
    except Exception:
        return redirect_url


# --- Helper: fetch jobs from Adzuna ---
def fetch_adzuna_jobs(query: str, location: str = "") -> List[Dict]:
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")

    if not app_id or not app_key:
        print("Warning: Adzuna API keys not set.")
        return []

    url = "https://api.adzuna.com/v1/api/jobs/in/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json",
        "what": query,
    }
    if location:
        params["where"] = location

    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            print(f"Adzuna returned {res.status_code}: {res.text[:200]}")
            return []
        data = res.json()
    except Exception as e:
        print(f"Adzuna API Error: {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        redirect_url = item.get("redirect_url", "")
        # Resolve to direct employer URL (Fix #3)
        direct_url = resolve_direct_url(redirect_url)
        jobs.append({
            "id": str(item.get("id", "")),
            "title": item.get("title", ""),
            "company": item.get("company", {}).get("display_name", ""),
            "location": item.get("location", {}).get("display_name", ""),
            "salary": float(item.get("salary_min", 0) or 0),
            "url": direct_url,           # direct employer link
            "apply_url": direct_url,     # explicit apply link for frontend
            "description": item.get("description", ""),
        })
    return jobs


# --- Helper: score a single job against user profile ---
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

    # 1. Role Fit
    role_fit = fuzz.partial_ratio(target_role.lower(), title)

    # 2. Skills / Domain Fit — also surface matched skills for UI breakdown
    if extracted_skills:
        matched_skills = [s for s in extracted_skills if s.lower() in desc]
        domain_score = int((len(matched_skills) / len(extracted_skills)) * 100)
    else:
        matched_skills = []
        domain_score = 50

    # 3. Location Fit
    if target_location:
        location_score = fuzz.partial_ratio(target_location.lower(), job_loc)
    else:
        location_score = 100

    # 4. Work Type Fit
    work_type_score = 80
    if target_work_type:
        wt_lower = target_work_type.lower()
        if wt_lower in desc or wt_lower in title:
            work_type_score = 100
        elif "remote" in desc and "remote" in wt_lower:
            work_type_score = 100
        else:
            work_type_score = 50

    # 5. Experience Fit
    experience_score = 80
    if target_experience:
        exp_lower = target_experience.lower()
        if "senior" in title and "senior" not in exp_lower:
            experience_score = 40
        elif "junior" in title and "senior" in exp_lower:
            experience_score = 40

    # Weighted composite
    overall_score = (
        role_fit * 0.35
        + domain_score * 0.30
        + location_score * 0.15
        + experience_score * 0.10
        + work_type_score * 0.10
    )

    # Write all score fields — both snake_case and camelCase for frontend (Fix #2)
    job.update({
        "match_score": round(overall_score),
        "matchScore": round(overall_score),
        "role_fit": round(role_fit),
        "roleFit": round(role_fit),
        "domain": round(domain_score),
        "domainScore": round(domain_score),
        "location_score": round(location_score),
        "locationScore": round(location_score),
        "work_type": round(work_type_score),
        "workTypeScore": round(work_type_score),
        "experience_score": round(experience_score),
        "experienceScore": round(experience_score),
        "matched_skills": matched_skills,   # breakdown list for popup UI
        "matchedSkills": matched_skills,
    })
    return job


# --- Endpoints ---

@app.get("/")
def root():
    return {"status": "JobMatcher API is running"}


@app.post("/upload")
async def upload_resume(resume: UploadFile = File(...)):
    """Extract text + skills from PDF."""
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
    """Raw job fetch."""
    jobs = fetch_adzuna_jobs(query=query, location=location)
    return {"total": len(jobs), "jobs": jobs}


@app.post("/match")
def match_jobs(req: MatchRequest):
    """Score and rank jobs against skills + filters."""
    # Use up to 5 skills in query for better relevance (Fix #1)
    skill_terms = " ".join(req.extracted_skills[:5])
    search_query = f"{req.target_role} {skill_terms}".strip()

    raw_jobs = fetch_adzuna_jobs(query=search_query, location=req.location or "")
    if not raw_jobs:
        raw_jobs = fetch_adzuna_jobs(query=req.target_role, location="")

    ranked_jobs = [
        calculate_job_scores(
            job, req.target_role, req.extracted_skills,
            req.location, req.work_types, req.experience
        )
        for job in raw_jobs
    ]
    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)
    return {"total_matches": len(ranked_jobs), "jobs": ranked_jobs}


@app.post("/filter")
def filter_jobs(req: MatchRequest):
    """Re-score and filter already-fetched jobs."""
    skill_terms = " ".join(req.extracted_skills[:5])
    search_query = f"{req.target_role} {skill_terms}".strip()
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=req.location or "")

    ranked_jobs = [
        calculate_job_scores(
            job, req.target_role, req.extracted_skills,
            req.location, req.work_types, req.experience
        )
        for job in raw_jobs
    ]
    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)
    return {"total_matches": len(ranked_jobs), "jobs": ranked_jobs}


@app.post("/save")
def save_results(req: SaveRequest):
    return {"message": f"Job {req.job_id} saved with action {req.action}"}


# --- Primary Lovable endpoint ---
@app.post("/upload-and-search")
async def upload_and_search(
    resume: UploadFile = File(...),
    roles: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    work_types: Optional[str] = Form(None),
    experience: Optional[str] = Form(None),
):
    """Combined upload + match for Lovable frontend."""

    # 1. Parse PDF
    try:
        contents = await resume.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "\n".join([page.get_text() for page in doc]).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    # 2. Extract Skills from resume text
    extracted_skills = [
        s for s in COMMON_SKILLS
        if re.search(r"\b" + re.escape(s) + r"\b", text)
    ]

    # 3. Determine target role from filter (Fix #1 — honour user role selection)
    target_role = "Product Manager"
    if roles:
        try:
            parsed = json.loads(roles)
            if isinstance(parsed, list) and parsed:
                target_role = parsed[0]
            elif isinstance(parsed, str):
                target_role = parsed
        except Exception:
            target_role = roles.strip()

    # 4. Build rich search query: role + top 5 resume skills (Fix #1)
    skill_terms = " ".join(extracted_skills[:5])
    search_query = f"{target_role} {skill_terms}".strip()

    # 5. Fetch jobs — fallback to role-only if empty
    raw_jobs = fetch_adzuna_jobs(query=search_query, location=location or "")
    if not raw_jobs:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location=location or "")
    if not raw_jobs and location:
        raw_jobs = fetch_adzuna_jobs(query=target_role, location="")

    # 6. Score + rank (Fix #1 & #2)
    ranked_jobs = [
        calculate_job_scores(
            job=job,
            target_role=target_role,
            extracted_skills=extracted_skills,
            target_location=location,
            target_work_type=work_types,
            target_experience=experience,
        )
        for job in raw_jobs
    ]
    ranked_jobs.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "extracted_skills": extracted_skills,
        "target_role": target_role,
        "total_matches": len(ranked_jobs),
        "jobs": ranked_jobs,
    }
