from src.safety.guard import SafetyConfig, SafetyGuard


def test_blocks_off_domain_navigation() -> None:
    guard = SafetyGuard(SafetyConfig(allowed_domains=["localhost"]))
    verdict = guard.check("navigate", {"url": "https://evil.example/login"})
    assert verdict.allowed is False


def test_flags_irreversible_click() -> None:
    guard = SafetyGuard(SafetyConfig(block_irreversible=False))
    verdict = guard.check("click", {"name": "Submit Decision"})
    assert verdict.allowed is True
    assert verdict.requires_confirmation is True
    assert verdict.is_irreversible is True


def test_blocks_irreversible_when_configured() -> None:
    guard = SafetyGuard(SafetyConfig(block_irreversible=True))
    verdict = guard.check("click", {"name": "Submit Decision"})
    assert verdict.allowed is False


def test_requires_sensitive_flag_on_ssn() -> None:
    guard = SafetyGuard()
    verdict = guard.check("type", {"name": "SSN", "value": "123-45-6789"})
    assert verdict.allowed is False
    ok = guard.check("type", {"name": "SSN", "value": "123-45-6789", "sensitive": True})
    assert ok.allowed is True
