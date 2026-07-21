from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

import httpx


@dataclass(frozen=True)
class StandardJob:
    source: str
    external_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    remote: bool
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None


class JobProvider(ABC):
    name: str = ""

    @abstractmethod
    def fetch_jobs(self) -> List[StandardJob]:
        raise NotImplementedError


class ArbeitnowProvider(JobProvider):
    name = "arbeitnow"

    def __init__(self, base_url: str = "https://www.arbeitnow.com/api/job-board-api"):
        self.base_url = base_url

    def fetch_jobs(self) -> List[StandardJob]:
        response = httpx.get(self.base_url, timeout=15.0)
        response.raise_for_status()
        payload = response.json()
        jobs = []
        for item in payload.get("data", []):
            jobs.append(
                StandardJob(
                    source=self.name,
                    external_id=str(item.get("slug") or item.get("id") or ""),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    remote=bool(item.get("remote") or "remote" in str(item.get("location", "")).lower()),
                )
            )
        return jobs


class RemoteOKProvider(JobProvider):
    name = "remoteok"

    def __init__(self, base_url: str = "https://remoteok.com/api"):
        self.base_url = base_url

    def fetch_jobs(self) -> List[StandardJob]:
        response = httpx.get(self.base_url, timeout=15.0)
        response.raise_for_status()
        payload = response.json()
        jobs = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            jobs.append(
                StandardJob(
                    source=self.name,
                    external_id=str(item.get("id") or item.get("slug") or ""),
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    remote=True,
                )
            )
        return jobs


class GreenhouseProvider(JobProvider):
    name = "greenhouse"

    def __init__(self, base_url: str = "https://boards-api.greenhouse.io/v1/boards"):
        self.base_url = base_url

    def fetch_jobs(self) -> List[StandardJob]:
        response = httpx.get(self.base_url, timeout=15.0)
        response.raise_for_status()
        payload = response.json()
        jobs = []
        for item in payload.get("jobs", []):
            jobs.append(
                StandardJob(
                    source=self.name,
                    external_id=str(item.get("id") or ""),
                    title=item.get("title", ""),
                    company=item.get("department", {}).get("name", ""),
                    location=item.get("location", {}).get("name", ""),
                    description=item.get("content", ""),
                    url=item.get("absolute_url", ""),
                    remote="remote" in str(item.get("location", {}).get("name", "")).lower(),
                )
            )
        return jobs


class JobAggregator:
    def __init__(self, providers: Optional[List[JobProvider]] = None):
        self.providers = providers or [ArbeitnowProvider(), RemoteOKProvider(), GreenhouseProvider()]

    def fetch_all_jobs(self) -> List[StandardJob]:
        merged: List[StandardJob] = []
        seen = set()
        for provider in self.providers:
            try:
                for job in provider.fetch_jobs():
                    key = (job.source, job.external_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(job)
            except httpx.HTTPError:
                continue
        return merged
