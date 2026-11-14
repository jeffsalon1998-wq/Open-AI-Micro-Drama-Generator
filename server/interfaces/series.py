"""Series / retention data models."""
from typing import List, Optional
from pydantic import BaseModel, Field


class EpisodePlan(BaseModel):
    episode_number: int
    title: str
    hook_line: str = Field(description="First spoken line / on-screen text in first 3 seconds")
    open_loop: str = Field(description="Curiosity question posed but not answered this episode")
    beats: List[str] = Field(default_factory=list, description="5-7 escalating beats")
    twist: str = ""
    cliffhanger: str = Field(description="Final 3 seconds — must force next episode")
    payoff: str = Field(description="One loop closed this episode (satisfaction)")


class SeriesBible(BaseModel):
    title: str
    logline: str
    genre: str
    loop_question: str = Field(description="The one question that drives the whole series")
    protagonist: str
    antagonist: str
    secret: str = Field(description="Hidden truth revealed gradually")
    episodes: List[EpisodePlan] = Field(default_factory=list)


class HookAudit(BaseModel):
    hook_score: int = Field(ge=0, le=100)
    retention_score: int = Field(ge=0, le=100)
    cliffhanger_score: int = Field(ge=0, le=100)
    overall: int = Field(ge=0, le=100)
    strengths: List[str] = Field(default_factory=list)
    fixes: List[str] = Field(default_factory=list)
    verdict: str = ""
