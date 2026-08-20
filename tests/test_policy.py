from pathlib import Path

from news_topic_monitor.policy import (
    load_briefing_policy,
    load_source_registry,
    validate_policy_contract,
)


def test_stable_policy_files_define_exact_census_and_reverse_search_sets() -> None:
    root = Path(__file__).parents[1]
    registry = load_source_registry(root / "config" / "source-registry.yaml")
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    validate_policy_contract(registry, policy)

    assert policy.publication.owner == "github_editorial_publish"
    assert policy.publish_gate.disability_press_census_required == [
        "beminor",
        "ablenews",
        "theindigo",
    ]
    assert len(policy.publish_gate.designated_reverse_search_required) == 9
    assert all(
        registry.sources[source].tier == "designated_reverse_search"
        for source in policy.publish_gate.designated_reverse_search_required
    )
    assert registry.gap_detectors["naver_api_hub"].original_validation_replacement is False
