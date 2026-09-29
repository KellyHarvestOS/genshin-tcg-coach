"""Voice assistant: spoken phrases and the TTS service (no network access in tests)."""
from __future__ import annotations

import pytest

from gcoach.config import load_config
from gcoach.core.actions import Action
from gcoach.core.enums import ActionType, PLAYER
from gcoach.coach.scenarios import load_scenario
from gcoach.knowledge.base import default_kb
from gcoach.rules.engine import RulesEngine
from gcoach.voice.speech import action_phrase, speech_text
from gcoach.voice.tts import TTSError, VoiceService


@pytest.fixture(scope="module")
def engine():
    return RulesEngine(default_kb(), auto_choose_on_death=True)


def test_status_phrases(engine):
    assert speech_text(engine, None, {"status": "waiting_opponent"}) == "Ход противника."
    assert speech_text(engine, None, {"status": "game_over", "message": "Победа!"}) == "Победа!"
    assert "распознать" in speech_text(engine, None, {"status": "uncertain_state"})
    assert speech_text(engine, None, None) == ""


def test_action_phrases(engine):
    s = load_scenario(engine.kb, "reaction_setup")
    name = engine.char_name(s.player.active)
    assert action_phrase(engine, s, Action(ActionType.NORMAL_ATTACK, PLAYER)) == f"{name}: обычная атака"
    assert action_phrase(engine, s, Action(ActionType.END_ROUND, PLAYER)) == "Завершайте раунд"
    other = next(i for i, c in enumerate(s.player.characters) if i != s.player.active_index)
    assert action_phrase(engine, s, Action(ActionType.SWITCH_CHARACTER, PLAYER, target=other)) == \
        f"Смена персонажа: {engine.char_name(s.player.characters[other])}"


def test_reason_is_short_and_skips_jargon(engine):
    s = load_scenario(engine.kb, "reaction_setup")
    rec = {"status": "ok", "action": Action(ActionType.END_ROUND, PLAYER).to_dict(),
           "why": ["Улучшает позицию: энергия", "Вызывает реакцию «Таяние» (+2 урона)"]}
    assert speech_text(engine, s, rec) == "Завершайте раунд. Вызывает реакцию «Таяние»."
    rec["why"] = ["Побеждает в партии прямо сейчас"]
    assert speech_text(engine, s, rec).endswith("Это победа!")


def test_google_voice_without_key_uses_free_neural_voice(tmp_path, monkeypatch):
    monkeypatch.delenv("GCOACH_GOOGLE_TTS_API_KEY", raising=False)
    cfg = load_config()
    cfg.GOOGLE_TTS_API_KEY = ""
    cfg.TTS_VOICE = "ru-RU-Chirp3-HD-Aoede"  # old-style id -> google engine
    monkeypatch.setattr(cfg, "path", lambda v: tmp_path / v)
    v = VoiceService(cfg)
    assert v.settings["voice"] == "google:ru-RU-Chirp3-HD-Aoede"
    assert v.public()["provider"] == "edge" and v.public()["has_key"] is False
    used = []
    monkeypatch.setattr(VoiceService, "_edge", staticmethod(lambda text, voice, rate: used.append(voice) or b"mp3"))
    assert v.synthesize("Завершайте раунд.") == b"mp3" and used == ["ru-RU-SvetlanaNeural"]


def test_neural_voice_failure_is_reported(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "path", lambda v: tmp_path / v)
    v = VoiceService(cfg)

    def boom(*a):
        raise OSError("offline")
    monkeypatch.setattr(VoiceService, "_edge", staticmethod(boom))
    with pytest.raises(TTSError) as err:
        v.synthesize("Ход противника.")
    assert err.value.status == 502


def test_voice_settings_persist_and_hide_key(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "path", lambda v: tmp_path / v)
    v = VoiceService(cfg)
    pub = v.update({"enabled": False, "voice": "google:ru-RU-Chirp3-HD-Kore", "rate": 5, "api_key": " secret "})
    assert pub == {**pub, "enabled": False, "voice": "google:ru-RU-Chirp3-HD-Kore", "rate": 1.6, "provider": "google",
                   "has_key": True}
    assert "secret" not in str(pub)
    again = VoiceService(cfg)
    assert again.api_key == "secret" and again.public()["enabled"] is False


def test_cached_audio_is_served_without_network(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "path", lambda v: tmp_path / v)
    v = VoiceService(cfg)
    monkeypatch.setattr(VoiceService, "_edge", staticmethod(lambda *a: b"ID3-audio"))
    assert v.synthesize("Ход противника.") == b"ID3-audio"
    monkeypatch.setattr(VoiceService, "_edge", staticmethod(lambda *a: (_ for _ in ()).throw(AssertionError)))
    assert v.synthesize("Ход  противника.") == b"ID3-audio"  # same phrase -> disk cache
