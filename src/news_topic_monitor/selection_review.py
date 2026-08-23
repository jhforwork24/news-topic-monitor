from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classifier import RuleClassifier
from .models import (
    ArticleRecord,
    Classification,
    EditorialCandidate,
    EditorialPlan,
    EditorialSection,
)

# (topic name in config/topics.yml, display label)
REVIEW_TOPICS: tuple[tuple[str, str], ...] = (
    ("disability_rights", "I절 장애정책·장애인운동"),
    ("labor_care_poverty", "II절 노동·돌봄·빈곤"),
)


@dataclass(frozen=True)
class ScoredArticle:
    article: ArticleRecord
    topic_score: float
    classification: Classification
    selected: bool = False


@dataclass(frozen=True)
class NearMissTopic:
    topic_label: str
    relevant_threshold: float
    articles: list[ScoredArticle]


@dataclass(frozen=True)
class SelectionReview:
    report_date: str
    candidate_pool: list[ScoredArticle]
    near_miss: list[NearMissTopic]


def build_selection_review(
    *,
    articles: list[ArticleRecord],
    plan: EditorialPlan,
    candidates: list[EditorialCandidate],
    topics_path: Path,
    report_date: str,
    candidate_pool_size: int = 30,
    near_miss_count: int = 5,
) -> SelectionReview:
    """Rank the day's full collection by disability_rights score, and separately
    surface the near-miss articles (closest below each topic's `relevant`
    cutline) across both topics — independent of what the editor selected.

    Every field used here (topic_score, classification, matched articles) is
    already persisted on `articles`, or cheap to recompute (pure string
    matching) for the topic that isn't persisted (labor_care_poverty). No new
    data collection or storage is required.
    """
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    selected_urls = {
        candidate_by_id[candidate_id].canonical_url
        for issue in plan.issues
        if issue.section == EditorialSection.DISABILITY
        for candidate_id in issue.candidate_ids
        if candidate_id in candidate_by_id
    }

    ranked = sorted(articles, key=lambda article: article.topic_score, reverse=True)
    candidate_pool = [
        ScoredArticle(
            article=article,
            topic_score=article.topic_score,
            classification=article.classification,
            selected=article.canonical_url in selected_urls,
        )
        for article in ranked[:candidate_pool_size]
    ]

    near_miss: list[NearMissTopic] = []
    for topic_name, label in REVIEW_TOPICS:
        classifier = RuleClassifier(topics_path, topic=topic_name)
        threshold = classifier.topic.thresholds.relevant
        below_cutline: list[ScoredArticle] = []
        for article in articles:
            if topic_name == "disability_rights":
                score, classification = article.topic_score, article.classification
            else:
                result = classifier.classify(
                    title=article.title,
                    summary=article.summary or article.monitor_summary,
                    section=article.section,
                )
                score, classification = result.topic_score, result.classification
            if score < threshold:
                below_cutline.append(
                    ScoredArticle(article=article, topic_score=score, classification=classification)
                )
        below_cutline.sort(key=lambda item: item.topic_score, reverse=True)
        near_miss.append(
            NearMissTopic(
                topic_label=label,
                relevant_threshold=threshold,
                articles=below_cutline[:near_miss_count],
            )
        )

    return SelectionReview(
        report_date=report_date,
        candidate_pool=candidate_pool,
        near_miss=near_miss,
    )
