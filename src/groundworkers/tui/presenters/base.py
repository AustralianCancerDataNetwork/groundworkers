from __future__ import annotations

from typing import Any, Protocol

from groundskeeping.contracts import (
    DetailView,
    KeyValueView,
    SemanticStatus,
    SurfaceView,
)
from rich.text import Text

type DetailRows = tuple[tuple[str | Text, str], ...]


class SetupPresenter(Protocol):
    """Common shape for setup presenters while the section APIs are still settling."""

    def status(self, *args: Any, **kwargs: Any) -> SemanticStatus: ...

    def landing(self, *args: Any, **kwargs: Any) -> SurfaceView: ...

    def detail(self, *args: Any, **kwargs: Any) -> DetailView | None: ...


class SetupPresenterBase:
    """Default presenter behavior shared by setup sections."""

    def detail(self, *args: Any, **kwargs: Any) -> DetailView | None:
        return None


def key_value_detail(title: str, detail: object) -> KeyValueView | None:
    if detail is None:
        return None
    if isinstance(detail, tuple):
        try:
            rows = tuple((key, str(value)) for key, value in detail)
        except (TypeError, ValueError):
            rows = (("?", str(detail)),)
    elif isinstance(detail, dict):
        rows = tuple((str(key), str(value)) for key, value in detail.items())
    else:
        rows = (("?", str(detail)),)
    return KeyValueView(title=title, rows=rows)


def detail_row(kind: str, message: str) -> tuple[Text, str]:
    return (_detail_marker(kind), message)


def _detail_marker(kind: str) -> Text:
    if kind == "ok":
        return Text("✓", style="green")
    if kind == "warn":
        return Text("!", style="yellow")
    if kind == "fail":
        return Text("✕", style="red")
    return Text("?", style="grey62")
