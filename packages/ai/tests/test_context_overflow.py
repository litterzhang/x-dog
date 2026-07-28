from ai.types import Context, Model, UserMessage
from ai.utils.overflow import estimate_context_tokens, is_context_overflow


def test_context_overflow_detection():
    """Short text doesn't overflow; long text does."""
    model = Model(id="dummy", context_window=100)

    short = Context(messages=(UserMessage(content="a" * 10),))
    assert not is_context_overflow(short, model)

    long = Context(messages=(UserMessage(content="a" * 500),))
    assert is_context_overflow(long, model)


def test_estimate_context_tokens():
    text = "abcd" * 25  # 100 chars -> 25 tokens + overhead
    context = Context(messages=(UserMessage(content=text),))
    assert estimate_context_tokens(context) == 29
