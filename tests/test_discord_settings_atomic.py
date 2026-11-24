from core import ui_shell_discord


class _Host(ui_shell_discord._ShellDiscordMixin):
    pass


def test_save_discord_settings_uses_atomic_writer(monkeypatch, tmp_path):
    settings_path = tmp_path / "discord_bot.json"
    monkeypatch.setattr(
        ui_shell_discord, "DISCORD_SETTINGS_FILE", settings_path, raising=False
    )
    calls = []
    monkeypatch.setattr(
        ui_shell_discord,
        "atomic_write_json",
        lambda path, data: calls.append((path, data)),
    )
    settings = {"bot_token": "token", "enabled": True, "channel_id": "123"}

    _Host()._save_discord_settings(settings)

    assert calls == [(settings_path, settings)]
