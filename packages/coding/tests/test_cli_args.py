from click.testing import CliRunner
from xdog.coding.cli.args import cli


def test_cli_list_models():
    runner = CliRunner()
    result = runner.invoke(cli, ['--list-models'])
    # Should not raise exception
    assert result.exit_code == 0

def test_cli_args_parsing(mocker):
    mock_run = mocker.patch("xdog.coding.main.run_agent")

    runner = CliRunner()
    result = runner.invoke(cli, ['-m', 'sonnet', '--thinking-level', 'high', '--print', '-p', 'hello'])

    assert result.exit_code == 0
    mock_run.assert_called_once()

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["overrides"] == {"model": "sonnet", "thinking_level": "high"}
    assert call_kwargs["print_mode"] is True
    assert call_kwargs["prompt"] == "hello"


def test_cli_thinking_level_choices(mocker):
    mock_run = mocker.patch("xdog.coding.main.run_agent")
    runner = CliRunner()

    for level in ("off", "minimal", "low", "medium", "high", "xhigh"):
        result = runner.invoke(cli, ["--thinking-level", level])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["overrides"]["thinking_level"] == level

    result = runner.invoke(cli, ["--thinking-level", "deep"])
    assert result.exit_code != 0


def test_cli_permission_mode(mocker):
    mock_run = mocker.patch("xdog.coding.main.run_agent")
    result = CliRunner().invoke(cli, ["--permission-mode", "ask-all"])

    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["overrides"]["permission_mode"] == "ask-all"
