import os
import re
import fitz  # PyMuPDF
import requests
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException
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

class Job(BaseModel):
    id: Optional[str] = None
    title: str
    company: str
    location: str
    salary: Optional[float] = 0.0
    url: str
    description: Optional[str] = ""
    score: Optional[float] = 0.0

# Expanded skill set including PM & Dev roles
COMMON_SKILLS = [
    "python", "fastapi", "sql", "postgresql", "docker", "aws", 
    "react", "javascript", "typescript", "node.js", "git", "rest api",
    "product management", "product strategy", "agile", "scrum", 
    "roadmapping", "jira", "analytics", "user research", "a/b testing"
]

def search_adzuna(query: str, location: str = "", country: str = "in"):
    """Queries Adzuna with specific target keywords and location."""
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []

    url = f"https://api.adzuna.com/v1/api/jobs/{country.lower()}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json",
        "what": query
    }
    if location:
        params["where"] = location

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
            "url": item.get("redirect_url"),
            "description": item.get("description", "")
        })
    return jobs

@app.post("/upload-and-search")
async def upload_and_search(
    file: UploadFile = File(...), 
    location: Optional[str] = "Mumbai",
    country: Optional[str] = "in"
):
    """Extracts resume text, searches Adzuna based on skills, and returns ranked jobs."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF supported")
    
    # 1. Parse Resume Text
    contents = await file.read()
    doc = fitz.open(stream=contents, filetype="pdf")
    text = "\n".join([page.get_text() for page in doc]).lower()

    # 2. Extract Skills
    extracted_skills = [s for s in COMMON_SKILLS if re.search(r'\b' + re.escape(s) + r'\b', text)]
    skills_str = " ".join(extracted_skills) if extracted_skills else "Product Manager"

    # 3. Fetch jobs dynamically based on extracted skills & location
    raw_jobs = search_adzuna(query=skills_str, location=location, country=country)

    # 4. Rank jobs based on fuzz matching score
    ranked_jobs = []
    for job in raw_jobs:
        target_text = f"{job['title']} {job['description']}"
        score = fuzz.token_set_ratio(skills_str, target_text)
        job["score"] = round(score, 1)
        ranked_jobs.append(job)

    ranked_jobs.sort(key=lambda x: x["score"], reverse=True)

    return {
        "extracted_skills": extracted_skills,
        "location": location,
        "total_matches": len(ranked_jobs),
        "jobs": ranked_jobs
    }
