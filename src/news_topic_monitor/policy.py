from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class PolicyConfigurationError(ValueError):
    """Stable registry or briefing policy is absent, invalid, or inconsistent."""


REQUIRED_DISABILITY_CENSUS = frozenset({"beminor", "ablenews", "theindigo"})
REQUIRED_REVERSE_SEARCH = frozenset(
    {
        "labortoday",
        "newscham",
        "chosun",
        "joongang",
        "donga",
        "khan",
        "hani",
        "ohmynews",
        "pressian",
    }
)


class CensusPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    page_size: int = 100
    requires_window_boundary: bool = False


class SourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    tier: str
    domains: list[str] = Field(default_factory=list)
    census: CensusPolicy | None = None
    discovery_routes: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)


class GapDetectorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    endpoint: str | None = None
    original_validation_replacement: bool
    required_for_publication: bool


class SourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    sources: dict[str, SourcePolicy]
    gap_detectors: dict[str, GapDetectorPolicy]

    @model_validator(mode="after")
    def require_unique_domains_for_designated_sources(self) -> SourceRegistry:
        for source, policy in self.sources.items():
            if (
                policy.tier in {"disability_press_census", "designated_reverse_search"}
                and not policy.domains
            ):
                raise ValueError(f"{source} requires at least one registered domain")
        return self

    def source_for_url_host(self, host: str | None) -> str | None:
        normalized = (host or "").lower().removeprefix("www.")
        for source, policy in self.sources.items():
            if any(domain.lower().removeprefix("www.") == normalized for domain in policy.domains):
                return source
        return None


class PublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    notion_single_writer: bool
    publish_empty_briefing: bool


class ReportWindowPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    start_hour: int
    duration_hours: int


class GapDetectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str]
    maximum_results_per_query: int = 100


class ProgressiveEventsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detection_terms: list[str]
    final_state_terms: list[str]


class PublishGatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disability_press_census_required: list[str]
    designated_reverse_search_required: list[str]
    allow_explicit_reverse_search_degraded: bool
    require_core_article_body: bool
    require_final_state_complete: bool
    validator_fatal_errors_max: int
    unclassified_failures_max: int


class FailureReportingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notion_section: str
    required_fields: list[str]

    @model_validator(mode="after")
    def require_machine_report_fields(self) -> FailureReportingPolicy:
        if set(self.required_fields) != {"원인", "대체경로", "결과", "다음조치"}:
            raise ValueError("failure reporting fields must be 원인·대체경로·결과·다음조치")
        return self


class BriefingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    publication: PublicationPolicy
    report_window: ReportWindowPolicy
    gap_detection: GapDetectionPolicy
    progressive_events: ProgressiveEventsPolicy
    publish_gate: PublishGatePolicy
    failure_reporting: FailureReportingPolicy


def load_source_registry(path: Path) -> SourceRegistry:
    try:
        return SourceRegistry.model_validate(_load_yaml(path))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise PolicyConfigurationError(f"invalid source registry {path.name}: {exc}") from exc


def load_briefing_policy(path: Path) -> BriefingPolicy:
    try:
        return BriefingPolicy.model_validate(_load_yaml(path))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise PolicyConfigurationError(f"invalid briefing policy {path.name}: {exc}") from exc


def validate_policy_contract(registry: SourceRegistry, policy: BriefingPolicy) -> None:
    census = policy.publish_gate.disability_press_census_required
    reverse = policy.publish_gate.designated_reverse_search_required
    errors: list[str] = []
    if len(census) != len(set(census)):
        errors.append("disability census source list contains duplicates")
    if len(reverse) != len(set(reverse)):
        errors.append("reverse-search source list contains duplicates")
    if set(census) != REQUIRED_DISABILITY_CENSUS:
        errors.append("disability census must contain the required exact 3 sources")
    if set(reverse) != REQUIRED_REVERSE_SEARCH:
        errors.append("reverse search must contain the required exact 9 sources")
    for source in census:
        source_policy = registry.sources.get(source)
        if source_policy is None:
            errors.append(f"census source is missing from registry: {source}")
        elif source_policy.tier != "disability_press_census" or source_policy.census is None:
            errors.append(f"census source contract is incomplete: {source}")
    for source in reverse:
        source_policy = registry.sources.get(source)
        if source_policy is None:
            errors.append(f"reverse-search source is missing from registry: {source}")
        elif source_policy.tier != "designated_reverse_search":
            errors.append(f"reverse-search source has the wrong tier: {source}")
    if policy.publication.owner not in {
        "claude_editorial_bridge",
        "github_editorial_publish",
        "deterministic_fallback",
    }:
        errors.append("publication owner is not recognized")
    if errors:
        raise PolicyConfigurationError("; ".join(errors))


def _load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)
