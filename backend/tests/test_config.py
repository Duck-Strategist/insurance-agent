import pytest
from pydantic import ValidationError

from app.core.config import AppSettings, AuthSettings, Settings


def test_prod_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="AUTH__JWT_SECRET"):
        Settings(app=AppSettings(env="prod"))


def test_prod_accepts_custom_secret() -> None:
    settings = Settings(
        app=AppSettings(env="prod"),
        auth=AuthSettings(jwt_secret="a-real-production-secret-with-enough-length"),  # type: ignore[arg-type]
    )
    assert settings.app.env == "prod"


def test_short_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="至少 32"):
        Settings(auth=AuthSettings(jwt_secret="short"))  # type: ignore[arg-type]
