"""The review format every AI provider must return, and the scoring rubric."""
from typing import List, Literal

from pydantic import BaseModel, Field

RUBRIC = [
    ("Pacing & rhythm", 15, "Shot lengths, cut density, breathing room, momentum across the runtime."),
    ("Story flow & structure", 15, "Opening hook, chaptering, clarity of the arc, how sections hand off."),
    ("Colour & grade cohesion", 15, "Consistent look between shots and scenes, exposure, skin tones, intentional palette."),
    ("Typography & titles", 15, "Legibility, hierarchy, consistency of title styles, timing on screen, safe areas."),
    ("Audio mix & music fit", 15, "Dialogue clarity, music level under speech, loudness, transitions, music matching mood."),
    ("Transitions & motion design", 10, "Purposeful transitions, consistency, overlays/effects serving the story."),
    ("Mood & tone cohesion", 15, "Whether picture, music, words and design all express one consistent feeling."),
]
CATEGORIES = Literal[
    "Pacing & rhythm", "Story flow & structure", "Colour & grade cohesion", "Typography & titles",
    "Audio mix & music fit", "Transitions & motion design", "Mood & tone cohesion",
]
ACTIONS = Literal[
    "add_title", "restyle_title", "add_transition", "remove_transition", "adjust_volume", "duck_music",
    "colour_match", "trim", "add_overlay", "add_marker_only",
]


class CategoryScore(BaseModel):
    category: CATEGORIES
    score: int = Field(description="Points earned, 0..max")
    max: int
    rationale: str


class Suggestion(BaseModel):
    id: str = Field(description="Short id like S01")
    start_seconds: float
    end_seconds: float
    category: CATEGORIES
    severity: Literal["high", "medium", "low"]
    issue: str = Field(description="What is wrong or could be better, concretely")
    suggestion: str = Field(description="The design change to make")
    how_in_final_cut: str = Field(description="Step-by-step how to do it in Final Cut Pro")
    action: ACTIONS = Field(description="Closest automatic edit type")
    expected_gain: int = Field(description="Score points this would likely add")


class Review(BaseModel):
    overall_score: int = Field(description="0..100, sum of category scores")
    verdict: str = Field(description="Two or three sentences")
    tone_and_mood: str = Field(description="How the film feels now, across picture, music and words")
    strengths: List[str]
    categories: List[CategoryScore]
    suggestions: List[Suggestion]


def rubric_text():
    return "\n".join(f"- {name} ({pts} pts): {desc}" for name, pts, desc in RUBRIC)


# ---- edit plans (Phase 5: apply approved edits) -------------------------------------------------
from typing import Optional  # noqa: E402

EDIT_OPS = Literal[
    "set_volume", "duck", "fade_audio", "add_transition", "remove_transition", "add_title", "restyle_title",
    "copy_grade", "trim", "duplicate", "set_enabled", "add_marker", "cut_range", "move_range", "swap_take",
]


class EditOp(BaseModel):
    op: EDIT_OPS
    note: str = Field(description="One plain-English sentence for the editor: what changes and why")
    suggestion_id: Optional[str] = Field(default=None, description="Review suggestion this carries out, e.g. S03")
    targets: List[str] = Field(default_factory=list, description="Timeline item ids (i0001…) this edit changes")
    template: Optional[str] = Field(default=None, description="Item id to copy a look/style/clip from, or a transition name")
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    duration_seconds: Optional[float] = None
    gain_db: Optional[float] = None
    under_speech: Optional[bool] = None
    ramp_seconds: Optional[float] = Field(default=None, description="Duck fade in/out length, default 0.4")
    fade_in_seconds: Optional[float] = None
    fade_out_seconds: Optional[float] = None
    text: Optional[str] = None
    lane: Optional[int] = None
    position: Optional[Literal["start", "end"]] = None
    font: Optional[str] = None
    font_size: Optional[float] = None
    font_color: Optional[str] = Field(default=None, description="#RRGGBB")
    copy_position: Optional[bool] = None
    trim_head_seconds: Optional[float] = None
    trim_tail_seconds: Optional[float] = None
    enabled: Optional[bool] = None
    to_seconds: Optional[float] = Field(default=None, description="move_range: where the moved section should start (timeline seconds)")
    source_start_seconds: Optional[float] = Field(default=None, description="swap_take: new in-point in the source file (seconds)")
    source: Optional[str] = Field(default=None, description="swap_take: another media file name to take from (optional)")


class EditPlan(BaseModel):
    summary: str = Field(description="Two sentences on what this set of edits does to the film")
    ops: List[EditOp]
    not_possible: List[str] = Field(default_factory=list,
                                    description="Parts of the request that can't be done automatically, and what to do by hand instead")
