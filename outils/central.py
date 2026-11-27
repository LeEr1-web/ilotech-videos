"""Envoie le bilan du robot à ILO Central (ilo-tech.com/central/, menu Connexions et robots).

Lit cloud.json, etat.json et catalogue.json (écrits par planif_buffer.py) et envoie un lot de relevés.
Le jeton vient du secret GitHub CENTRAL_JETON ; sans lui, rien n'est envoyé et le robot continue normalement.
N'échoue jamais : ILO Central injoignable ne doit pas faire échouer le robot.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent
URL = "https://mocntvrlwdndyuiwrxen.supabase.co/rest/v1/rpc/central_envoyer_lot"
CLE_PUBLIQUE = "sb_publishable_vqYMz6iZVHoSAcZiiOsUow_ORMzCzNq"  # clé publique Supabase (sites/commun/config.js), pas un secret


def lire(nom, defaut):
    p = ICI / nom
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else defaut
    except ValueError:
        return defaut


def releves():
    cloud = lire("cloud.json", {})
    posts = lire("etat.json", {"posts": {}}).get("posts", {})
    cat = lire("catalogue.json", {"contenus": []}).get("contenus", [])
    maintenant = dt.datetime.now(dt.timezone.utc)
    a_venir = 0
    for p in posts.values():
        try:
            if dt.datetime.fromisoformat(p["date"]) > maintenant:
                a_venir += 1
        except (KeyError, ValueError):
            pass
    reserve = sum(1 for it in cat if it.get("id") not in posts)
    lot = [
        {"type": "stat", "cle": "posts_programmes_passage", "valeur": int(cloud.get("programmes") or 0)},
        {"type": "stat", "cle": "posts_a_venir", "valeur": a_venir},
        {"type": "stat", "cle": "reserve_contenus", "valeur": reserve},
    ]
    if cloud.get("ok") is False:
        lot.append({"type": "alerte", "cle": "robot-buffer-erreur",
                    "donnees": {"message": str(cloud.get("message") or "erreur inconnue")[:500]}})
    if reserve < 14:
        lot.append({"type": "alerte", "cle": "reserve-basse",
                    "donnees": {"message": f"Il reste {reserve} contenus en réserve : prévoir une nouvelle fournée."}})
    return lot


def envoyer(jeton, lot):
    corps = json.dumps({"p_jeton": jeton, "p_releves": lot}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=corps, method="POST", headers={
        "apikey": CLE_PUBLIQUE, "Authorization": "Bearer " + CLE_PUBLIQUE,
        "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main():
    jeton = os.environ.get("CENTRAL_JETON", "").strip()
    lot = releves()
    if "--essai" in sys.argv:
        print(json.dumps(lot, ensure_ascii=False, indent=1))
        return 0
    if not jeton:
        print("ILO Central : secret CENTRAL_JETON absent, rien envoyé")
        return 0
    try:
        print(f"ILO Central : {len(lot)} relevés envoyés (HTTP {envoyer(jeton, lot)})")
    except Exception as e:  # noqa: BLE001 — ne jamais faire échouer le robot
        print(f"ILO Central injoignable : {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
