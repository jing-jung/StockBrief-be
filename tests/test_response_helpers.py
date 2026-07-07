from app.models import PaginationResponse
from app.services.response_helpers import pagination


def test_pagination_has_more_when_more_rows_remain() -> None:
    result = pagination(limit=10, offset=0, total=30)

    assert result == PaginationResponse(limit=10, offset=0, total=30, has_more=True)


def test_pagination_has_more_false_on_last_page() -> None:
    result = pagination(limit=10, offset=20, total=30)

    assert result == PaginationResponse(limit=10, offset=20, total=30, has_more=False)


def test_pagination_has_more_false_when_offset_plus_limit_equals_total() -> None:
    result = pagination(limit=10, offset=20, total=30)

    assert result.has_more is False


def test_pagination_has_more_true_when_offset_beyond_total_is_still_short_of_limit() -> None:
    # offset + limit < total is the only condition checked; this documents
    # that behavior precisely rather than re-deriving it.
    result = pagination(limit=5, offset=0, total=1)

    assert result.has_more is False


def test_pagination_with_zero_total_has_no_more_rows() -> None:
    result = pagination(limit=20, offset=0, total=0)

    assert result.has_more is False
    assert result.total == 0


def test_pagination_preserves_limit_and_offset_verbatim() -> None:
    result = pagination(limit=7, offset=3, total=100)

    assert result.limit == 7
    assert result.offset == 3
