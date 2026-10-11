from typing import Literal
from pydantic import BaseModel, Field


class CodeExample(BaseModel):
    caption: str = Field(default="", description="Short label for this code block, e.g. 'UserRepository interface'")
    language: str = Field(default="", description="Programming language or config format, e.g. 'java', 'yaml', 'sql'")
    code: str = Field(default="", description="The full code snippet, exactly as it appears or is implied by the transcript. Preserve indentation with spaces.")


class ChunkSummary(BaseModel):
    title: str = Field(default="", description="Clear, descriptive title for this video segment")
    overview: str = Field(default="", description="2-3 sentence technical overview of this segment")
    key_concepts: list[str] = Field(default_factory=list, description="Core concepts introduced or used in this segment")
    important_points: list[str] = Field(default_factory=list, description="Detailed bullet points covering technical details")
    examples: list[str] = Field(default_factory=list, description="Concrete examples or analogies mentioned")
    technical_terms: list[str] = Field(default_factory=list, description="Key technical terms")
    code_examples: list[CodeExample] = Field(default_factory=list, description="List of code snippets, configs, or commands reconstructed from this chunk")
    keywords: list[str] = Field(default_factory=list, description="5-8 search keywords/topics")
    new_definitions: list[str] = Field(default_factory=list, description="'term: definition' ONLY for terms this part explicitly defines")


class NotesSection(BaseModel):
    heading: str = Field(default="", description="Section heading")
    start_sec: int = Field(default=0, description="Second where this topic starts, taken from the [Ns] markers")
    overview: str = Field(default="", description="2-3 sentence summary of what this section covers")
    points: list[str] = Field(default_factory=list, description="Detailed bullet points — each one self-contained and technically precise")
    key_concepts: list[str] = Field(default_factory=list, description="Core concepts introduced or used in this section")
    examples: list[str] = Field(default_factory=list, description="Concrete real-world examples or analogies mentioned")
    code_examples: list[CodeExample] = Field(default_factory=list, description="Any code snippets, class/interface definitions, config, or shell commands discussed. Reconstruct faithfully from context.")
    technical_terms: list[str] = Field(default_factory=list, description="Key technical terms defined or used in this section")


class FinalNotes(BaseModel):
    title: str = Field(default="", description="Comprehensive title for the final study notes")
    overview: str = Field(default="", description="Comprehensive overview summarizing the entire video")
    sections: list[NotesSection] = Field(default_factory=list, description="Ordered sections covering all topics")
    key_takeaways: list[str] = Field(default_factory=list, description="Key takeaways and conclusions")


class VideoOverviewSynthesis(BaseModel):
    title: str = Field(default="", description="Refined, professional title for the study notes")
    overview: str = Field(default="", description="Comprehensive 2-3 paragraph executive summary of the entire video")
    key_takeaways: list[str] = Field(default_factory=list, description="5-8 high-impact bullet takeaways from the entire video")



class QueryAnalysis(BaseModel):
    standalone_question: str = Field(default="", description="Question rewritten to be self-contained using chat history")
    intent: Literal["qa", "locate", "overview"] = Field(
        default="qa",
        description="locate = asks WHERE/WHEN a topic is discussed; overview = asks for a broad summary; else qa")


class QueryVariants(BaseModel):
    queries: list[str] = Field(default_factory=list)
