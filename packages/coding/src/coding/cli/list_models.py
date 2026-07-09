"""List available models in a user-friendly table."""

from __future__ import annotations


def list_models_command() -> None:
    """Print a table of all registered models and exit."""
    import ai

    runtime = ai.load()
    models = sorted(
        (m for m in runtime.models() if m.model_type == "chat"),
        key=lambda m: (m.provider, m.id),
    )

    if not models:
        print("No models available. Run 'xdog-ai login copilot' first.")
        return

    header = f"  {'Model ID':<45s}  {'Provider':<15s}  {'Protocol':<20s}"
    separator = "  " + "-" * 84

    print("\nAvailable models:\n")
    print(header)
    print(separator)
    for m in models:
        print(f"  {m.id:<45s}  {m.provider:<15s}  {m.api:<20s}")
    print()
