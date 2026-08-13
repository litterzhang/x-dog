"""Tests for the config selector."""


from xdog.coding.cli.config_selector import _prompt_api_key, _prompt_provider, config_selector


def test_prompt_provider(monkeypatch):
    inputs = iter(["5", "invalid", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    provider = _prompt_provider()
    assert provider == "anthropic"


def test_prompt_api_key(capsys):
    # _prompt_api_key now just prints env var instructions
    result = _prompt_api_key("anthropic")
    assert result == "anthropic"
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in captured.out


def test_config_selector(monkeypatch, tmp_path):
    # simulate: model, thinking, permission mode, no custom instructions
    inputs = iter(["opus", "high", "ask-all", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    cfg_path = tmp_path / "settings.json"
    cfg = config_selector(config_path=cfg_path)

    assert cfg.default_model == "opus"
    assert cfg.thinking_level == "high"
    assert cfg.permission_mode == "ask-all"
    assert cfg_path.exists()
