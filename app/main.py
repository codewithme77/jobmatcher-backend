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

# Role keyword synonyms — used for strict title filtering
ROLE_KEYWORDS = {
    "product manager": ["product manager", "product owner", "pm ", " pm", "product lead", "product director"],
    "product owner": ["product owner", "product manager", "scrum master", "po "],
    "software engineer": ["software engineer", "software developer", "swe", "backend", "frontend", "fullstack", "full stack"],
    "data scientist": ["data scientist", "data analyst", "ml engineer", "machine learning"],
    "designer": ["designer", "ux", "ui", "product design"],
    "marketing manager": ["marketing manager", "marketing lead", "growth manager", "demand generation"],
    "sales director": ["sales director", "sales manager", "account executive", "business development"],
}

def get_role_keywords(target_role: str) -> List[str]:
    """Return list of acceptable title keywords for a given role."""
    role_lower = target_role.lower().strip()
    for key, keywords in ROLE_KEYWORDS.items():
        if key in role_lower or role_lower in key:
            return keywords
    # Fallback: split the role into words and use each word (2+ chars)
    words = [w for w in role_lower.split() if len(w) > 2]
    return [role_lower] + words

def title_matches_role(title: str, target_role: str) -> bool:
    """Returns True only if the job title is genuinely related to the target role."""
    title_lower = title.lower()
    keywords = get_role_keywords(target_role)
    return any(kw in title_lower for kw in keywords)


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


# ── Source 1: JSearch (RapidAPI) ─────────────────────────────────────────────

def fetch_jsearch_jobs(query: str, location: str = "", target_role: str = "") -> List[Dict]:
    api_key = os.getenv("JSEARCH_API_KEY", "")
    if not api_key:
        print("JSearch: no API key, skipping.")
        return []

    q = f"{query} in {location}" if location else query
    url = "https://jsearch.p.rapidapi.com/search"
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {"query": q, "page": "1", "num_pages": "1", "date_posted": "month"}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            print(f"JSearch {res.status_code}")
            return []
        data = res.json()
    except Exception as e:
        print(f"JSearch error: {e}")
        return []

    jobs = []
    for item in data.get("data", []):
        title = item.get("job_title", "")
        # STRICT: skip if title doesn't relate to target role
        if target_role and not title_matches_role(title, target_role):
            continue
        apply_url = item.get("job_apply_link") or item.get("job_google_link") or ""
        jobs.append({
            "id": f"js_{item.get('job_id', '')}",
            "title": title,
            "company": item.get("employer_name", ""),
            "location": f"{item.get('job_city', '')} {item.get('job_country', '')}".strip(),
            "salary": float(item.get("job_min_salary") or 0),
            "url": apply_url,
            "apply_url": apply_url,
            "description": item.get("job_description", ""),
            "source": "JSearch",
        })
    return jobs


# ── Source 2: Arbeitnow ──────────────────────────────────────────────────────

def fetch_arbeitnow_jobs(query: str, location: str = "", target_role: str = "") -> List[Dict]:
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception as e:
        print(f"Arbeitnow error: {e}")
        return []

    jobs = []
    for item in data.get("data", []):
        title = item.get("title", "")
        # STRICT: title must match role
        if target_role and not title_matches_role(title, target_role):
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
            "description": item.get("description", ""),
            "source": "Arbeitnow",
        })
        if len(jobs) >= 10:
            break
    return jobs


# ── Source 3: RemoteOK ───────────────────────────────────────────────────────

def fetch_remoteok_jobs(query: str, target_role: str = "") -> List[Dict]:
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

    jobs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("position", "")
        # STRICT: title must match role — NO tag fallback (tags caused Dietician bug)
        if target_role and not title_matches_role(title, target_role):
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


# ── Fetch all in parallel ─────────────────────────────────────────────────────

