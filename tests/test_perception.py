"""OCR / recognition / reconstruction / verification tests on synthetic screenshots (no Genshin needed)."""
import cv2
import numpy as np
import pytest

from gcoach.config import ROOT
from gcoach.core.actions import Action
from gcoach.core.enums import PLAYER, ActionType, DieType, Element
from gcoach.core.observed import Observed, ObservedCharacter, ObservedGameState, ObservedSide
from gcoach.ocr.ocr import OCREngine, TemplateDigitOCR, parse_fraction, parse_int, preprocess
from gcoach.screen_capture.capture import detect_game_area, frame_difference
from gcoach.verification.verifier import Verifier
from gcoach.vision.layout import Layout
from gcoach.vision.reconstruct import MatchSetup, Reconstructor
from gcoach.vision.recognizer import ELEMENT_HSV, ENERGY_HSV, OMNI_HSV, Recognizer, hsv_to_bgr
from gcoach.vision.templates import TemplateLibrary

W, H = 1600, 900


def test_parse_helpers():
    assert parse_int("HP 8") == 8
    assert parse_int("1O") == 10
    assert parse_int("—") is None
    assert parse_fraction("8 / 10") == (8, 10)


def test_preprocess_makes_white_text_on_black():
    img = np.full((30, 40, 3), 230, np.uint8)
    cv2.putText(img, "7", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    bw = preprocess(img, 2.0)
    assert bw.mean() < 127 and bw.max() == 255


def test_template_digit_ocr_reads_numbers():
    ocr = TemplateDigitOCR.from_font()
    img = np.zeros((40, 70, 3), np.uint8)
    cv2.putText(img, "10", (4, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
    res = ocr.read(img)
    assert res.value == 10 and res.confidence > 0.5


def test_ocr_disabled_returns_nothing():
    res = OCREngine(enabled=False).read_number(np.zeros((10, 10, 3), np.uint8))
    assert res.value is None and res.confidence == 0


def test_game_area_and_frame_difference():
    img = np.zeros((500, 900, 3), np.uint8)
    img[50:450, 100:800] = (60, 40, 80)
    assert detect_game_area(img) == (100, 50, 700, 400)
    assert frame_difference(img, img) == 0.0
    other = img.copy()
    other[50:450, 100:450] = (200, 200, 200)
    assert frame_difference(img, other) > 0.2


# ----------------------------------------------------------------------------------------
def render_board(layout: Layout, hp, energy, active, aura, dice_faces, dice_count, dead=()):
    """Draw a simplified board following the layout (a stand-in for a real screenshot)."""
    img = np.full((H, W, 3), (40, 26, 30), np.uint8)
    for side in ("player", "opponent"):
        cfg = layout.side(side)
        for slot in range(3):
            card = layout.card_rect(side, slot, W, H, shifted=(active[side] == slot))
            x, y, w, h = card
            fill = (95, 95, 95) if (side, slot) in dead else (150, 60, 170)
            cv2.rectangle(img, (x, y), (x + w, y + h), fill, -1)
            cv2.rectangle(img, (x, y), (x + w, y + h), (225, 225, 240), 2)
            hx, hy, hw, hh = Layout.part(card, layout.data["card_parts"]["hp"])
            cv2.rectangle(img, (hx, hy), (hx + hw, hy + hh), (10, 10, 10), -1)
            cv2.putText(img, str(hp[side][slot]), (hx + 6, hy + hh - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (255, 255, 255), 2, cv2.LINE_AA)
            ex, ey, ew, eh = Layout.part(card, layout.data["card_parts"]["energy"])
            for k in range(energy[side][slot]):
                cy = ey + 10 + k * 16
                cv2.circle(img, (ex + ew // 2, cy), 5, hsv_to_bgr(ENERGY_HSV), -1)
            ax, ay, aw, ah = Layout.part(card, layout.data["card_parts"]["aura"])
            for k, el in enumerate(aura[side][slot]):
                cv2.circle(img, (ax + 12 + 26 * k, ay + ah // 2), 9, hsv_to_bgr(ELEMENT_HSV[el]), -1)
        dx, dy, dw, dh = Layout.to_px(cfg["dice_count"], W, H)
        cv2.rectangle(img, (dx, dy), (dx + dw, dy + dh), (10, 10, 10), -1)
        cv2.putText(img, str(dice_count[side]), (dx + 6, dy + dh - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 255, 255), 2, cv2.LINE_AA)
    cx, cy, cw, ch = Layout.to_px(layout.side("player")["dice_column"], W, H)
    step = ch / 8
    for k, face in enumerate(dice_faces):
        color = hsv_to_bgr(OMNI_HSV if face == "omni" else ELEMENT_HSV[face])
        y0 = int(cy + k * step + 3)
        cv2.rectangle(img, (cx + 3, y0), (cx + cw - 3, int(y0 + step - 6)), color, -1)
    return img


@pytest.fixture
def recognizer(tmp_path):
    layout = Layout.load(ROOT / "assets/layouts/default_16x9.json")
    ocr = OCREngine(enabled=False)
    ocr.backend = TemplateDigitOCR.from_font()
    return Recognizer(layout, TemplateLibrary(tmp_path), ocr), layout


def test_screen_recognition_on_synthetic_board(recognizer):
    rec, layout = recognizer
    hp = {"player": [7, 8, 4], "opponent": [6, 5, 9]}
    energy = {"player": [2, 2, 1], "opponent": [1, 2, 0]}
    active = {"player": 0, "opponent": 1}
    aura = {"player": [[], [], []], "opponent": [[], ["hydro"], []]}
    faces = ["omni", "pyro", "pyro", "pyro", "cryo", "cryo", "anemo"]
    img = render_board(layout, hp, energy, active, aura, faces, {"player": 7, "opponent": 5})
    obs = rec.recognize(img).observed
    assert obs.player.active_index.value == 0 and obs.opponent.active_index.value == 1
    assert [c.hp.value for c in obs.player.characters] == hp["player"]
    assert [c.hp.value for c in obs.opponent.characters] == hp["opponent"]
    assert [c.energy.value for c in obs.player.characters] == energy["player"]
    assert obs.opponent.characters[1].aura.value == ["hydro"]
    assert obs.player.dice_count.value == 7 and obs.opponent.dice_count.value == 5
    assert obs.player.dice.value == {"omni": 1, "pyro": 3, "cryo": 2, "anemo": 1}
    assert obs.player.characters[0].identity.value is None  # no templates -> UNKNOWN, never guessed


def test_dead_card_detected(recognizer):
    rec, layout = recognizer
    img = render_board(layout, {"player": [7, 0, 4], "opponent": [6, 5, 9]},
                       {"player": [0, 0, 0], "opponent": [0, 0, 0]}, {"player": 0, "opponent": 0},
                       {"player": [[], [], []], "opponent": [[], [], []]}, [], {"player": 0, "opponent": 8},
                       dead={("player", 1)})
    obs = rec.recognize(img).observed
    assert obs.player.characters[1].alive.value is False
    assert obs.player.characters[0].alive.value is True


# ----------------------------------------------------------------------------------------
def _obs(hp_p, hp_o, active_p=0, active_o=0, dice=8, opp_dice=8, conf=0.95):
    obs = ObservedGameState()
    for name, hps, act, dc in (("player", hp_p, active_p, dice), ("opponent", hp_o, active_o, opp_dice)):
        side = ObservedSide()
        for i, h in enumerate(hps):
            ch = ObservedCharacter(i)
            ch.hp = Observed.known(h, conf)
            ch.energy = Observed.known(0, conf)
            ch.alive = Observed.known(h > 0, conf)
            ch.aura = Observed.known([], conf)
            side.characters.append(ch)
        side.active_index = Observed.known(act, conf)
        side.dice_count = Observed.known(dc, conf)
        setattr(obs, name, side)
    obs.round = Observed.known(1, conf)
    obs.active_player = Observed.known("player", conf)
    return obs


def test_reconstruction_uses_setup_and_reports_confidence(kb):
    r = Reconstructor(kb)
    setup = MatchSetup(["Дилюк", "Кэйа", "Сахароза"], ["Фишль", "Син Цю", "Гань Юй"])
    res = r.build(_obs([10, 10, 10], [10, 10, 12]), None, setup)
    assert res.state.player.characters[0].id == "Дилюк" and res.state.opponent.characters[2].max_hp == 12
    assert res.confidence >= 0.44  # dice faces unknown -> capped, reported honestly
    assert res.confidence_map["player.characters[0].identity"]["knowledge"] == "inferred"
    assert res.state.player.dice.hidden == 8


def test_reconstruction_without_identity_is_uncertain(kb):
    res = Reconstructor(kb).build(_obs([10, 10, 10], [10, 10, 10]))
    assert res.confidence < 0.5
    assert any("identity" in p for p in res.problems)


def test_verification_confirms_and_detects_mismatch(engine, kb, duel):
    s = duel()
    sk = next(x for x in kb.character("Дилюк").skills if x.type.value == "skill")
    rec = Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id)
    after = engine.apply(s, rec).state
    v = Verifier(engine)
    ok = v.verify(s, after, rec)
    assert ok.status == "CONFIRMED" and ok.matched[0].startswith("Элементальный навык")
    nothing = v.verify(s, s.clone(), rec)
    assert nothing.status == "NO_CHANGE"
    weird = s.clone()
    weird.opponent.characters[0].hp = 1
    weird.player.characters[2].hp = 2
    weird.round = 4
    bad = v.verify(s, weird, rec)
    assert bad.status in ("UNCERTAIN", "PARTIAL")
