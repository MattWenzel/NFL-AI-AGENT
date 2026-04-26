"""Wire schemas shared across multiple feature areas."""

from pydantic import BaseModel


class OkResponse(BaseModel):
    """Generic acknowledgment for mutations whose only signal is success."""

    ok: bool = True
