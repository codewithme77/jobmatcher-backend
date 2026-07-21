from typing import List

from sqlalchemy.orm import Session

from app.models import Candidate, Job, JobCandidateMatch


def compute_match_score(job: Job, candidate: Candidate) -> int:
    """Calculate a simple relevance score based on shared skills."""
    job_skills = {skill.lower() for skill in job.required_skills or []}
    candidate_skills = {skill.lower() for skill in candidate.skills or []}
    overlap = job_skills & candidate_skills
    return len(overlap) * 10 + min(candidate.years_experience, 5) * 2


def build_matches(db: Session, job_id: int) -> List[JobCandidateMatch]:
    """Return all candidate matches for a job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        return []

    candidates = db.query(Candidate).all()
    matches: List[JobCandidateMatch] = []
    for candidate in candidates:
        score = compute_match_score(job, candidate)
        if score > 0:
            match = JobCandidateMatch(job_id=job.id, candidate_id=candidate.id, score=score)
            matches.append(match)
    return matches
