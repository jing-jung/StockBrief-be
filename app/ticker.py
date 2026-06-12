import re

from fastapi import HTTPException


def validate_ticker(ticker: str) -> None:
    if re.fullmatch(r"\d{6}", ticker):
        return
    raise HTTPException(
        status_code=400,
        detail={
            "code": "INVALID_TICKER",
            "message": "Ticker must be a 6-digit Korean stock ticker.",
            "details": [{"field": "ticker", "reason": "invalid_format"}],
        },
    )
