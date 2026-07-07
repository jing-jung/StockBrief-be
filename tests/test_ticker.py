import pytest
from fastapi import HTTPException

from app.ticker import validate_ticker


@pytest.mark.parametrize(
    "ticker",
    ["005930", "000660", "000000", "999999"],
)
def test_validate_ticker_accepts_six_digit_tickers(ticker: str) -> None:
    assert validate_ticker(ticker) is None


@pytest.mark.parametrize(
    "ticker",
    [
        "",
        "12345",
        "1234567",
        "ABCDEF",
        "12345A",
        "12 345",
        "005930 ",
        " 005930",
        "-05930",
    ],
)
def test_validate_ticker_rejects_invalid_formats(ticker: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_ticker(ticker)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "INVALID_TICKER",
        "message": "Ticker must be a 6-digit Korean stock ticker.",
        "details": [{"field": "ticker", "reason": "invalid_format"}],
    }
