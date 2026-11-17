from src.discovery.agent import DiscoveryConfig, infer_max_steps
from src.goal import preserve_dollar_amounts


def test_git_bash_eats_dollar_5000() -> None:
    eaten = (
        "Open application APP-1001293 from the queue, approve it for 000 "
        "with officer notes 'Score and DTI within policy'"
    )
    restored = preserve_dollar_amounts(eaten)
    assert "$5000" in restored
    assert "for 000" not in restored


def test_git_bash_eats_dollar_5_comma_000() -> None:
    assert preserve_dollar_amounts("approve it for ,000 with notes") == (
        "approve it for $5,000 with notes"
    )


def test_leaves_real_amounts_alone() -> None:
    text = "approve it for $5000 with officer notes"
    assert preserve_dollar_amounts(text) == text


def test_discovery_config_restores_goal() -> None:
    cfg = DiscoveryConfig(
        goal="approve it for 000 with notes",
        entry_url="http://localhost:3000/queue.html",
        allowed_domain="localhost",
    )
    assert cfg.goal == "approve it for $5000 with notes"


def test_new_application_gets_higher_max_steps() -> None:
    assert infer_max_steps("lookup member", "http://localhost:3000/member-lookup.html") == 30
    assert infer_max_steps("submit a new credit card application", "http://localhost:3000/application.html") == 80
    cfg = DiscoveryConfig(
        goal="Submit a new credit card application for member 1042381",
        entry_url="http://localhost:3000/application.html",
        allowed_domain="localhost",
    )
    assert cfg.max_steps == 80
