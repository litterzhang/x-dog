
from tui.autocomplete import (
    AutocompleteEngine,
    CallbackCompletionProvider,
    CompletionItem,
    StaticCompletionProvider,
)


def test_callback_provider_get_completions():
    def my_callback(prefix: str):
        return [CompletionItem(text=f"{prefix}_result")]

    provider = CallbackCompletionProvider(callback=my_callback)

    res = provider.get_completions("test")
    assert len(res) == 1
    assert res[0].text == "test_result"

def test_engine_basic_completion():
    items = [
        CompletionItem(text="apple"),
        CompletionItem(text="application"),
        CompletionItem(text="banana"),
    ]
    engine = AutocompleteEngine()
    engine.add_provider(StaticCompletionProvider(items=items))

    results = engine.complete("app")
    assert len(results) == 2
    assert results[0].item.text == "apple"
    assert results[1].item.text == "application"

