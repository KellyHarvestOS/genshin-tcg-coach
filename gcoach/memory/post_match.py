"""Post-match analysis built from MatchMemory (deterministic; an AI provider may add commentary)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .memory import MatchMemory

MISTAKE_THRESHOLD = 0.05  # win-probability drop that counts as a mistake
KEY_SPREAD = 0.08  # spread between best and worst option that makes a decision "key"


def analyse(memory: MatchMemory, result: str | None = None) -> dict[str, Any]:
    result = result or memory.result or "unknown"
    decisions = [d for d in memory.decisions if d.get("options")]
    key, good, mistakes, missed = [], [], [], []
    wasted_dice: list[int] = []
    for d in decisions:
        opts = d["options"]
        best = max(o["win_probability"] for o in opts)
        worst = min(o["win_probability"] for o in opts)
        actual = d.get("actual")
        if best - worst >= KEY_SPREAD:
            key.append(d)
        if not actual:
            continue
        rec_label = d["recommended"]["label"]
        if actual["label"] == rec_label:
            good.append(d)
        else:
            awp = actual.get("win_probability")
            if awp is not None and best - awp >= MISTAKE_THRESHOLD:
                d = dict(d)
                d["loss"] = round(best - awp, 3)
                mistakes.append(d)
        if best >= 0.99 and (actual.get("win_probability") or 0) < 0.99:
            missed.append({"round": d["round"], "text": f"Была выигрывающая линия: {rec_label}"})
        if actual["type"] == "end_round":
            if d.get("burst_ready"):
                missed.append({"round": d["round"], "text": "Раунд завершён с готовым взрывом стихии"})
            if d.get("dice_left", 0) >= 3:
                wasted_dice.append(d["dice_left"])

    reactions = Counter()
    enemy_reactions = Counter()
    switches = deaths_us = deaths_them = 0
    for e in memory.events:
        if e["kind"] == "reaction":
            (reactions if e["side"] == "opponent" else enemy_reactions)[e["text"].replace("Реакция: ", "")] += 1
        elif e["kind"] == "switch" and e["side"] == "player" and not e.get("data", {}).get("forced"):
            switches += 1
        elif e["kind"] == "death":
            if e["side"] == "player":
                deaths_us += 1
            else:
                deaths_them += 1

    improvements = []
    if mistakes:
        improvements.append("Сверяйтесь с рекомендацией в ключевых моментах: самые дорогие ошибки отмечены выше.")
    if wasted_dice:
        improvements.append(f"В {len(wasted_dice)} раундах осталось ≥3 неиспользованных кубиков — планируйте траты заранее.")
    if not reactions:
        improvements.append("Реакции почти не использовались — чаще готовьте ауру под элемент следующего персонажа.")
    if deaths_us > deaths_them:
        improvements.append("Персонажи погибали чаще, чем у противника — раньше уводите раненых из-под удара.")
    if any("взрыв" in m["text"] for m in missed):
        improvements.append("Не завершайте раунд с полной энергией — взрыв стихии часто решает обмен.")
    if not improvements:
        improvements.append("Решения близки к оптимальным — продолжайте в том же духе.")

    def brief(d: dict[str, Any]) -> dict[str, Any]:
        return {"round": d["round"], "recommended": d["recommended"]["label"],
                "actual": (d.get("actual") or {}).get("label"), "loss": d.get("loss")}

    return {
        "match_id": memory.match_id,
        "result": result,
        "key_decisions": [brief(d) for d in key[:6]],
        "good_decisions": [brief(d) for d in good[:8]],
        "mistakes": [brief(d) for d in sorted(mistakes, key=lambda x: -x["loss"])[:6]],
        "missed_opportunities": missed[:6],
        "resource_management": {
            "decisions": len(decisions),
            "followed_recommendations": len(good),
            "rounds_with_wasted_dice": len(wasted_dice),
            "avg_wasted_dice": round(sum(wasted_dice) / len(wasted_dice), 1) if wasted_dice else 0,
        },
        "reaction_management": {"ours": dict(reactions), "opponent": dict(enemy_reactions)},
        "positioning": {"switches": switches, "our_defeated": deaths_us, "their_defeated": deaths_them},
        "future_improvements": improvements,
        "deck": memory.deck,
    }


RESULT_RU = {"player": "Победа", "opponent": "Поражение", "draw": "Ничья", "unknown": "Не определён"}


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Разбор партии {report['match_id']}", "", f"## MATCH RESULT\n{RESULT_RU.get(report['result'], report['result'])}", ""]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"## {title}")
        lines.extend(f"- {i}" for i in items) if items else lines.append("- —")
        lines.append("")

    fmt = lambda d: f"Раунд {d['round']}: рекомендовано «{d['recommended']}», сыграно «{d['actual'] or '—'}»" + (
        f" (−{round(d['loss'] * 100)}% к шансу победы)" if d.get("loss") else "")
    section("KEY DECISIONS", [fmt(d) for d in report["key_decisions"]])
    section("GOOD DECISIONS", [fmt(d) for d in report["good_decisions"]])
    section("MISTAKES", [fmt(d) for d in report["mistakes"]])
    section("MISSED OPPORTUNITIES", [f"Раунд {m['round']}: {m['text']}" for m in report["missed_opportunities"]])
    rm = report["resource_management"]
    section("RESOURCE MANAGEMENT", [
        f"Решений проанализировано: {rm['decisions']}, совпало с советом: {rm['followed_recommendations']}",
        f"Раундов с потерянными кубиками (≥3): {rm['rounds_with_wasted_dice']}, в среднем {rm['avg_wasted_dice']}",
    ])
    rx = report["reaction_management"]
    section("REACTION MANAGEMENT", [
        "Ваши реакции: " + (", ".join(f"{k} ×{v}" for k, v in rx["ours"].items()) or "нет"),
        "Реакции противника: " + (", ".join(f"{k} ×{v}" for k, v in rx["opponent"].items()) or "нет"),
    ])
    pos = report["positioning"]
    section("POSITIONING", [f"Смен персонажа: {pos['switches']}",
                            f"Побеждено ваших: {pos['our_defeated']}, противника: {pos['their_defeated']}"])
    section("FUTURE IMPROVEMENTS", report["future_improvements"])
    if report.get("ai_commentary"):
        lines += ["## AI COMMENTARY", report["ai_commentary"], ""]
    return "\n".join(lines)


def save_report(memory: MatchMemory, report: dict[str, Any]) -> Path:
    import json
    folder = memory.root / memory.match_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    path = folder / "report.md"
    path.write_text(to_markdown(report), encoding="utf-8")
    return path
