from app.config import Settings


def set_required_environment(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("API_KEY", "test-api-key-that-is-at-least-32-characters")


def test_single_admin_id_from_environment(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_IDS", "123456789")

    settings = Settings(_env_file=None)

    assert settings.admin_ids == frozenset({123456789})


def test_multiple_admin_ids_from_environment(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_IDS", "123456789,987654321")

    settings = Settings(_env_file=None)

    assert settings.admin_ids == frozenset({123456789, 987654321})


def test_json_style_admin_ids_from_environment(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_IDS", "[123456789,987654321]")

    settings = Settings(_env_file=None)

    assert settings.admin_ids == frozenset({123456789, 987654321})


def test_bundled_payment_poster_is_resolved_from_project_root(monkeypatch) -> None:
    set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.payment_qr_path.name == "payment_qr.png"
    assert settings.payment_qr_path.is_absolute()
    assert settings.payment_qr_path.is_file()


def test_payment_queue_expires_after_fifteen_minutes_by_default(monkeypatch) -> None:
    set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.payment_expiry_minutes == 30
    assert settings.topup_expiry_minutes == 15
    assert settings.admin_test_mode_enabled is False


def test_admin_test_mode_can_be_explicitly_enabled(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_TEST_MODE_ENABLED", "true")

    settings = Settings(_env_file=None)

    assert settings.admin_test_mode_enabled is True


def test_aba_listener_settings_are_normalized(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("PAYMENT_CHECK_BOT_TOKEN", "123456:CHECK_TOKEN")
    monkeypatch.setenv("ABA_PAYMENT_GROUP_ID", "-1001234567890")
    monkeypatch.setenv("ABA_PAYMENT_BOT_USERNAME", "@PayWayByABA_bot")

    settings = Settings(_env_file=None)

    assert settings.payment_check_bot_token is not None
    assert settings.aba_payment_group_id == -1001234567890
    assert settings.aba_payment_bot_username == "paywaybyaba_bot"


def test_payment_reader_settings_are_complete(monkeypatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("PAYMENT_READER_API_ID", "123456")
    monkeypatch.setenv("PAYMENT_READER_API_HASH", "test-api-hash")
    monkeypatch.setenv("PAYMENT_READER_SESSION", "test-session")

    settings = Settings(_env_file=None)

    assert settings.payment_reader_configured is True
    assert settings.payment_reader_api_id == 123456
