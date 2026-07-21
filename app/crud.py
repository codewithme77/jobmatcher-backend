from typing import Optional

from sqlalchemy.orm import Session

from app.models import Resume, Search, User


def create_user(db: Session, email: str, full_name: Optional[str] = None) -> User:
    user = User(email=email, full_name=full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def create_resume(db: Session, user_id, file_name: str, storage_path: str, extracted_text: Optional[str] = None) -> Resume:
    resume = Resume(user_id=user_id, file_name=file_name, storage_path=storage_path, extracted_text=extracted_text)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def get_resumes_for_user(db: Session, user_id) -> list[Resume]:
    return db.query(Resume).filter(Resume.user_id == user_id).order_by(Resume.created_at.desc()).all()


def create_search(db: Session, user_id, query: str, location: Optional[str] = None) -> Search:
    search = Search(user_id=user_id, query=query, location=location)
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


def get_searches_for_user(db: Session, user_id) -> list[Search]:
    return db.query(Search).filter(Search.user_id == user_id).order_by(Search.created_at.desc()).all()
