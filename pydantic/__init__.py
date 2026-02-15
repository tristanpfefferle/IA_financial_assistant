"""Minimal local stub for pydantic used in offline scaffolding environments.

Replace with real `pydantic` dependency in connected environments.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


@dataclass
class _FieldInfo:
    default: Any = ...
    default_factory: Callable[[], Any] | None = None


def Field(default: Any = ..., **kwargs: Any) -> Any:
    return _FieldInfo(default=default, default_factory=kwargs.get("default_factory"))


class BaseModel:
    def __init__(self, **data: Any) -> None:
        annotations = getattr(self, "__annotations__", {})
        for name in annotations:
            class_value = getattr(self.__class__, name, ...)
            default_factory = None
            if isinstance(class_value, _FieldInfo):
                default_factory = class_value.default_factory
                class_value = class_value.default

            if name in data:
                value = data[name]
            elif default_factory is not None:
                value = default_factory()
            elif class_value is not ...:
                value = class_value
            else:
                raise TypeError(f"Missing required field: {name}")

            if name == "amount" and isinstance(value, str):
                try:
                    value = Decimal(value)
                except Exception:
                    pass
            setattr(self, name, value)

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()
