from __future__ import annotations

import math
import re
from typing import Optional, Sequence


class SemanticMatcher:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name

    def semantic_similarity(self, text_a: str, text_b: str) -> float:
        if not text_a.strip() or not text_b.strip():
            return 0.0
        tokens_a = self._tokenize(text_a)
        tokens_b = self._tokenize(text_b)
        if not tokens_a or not tokens_b:
            return 0.0

        vector_a = self._bag_of_words(tokens_a)
        vector_b = self._bag_of_words(tokens_b)
        numerator = sum(vector_a[word] * vector_b.get(word, 0.0) for word in vector_a)
        magnitude_a = math.sqrt(sum(value * value for value in vector_a.values()))
        magnitude_b = math.sqrt(sum(value * value for value in vector_b.values()))
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
        return max(0.0, min(1.0, numerator / (magnitude_a * magnitude_b)))

    def _tokenize(self, text: str) -> list[str]:
        cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return [token for token in cleaned.split() if token]

    def _bag_of_words(self, tokens: Sequence[str]) -> dict[str, float]:
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0.0) + 1.0
        return counts


class MatchScorer:
    def __init__(self, matcher: Optional[SemanticMatcher] = None):
        self.matcher = matcher or SemanticMatcher()

    def score_job(
        self,
        resume_text: str,
        job_description: str,
        required_skills: Sequence[str],
        years_experience: int,
        location: str,
        resume_location: str,
        weights: Optional[dict[str, float]] = None,
    ) -> float:
        weights = weights or {
            "semantic": 0.55,
            "skills": 0.25,
            "experience": 0.10,
            "location": 0.10,
        }

        semantic_score = self.matcher.semantic_similarity(resume_text, job_description)
        skill_score = self._skill_overlap_score(required_skills, resume_text)
        experience_score = self._experience_score(years_experience, job_description)
        location_score = self._location_score(location, resume_location)

        final_score = (
            semantic_score * weights["semantic"]
            + skill_score * weights["skills"]
            + experience_score * weights["experience"]
            + location_score * weights["location"]
        )
        return round(final_score * 100, 2)

    def _skill_overlap_score(self, required_skills: Sequence[str], resume_text: str) -> float:
        if not required_skills:
            return 0.0
        normalized_resume = resume_text.lower()
        hits = 0
        for skill in required_skills:
            if skill.lower() in normalized_resume:
                hits += 1
        return hits / max(1, len(required_skills))

    def _experience_score(self, years_experience: int, job_description: str) -> float:
        lowered = job_description.lower()
        if "senior" in lowered or "lead" in lowered:
            return min(1.0, years_experience / 8.0)
        if "mid" in lowered or "intermediate" in lowered:
            return min(1.0, years_experience / 5.0)
        return min(1.0, years_experience / 3.0)

    def _location_score(self, job_location: str, resume_location: str) -> float:
        if not job_location or not resume_location:
            return 0.5
        if job_location.lower() == resume_location.lower():
            return 1.0
        if "remote" in job_location.lower() or "remote" in resume_location.lower():
            return 0.8
        return 0.0