def fetch_all_jobs(query: str, location: str = "", target_role: str = "") -> List[Dict]:
    results = []
    seen = set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_jsearch_jobs, query, location, target_role): "jsearch",
            executor.submit(fetch_arbeitnow_jobs, query, location, target_role): "arbeitnow",
            executor.submit(fetch_remoteok_jobs, query, target_role): "remoteok",
        }
        for future in as_completed(futures):
            try:
                for job in future.result():
                    key = (job["title"].lower().strip(), job["company"].lower().strip())
                    if key not in seen:
                        seen.add(key)
                        results.append(job)
            except Exception as e:
                print(f"Fetch error: {e}")

    return results


# ── Scoring ───────────────────────────────────────────────────────────────────

MIN_ROLE_FIT = 50    # job title must score at least 50 vs target role
MIN_OVERALL  = 55    # composite must be at least 55 to show in results
TOP_N        = 10    # return only top 10

def calculate_job_scores(
    job: Dict,
    target_role: str,
    extracted_skills: List[str],
    target_location: Optional[str],
    target_work_type: Optional[str],
    target_experience: Optional[str],
) -> Dict:
    title = job.get("title", "").lower()
    desc  = job.get("description", "").lower()
    job_loc = job.get("location", "").lower()

    # 1. Role fit — fuzzy match of target role vs job title
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
        role_fit      * 0.35
        + domain_score  * 0.30
        + location_score * 0.15
        + experience_score * 0.10
        + work_type_score  * 0.10
    )

    job.update({
        "match_score":     overall,
        "matchScore":      overall,
        "role_fit":        role_fit,
        "roleFit":         role_fit,
        "domain":          domain_score,
        "domainScore":     domain_score,
        "location":        location_score,
        "locationScore":   location_score,
        "location_score":  location_score,
        "work_type":       work_type_score,
        "workType":        work_type_score,
        "workTypeScore":   work_type_score,
        "experience":      experience_score,
        "experienceScore": experience_score,
        "matched_skills":  matched_skills,
        "matchedSkills":   matched_skills,
        "job_location":    job.get("location", ""),
    })
    return job


def score_and_rank(
    raw_jobs: List[Dict],
    target_role: str,
    extracted_skills: List[str],
    location: Optional[str],
    work_types: Optional[str],
    experience: Optional[str],
) -> List[Dict]:
    """Score, hard-filter weak matches, sort, return top N."""
    scored = []
    for job in raw_jobs:
        j = calculate_job_scores(job, target_role, extracted_skills,
                                  location, work_types, experience)
        # Hard gates — role must be relevant AND overall score must be decent
        if j["role_fit"] >= MIN_ROLE_FIT and j["match_score"] >= MIN_OVERALL:
            scored.append(j)

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored[:TOP_N]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "JobMatcher v4 — strict matching, top 10 only"}


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
    jobs = fetch_all_jobs(query=query, location=location, target_role=query)
    return {"total": len(jobs), "jobs": jobs}


@app.post("/match")
def match_jobs(req: MatchRequest):
    query = f"{req.target_role} " + " ".join(req.extracted_skills[:3])
    raw = fetch_all_jobs(query.strip(), req.location or "", req.target_role)
    ranked = score_and_rank(raw, req.target_role, req.extracted_skills,
                            req.location, req.work_types, req.experience)
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

    # 4. Search query: role + top 3 skills
    skill_terms  = " ".join(extracted_skills[:3])
    search_query = f"{target_role} {skill_terms}".strip()

    # 5. Fetch all 3 sources in parallel with strict title filtering
    raw_jobs = fetch_all_jobs(search_query, location or "", target_role)

    # 6. Score, hard-filter, top 10
    ranked = score_and_rank(raw_jobs, target_role, extracted_skills,
                            location, work_types, experience)

    return {
        "extracted_skills": extracted_skills,
        "target_role":      target_role,
        "total_matches":    len(ranked),
        "jobs":             ranked,
    }
