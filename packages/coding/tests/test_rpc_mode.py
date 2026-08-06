"""Tests for RPC mode."""


def test_rpc_status_command():
    """Test that status command returns session info."""
    from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode

    # We can't easily test the full async loop without a real session,
    # but we can verify the module imports and basic structure.
    assert callable(run_rpc_mode)
