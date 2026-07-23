import os
import re
import json
import fitz  # PyMuPDF
import requests
from typing import List, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "cross-functional", "p&l", "mobile", "ios", "android",
]


# ── Pydantic models ──────────────────────────────────────────────────────────

class MatchRequest(BaseModel):
    extracted_skills: List[str]
    target_role: str
    location: Optional[str] = None
    work_types: Optional[str] = None
    experience: Optional[str] = None

class SaveRequest(BaseModel):
    job_id: str
    action: str


# ── Source 1: JSearch (RapidAPI) — UAE/global, direct apply links ────────────

def fetch_jsearch_jobs(query: str, location: str = "") -> List[Dict]:
    api_key = os.getenv("JSEARCH_API_KEY", "")
    if not api_key:
        print("JSearch: no API key set, skipping.")
        return []

    # Build query string: "Product Manager in Dubai" style
    q = f"{query} in {location}" if location else query

    url = "https://jsearch.p.rapidapi.com/search"
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {
        "query": q,
        "page": "1",
        "num_pages": "1",
        "date_posted": "month",   # freshness filter
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            print(f"JSearch returned {res.status_code}")
            return []
        data = res.json()
    except Exception as e:
        print(f"JSearch error: {e}")
        return []

    jobs = []
    for item in data.get("data", []):
        # JSearch returns direct employer/ATS apply links
        apply_url = (
            item.get("job_apply_link")
            or item.get("job_google_link")
            or ""
        )
        jobs.append({
            "id": f"js_{item.get('job_id', '')}",
            "title": item.get("job_title", ""),
            "company": item.get("employer_name", ""),
            "location": f"{item.get('job_city', '')} {item.get('job_country', '')}".strip(),
            "salary": float(item.get("job_min_salary") or 0),
            "url": apply_url,
            "apply_url": apply_url,
            "description": item.get("job_description", ""),
            "source": "JSearch",
        })
    return jobs


# ── Source 2: Arbeitnow — Europe + remote, no key needed ────────────────────

def fetch_arbeitnow_jobs(query: str, location: str = "") -> List[Dict]:
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception as e:
        print(f"Arbeitnow error: {e}")
        return []

    query_lower = query.lower()
    loc_lower = location.lower() if location else ""

    jobs = []
    for item in data.get("data", []):
        title = item.get("title", "")
        tags = " ".join(item.get("tags", [])).lower()
        desc = item.get("description", "")

        # Client-side filter since API has no query param
        if query_lower not in title.lower() and query_lower not in tags:
            continue

        apply_url = item.get("url", "")
        jobs.append({
            "id": f"an_{item.get('slug', '')}",
            "title": title,
            "company": item.get("company_name", ""),
            "location": item.get("location", "Remote"),
            "salary": 0.0,
            "url": apply_url,
            "apply_url": apply_url,
            "description": desc,
            "source": "Arbeitnow",
        })
        if len(jobs) >= 10:   # cap to keep response fast
            break

    return jobs


# ── Source 3: RemoteOK — pure remote, no key needed ─────────────────────────

def fetch_remoteok_jobs(query: str) -> List[Dict]:
    url = "https://remoteok.com/api"
    headers = {"User-Agent": "JobMatcherMVP/1.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception as e:
        print(f"RemoteOK error: {e}")
        return []

    query_lower = query.lower()
    jobs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("position", "")
        tags = " ".join(item.get("tags", [])).lower() if item.get("tags") else ""
        if query_lower not in title.lower() and query_lower not in tags:
            continue

        apply_url = item.get("url") or item.get("apply_url") or ""
        jobs.append({
            "id": f"ro_{item.get('id', '')}",
            "title": title,
            "company": item.get("company", ""),
            "location": "Remote",
            "salary": float(item.get("salary_min") or 0),
            "url": apply_url,
            "apply_url": apply_url,
            "description": item.get("description", ""),
            "source": "RemoteOK",
        })
        if len(jobs) >= 10:
            break

    return jobs


# ── Fetch all sources in parallel ────────────────────────────────────────────

def fetch_all_jobs(query: str, location: str = "") -> List[Dict]:
    results = []
    seen = set()   # deduplicate by (title_lower, company_lower)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_jsearch_jobs, query, location): "jsearch",
            executor.submit(fetch_arbeitnow_jobs, query, location): "arbeitnow",
            executor.submit(fetch_remoteok_jobs, query): "remoteok",
        }
        for future in as_completed(futures):
            try:
                jobs = future.result()
                for job in jobs:
                    key = (job["title"].lower().strip(), job["company"].lower().strip())
                    if key not in seen:
                        seen.add(key)
                        results.append(job)
            except Exception as e:
                print(f"Fetch error: {e}")

    return results


# ── Scoring ──────────────────────────────────────────────────────────────────

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
        location_score = fuzz.partial_ratio(target_location.lower(), job_loc)
        # Remote jobs score well for any location preference
        if "remote" in job_loc:
            location_score = max(location_score, 70)
    else:
        location_score = 100

    # 4. Work type fit
    work_type_score = 80
    if target_work_type:
        wt = target_work_type.lower()
        if wt in desc or wt in title or wt in job_loc:
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

    overall = round(
        role_fit * 0.35
        + domain_score * 0.30
        + location_score * 0.15
        + experience_score * 0.10
        + work_type_score * 0.10
    )

    job.update({
        # Overall
        "match_score": overall,
        "matchScore": overall,
        # Breakdown — all naming variants for Lovable frontend
        "role_fit": role_fit,
        "roleFit": role_fit,
        "domain": domain_score,
        "domainScore": domain_score,
        "location_score": location_score,
        "locationScore": location_score,
        "location": location_score,       # what Lovable score breakdown reads
        "work_type": work_type_score,
        "workType": work_type_score,
        "workTypeScore": work_type_score,
        "experience": experience_score,
        "experienceScore": experience_score,
        # Skill breakdown list for popup
        "matched_skills": matched_skills,
        "matchedSkills": matched_skills,
        # Preserve city string separately
        "job_location": job.get("location", ""),
    })
    return job


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "JobMatcher API is running — v3 (JSearch + Arbeitnow + RemoteOK)"}


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
    jobs = fetch_all_jobs(query=query, location=location)
    return {"total": len(jobs), "jobs": jobs}


@app.post("/match")
def match_jobs(req: MatchRequest):
    query = f"{req.target_role} " + " ".join(req.extracted_skills[:3])
    raw_jobs = fetch_all_jobs(query=query.strip(), location=req.location or "")

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


# ── Primary Lovable endpoint ─────────────────────────────────────────────────

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

    # 2. Extract skills from resume
    extracted_skills = [
        s for s in COMMON_SKILLS
        if re.search(r"\b" + re.escape(s) + r"\b", text)
    ]

    # 3. Target role from filter selection
    target_role = "Product Manager"
    if roles:
        try:
            parsed = json.loads(roles)
            target_role = parsed[0] if isinstance(parsed, list) and parsed else str(parsed)
        except Exception:
            target_role = roles.strip()

    # 4. Build query: role + top 3 skills (tight = faster + more relevant)
    skill_terms = " ".join(extracted_skills[:3])
    search_query = f"{target_role} {skill_terms}".strip()

    # 5. Fetch all 3 sources in parallel
    raw_jobs = fetch_all_jobs(query=search_query, location=location or "")

    # 6. Score + rank
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
