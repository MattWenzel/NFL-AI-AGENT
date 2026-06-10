"""Provider endpoint response models."""

from pydantic import BaseModel


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    models: list[str]
    default_model: str
    available: bool
    context_window: int
