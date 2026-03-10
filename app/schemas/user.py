from pydantic import BaseModel


class UserMeResponse(BaseModel):
    user_id: int
    employee_no: str
    display_name: str
    roles: list[str]
    team: str | None = None
