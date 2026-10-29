from pydantic import BaseModel, ConfigDict


class BaseTypeConfig(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
