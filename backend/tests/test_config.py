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


def test_blank_rag_keys_are_treated_as_missing() -> None:
    from app.core.config import RagSettings
    from app.rag.factory import RagNotConfiguredError, check_configured

    rag = RagSettings(mineru_token="", dashscope_api_key="   ")  # type: ignore[arg-type]
    assert rag.mineru_token is None and rag.dashscope_api_key is None
    with pytest.raises(RagNotConfiguredError) as exc:
        check_configured(rag)
    assert exc.value.missing == ["RAG__MINERU_TOKEN", "RAG__DASHSCOPE_API_KEY"]


def test_tests_are_isolated_from_local_env_file() -> None:
    """即使本机 backend/.env 填了真实密钥，测试里也必须视为未配置、且不碰真实 data/ 目录。"""
    from app.core.config import DEFAULT_DATA_DIR, get_settings

    rag = get_settings().rag
    assert rag.mineru_token is None and rag.dashscope_api_key is None
    assert rag.data_dir != DEFAULT_DATA_DIR
