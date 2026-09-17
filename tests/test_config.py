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
