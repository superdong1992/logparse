from backend.extensions.products.current.models import ParseResult as ProductParseResult
from backend.models import ParseResult as LegacyParseResult


def test_legacy_model_path_is_a_compatibility_facade() -> None:
    assert LegacyParseResult is ProductParseResult
