"""One JSON shape for every error response:

    {"detail": "<message a person can read>", "code": "<machine-readable>"}

Validation failures add `"errors": [{"field": "body.email", "message": "..."}]`. Submitted values
are never echoed back, so a rejected password can't reappear in a response or a log.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error_body(status_code: int, detail: str, code: str | None = None, **extra: Any) -> dict:
    return {"detail": detail, "code": code or _code_for(status_code), **extra}


def not_implemented(task: str) -> HTTPException:
    """For a route whose request and response models are final but whose body isn't written yet.

    `task` is the backlog item that implements it, e.g. "B-301"; the 501 names it so the frontend
    knows what it's waiting for. Raise it after the login and ownership checks.
    """
    return HTTPException(status.HTTP_501_NOT_IMPLEMENTED, f"Not implemented yet ({task})")


def _code_for(status_code: int) -> str:
    """404 -> "not_found", 409 -> "conflict", 401 -> "unauthorized", ..."""
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        return "error"
    return phrase.lower().replace(" ", "_").replace("-", "_")


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    # Covers our HTTPExceptions and Starlette's own (unknown route 404, wrong method 405).
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return JSONResponse(
        error_body(exc.status_code, detail), status_code=exc.status_code, headers=exc.headers
    )


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        error_body(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Request validation failed",
            "validation_error",
            errors=errors,
        ),
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    # The server still logs the traceback; the client only learns that something broke.
    return JSONResponse(
        error_body(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error", "internal_error"
        ),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_error)
