from app.semantic_matching import MatchScorer, SemanticMatcher


def test_matching_score_returns_value_between_zero_and_hundred():
    scorer = MatchScorer(matcher=SemanticMatcher(model_name="all-MiniLM-L6-v2"))
    score = scorer.score_job(
        resume_text="Experienced Python engineer with FastAPI and SQLAlchemy",
        job_description="Backend engineer building APIs with Python and FastAPI",
        required_skills=["python", "fastapi"],
        years_experience=5,
        location="Remote",
        resume_location="Remote",
    )
    assert 0 <= score <= 100
