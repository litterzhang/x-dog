from xdog.ai.types import Model, ModelCost, Usage
from xdog.ai.utils.cost import calculate_cost


def test_calculate_cost():
    model = Model(
        id="dummy",
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
    )
    usage = Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000)
    cost = calculate_cost(model, usage)

    assert cost.input == 3.0
    assert cost.output == 15.0
    assert cost.cache_read == 0.3
    assert cost.cache_write == 3.75
    assert cost.total == 22.05
