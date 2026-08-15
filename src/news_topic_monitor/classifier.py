from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import Classification, ClassificationResult
from .utils import normalize_text, unique_preserving_order


class WeightedTerm(BaseModel):
    term: str
    score: float
    ambiguous: bool = False


class CombinationRule(BaseModel):
    all: list[str] = Field(default_factory=list)
    any: list[str] = Field(default_factory=list)
    score: float

    @model_validator(mode="after")
    def require_terms(self) -> CombinationRule:
        if not self.all and not self.any:
            raise ValueError("combination rule requires all or any terms")
        return self


class Thresholds(BaseModel):
    candidate: float
    review: float
    relevant: float

    @model_validator(mode="after")
    def ordered(self) -> Thresholds:
        if not self.candidate <= self.review < self.relevant:
            raise ValueError("thresholds must satisfy candidate <= review < relevant")
        return self


class TopicDefinition(BaseModel):
    label: str
    description: str
    thresholds: Thresholds
    field_weights: dict[str, float]
    core_terms: list[WeightedTerm] = Field(default_factory=list)
    supporting_terms: list[WeightedTerm] = Field(default_factory=list)
    laws: list[WeightedTerm] = Field(default_factory=list)
    policies: list[WeightedTerm] = Field(default_factory=list)
    organizations: list[WeightedTerm] = Field(default_factory=list)
    people_aliases: list[WeightedTerm] = Field(default_factory=list)
    conventions: list[WeightedTerm] = Field(default_factory=list)
    excluded_terms: list[WeightedTerm] = Field(default_factory=list)
    combinations: list[CombinationRule] = Field(default_factory=list)

    def positive_terms(self) -> list[WeightedTerm]:
        return [
            *self.core_terms,
            *self.supporting_terms,
            *self.laws,
            *self.policies,
            *self.organizations,
            *self.people_aliases,
            *self.conventions,
        ]


class TopicsFile(BaseModel):
    version: int
    topics: dict[str, TopicDefinition]


class SemanticClassifier(Protocol):
    """Optional paid semantic layer; the default implementation never makes API calls."""

    @property
    def enabled(self) -> bool: ...

    def refine(
        self, fields: dict[str, str], result: ClassificationResult
    ) -> ClassificationResult: ...


class DisabledSemanticClassifier:
    @property
    def enabled(self) -> bool:
        return False

    def refine(self, fields: dict[str, str], result: ClassificationResult) -> ClassificationResult:
        del fields
        return result


@dataclass(frozen=True)
class Match:
    term: str
    score: float
    strong: bool


class RuleClassifier:
    def __init__(
        self,
        config_path: Path,
        topic: str = "disability_rights",
        semantic: SemanticClassifier | None = None,
    ) -> None:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config = TopicsFile.model_validate(raw)
        if topic not in config.topics:
            raise ValueError(f"unknown topic: {topic}")
        self.topic_name = topic
        self.topic = config.topics[topic]
        self.semantic = semantic or DisabledSemanticClassifier()

    def classify(
        self,
        *,
        title: str,
        summary: str | None = None,
        section: str | None = None,
        body: str | None = None,
    ) -> ClassificationResult:
        fields = {
            "title": normalize_text(title).lower(),
            "summary": normalize_text(summary).lower(),
            "section": normalize_text(section).lower(),
            "body": normalize_text(body).lower(),
        }
        matches: list[Match] = []
        excluded: list[str] = []
        total = 0.0

        for field_name, text in fields.items():
            if not text:
                continue
            weight = self.topic.field_weights.get(field_name, 1.0)
            for term in self.topic.positive_terms():
                if term.term.lower() in text:
                    score = term.score * weight
                    matches.append(
                        Match(
                            term=term.term,
                            score=score,
                            strong=not term.ambiguous and term.score >= 3,
                        )
                    )
                    total += score
            for term in self.topic.excluded_terms:
                if term.term.lower() in text:
                    excluded.append(term.term)
                    total += term.score * weight
            for rule in self.topic.combinations:
                all_match = not rule.all or all(term.lower() in text for term in rule.all)
                any_match = not rule.any or any(term.lower() in text for term in rule.any)
                if all_match and any_match:
                    label = "+".join([*rule.all, *(f"({term})" for term in rule.any)])
                    matches.append(Match(term=label, score=rule.score * weight, strong=True))
                    total += rule.score * weight

        matched_terms = unique_preserving_order([match.term for match in matches])
        excluded_terms = unique_preserving_order(excluded)
        strong_positive = any(match.strong for match in matches)
        if excluded_terms and not strong_positive:
            classification = Classification.IRRELEVANT
            total = min(total, self.topic.thresholds.review - 0.1)
        elif total >= self.topic.thresholds.relevant:
            classification = Classification.RELEVANT
        elif total >= self.topic.thresholds.review:
            classification = Classification.REVIEW
        else:
            classification = Classification.IRRELEVANT

        candidate = strong_positive or total >= self.topic.thresholds.candidate
        reason = self._reason(
            classification,
            total,
            matched_terms,
            excluded_terms,
            strong_positive,
            self.topic.label,
        )
        result = ClassificationResult(
            topic=self.topic_name,
            classification=classification,
            topic_score=round(total, 2),
            matched_terms=matched_terms,
            excluded_terms=excluded_terms,
            classification_reason=reason,
            candidate=candidate,
        )
        if self.semantic.enabled and classification == Classification.REVIEW:
            return self.semantic.refine(fields, result)
        return result

    @staticmethod
    def _reason(
        classification: Classification,
        score: float,
        matches: list[str],
        excluded: list[str],
        strong_positive: bool,
        topic_label: str,
    ) -> str:
        positive_text = (
            ", ".join(matches[:6]) if matches else f"뚜렷한 {topic_label} 관련 표현 없음"
        )
        if excluded and not strong_positive:
            return (
                f"제외 문맥({', '.join(excluded[:4])})이 확인되고 {topic_label} 관련 "
                f"강한 문맥이 없어 irrelevant로 판정함(점수 {score:.2f})."
            )
        if classification == Classification.RELEVANT:
            return f"{topic_label} 관련 표현({positive_text})이 충분히 확인됨(점수 {score:.2f})."
        if classification == Classification.REVIEW:
            return (
                f"관련 표현({positive_text})이 있으나 자동 확정 임계값에는 미달하여 "
                f"사람의 검토가 필요함(점수 {score:.2f})."
            )
        suffix = f" 제외 문맥: {', '.join(excluded[:4])}." if excluded else ""
        return f"{topic_label} 관련성이 낮아 irrelevant로 판정함(점수 {score:.2f}).{suffix}"
