from typing import Literal
from pydantic import BaseModel, ConfigDict


class Staff(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    username: str
    first_name: str
    last_name: str
    role: Literal["Staff", "Admin"]
    is_active: bool

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"
