from typing import List

from pydantic import BaseModel, EmailStr, Field


class JobCreate(BaseModel):
    title: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    required_skills: List[str] = Field(default_factory=list)


class JobRead(JobCreate):
    id: int


class CandidateCreate(BaseModel):
    full_name: str = Field(..., min_length=1)
    email: EmailStr
    desired_title: str = Field(..., min_length=1)
    years_experience: int = Field(default=0, ge=0)
    skills: List[str] = Field(default_factory=list)


class CandidateRead(CandidateCreate):
    id: int


class MatchResult(BaseModel):
    candidate_id: int
    candidate_name: str
    score: int


class MatchResponse(BaseModel):
    job_id: int
    results: List[MatchResult]


class ResumeUploadResponse(BaseModel):
    filename: str
    extracted_text: str
