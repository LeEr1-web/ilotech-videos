"""IloTech — programme la file Buffer (plan gratuit). Code commun au robot GitHub et au PC.

Le robot GitHub (.github/workflows/remplir-buffer.yml) le lance à 6 h 30 et 18 h 30 (heure de Martinique) : le PC peut
rester éteint. Le PC (studio/outils/buffer_auto.py) met les vidéos et images en ligne dans ce dépôt et les ajoute à
catalogue.json ; ce programme les programme dans Buffer, sans dépasser 10 posts en attente par compte.

    python outils/planif_buffer.py            programme les prochains contenus
    python outils/planif_buffer.py --essai    montre ce qui serait programmé, n'envoie rien

Fichiers : catalogue.json (contenus en ligne, dans l'ordre de passage), etat.json (ce qui est programmé dans Buffer,
partagé avec le PC), cloud.json (dernier passage du robot), journal.txt, reglages.json (heures, comptes, limites).
Clé : variable d'environnement BUFFER_API_KEY (secret du dépôt). Elle n'est jamais écrite dans un fichier.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ICI = pathlib.Path(__file__).resolve().parent.parent  # racine du dépôt
API = "https://api.buffer.com"
MQ = dt.timezone(dt.timedelta(hours=-4))  # Martinique, pas d'heure d'été


def lire(nom, defaut):
    p = ICI / nom
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else defaut


def ecrire(nom, data):
    (ICI / nom).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


CONF = lire("reglages.json", {})


def _journal(msg):
    line = f"{dt.datetime.now(MQ):%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    p = ICI / "journal.txt"
    old = p.read_text(encoding="utf-8").splitlines()[-999:] if p.exists() else []
    p.write_text("\n".join(old + [line]) + "\n", encoding="utf-8")


LOG = _journal  # le PC le remplace par son propre journal


def log(msg):
    LOG(msg)


def key():
    k = os.environ.get("BUFFER_API_KEY")
    if not k and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                k = winreg.QueryValueEx(h, "BUFFER_API_KEY")[0]
        except OSError:
            pass
    if not k:
        raise RuntimeError("clé Buffer absente (secret BUFFER_API_KEY)")
    return k


def gql(query, variables=None):
    req = urllib.request.Request(API, json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key()}"})
    for attempt in range(4):  # Buffer coupe parfois la connexion : on réessaie
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    if out.get("errors"):
        raise RuntimeError(json.dumps(out["errors"], ensure_ascii=False)[:800])
    return out["data"]


def channels():
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        q = "query($o: OrganizationId!) { channels(input: {organizationId: $o}) { id name service } }"
        out += gql(q, {"o": o["id"]})["channels"]
    return out


def liens(it):
    return [CONF["url_hebergement"].rstrip("/") + "/" + f for f in it["fichiers"]]


def slots(after):
    day = after.date()
    while True:
        for hhmm in CONF["heures"]:
            h, m = map(int, hhmm.split(":"))
            t = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=MQ)
            if t > after:
                yield t
        day += dt.timedelta(days=1)


def text_for(service, it):
    return {"tiktok": it["tiktok"], "instagram": it["instagram"], "youtube": it["youtube"]}.get(service, it["facebook"])


def metadata(service, it):
    if it["kind"] == "images":
        # plusieurs images = carrousel automatiquement sur Instagram
        m = {"instagram": {"type": "post", "shouldShareToFeed": True}, "facebook": {"type": "post"},
             "tiktok": {"title": it["titre"]}}.get(service)
    else:
        m = {"instagram": {"type": "reel", "shouldShareToFeed": True},
             "facebook": {"type": "reel"},
             "youtube": {"title": it["yt_title"][:100], "privacy": "public", "categoryId": "28", "madeForKids": False,
                         "notifySubscribers": True},
             "tiktok": {}}.get(service)
    if m is not None and it.get("ia") and service in ("instagram", "tiktok", "youtube"):
        m = dict(m, isAiGenerated=True)  # obligatoire quand la vidéo contient des images générées par IA
    return {service: m} if m else None


def assets(service, it, urls):
    if it["kind"] == "images":
        return [{"image": {"url": u, "metadata": {"altText": it["titre"]}}} for u in urls]
    v = {"url": urls[0]}
    if service in ("instagram", "tiktok"):  # pas de couverture séparée : image de la vidéo à 1,5 s (accroche)
        v["metadata"] = {"thumbnailOffset": CONF["image_couverture_ms"]}
    return [{"video": v}]


MUTATION = """mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""


def pick(rest, when):
    """Heures « vidéo » (reglages.json) = vidéo, les autres = carrousel ou image ; si un type est épuisé, on prend l'autre."""
    want = "video" if when.strftime("%H:%M") in CONF["heures_video"] else "images"
    for it in rest:
        if it["kind"] == want:
            return it
    return rest[0] if rest else None


def compatible(c, it):
    """YouTube (Shorts) ne prend que les vidéos ; les autres comptes prennent tout."""
    return c["service"] != "youtube" or it["kind"] == "video"


def post_one(c, it, due, urls):
    inp = {"channelId": c["id"], "text": text_for(c["service"], it), "schedulingType": "automatic",
           "mode": "customScheduled", "dueAt": due, "assets": assets(c["service"], it, urls)}
    md = metadata(c["service"], it)
    if md:
        inp["metadata"] = md
    r = gql(MUTATION, {"input": inp})["createPost"]
    if "message" in r:
        raise RuntimeError(f"{c['service']} : {r['message']}")
    return r["post"]["id"]


def stories_pour(c, it):
    """Story en plus du post (reglages.json « stories ») : par défaut les vidéos sur Instagram et Facebook.
    La musique est déjà dans la vidéo (les réseaux ne permettent pas d'ajouter leur musique par programme)."""
    st = CONF.get("stories", {"services": ["instagram", "facebook"], "types": ["video"]})
    return c["service"] in st.get("services", []) and it["kind"] in st.get("types", [])


def post_story(c, it, due, urls):
    """Story Instagram ou Facebook : la vidéo entière, ou la première image d'un carrousel."""
    if it["kind"] == "images":
        a = [{"image": {"url": urls[0], "metadata": {"altText": it["titre"]}}}]
    else:
        a = [{"video": {"url": urls[0]}}]
    inp = {"channelId": c["id"], "text": text_for(c["service"], it), "schedulingType": "automatic",
           "mode": "customScheduled", "dueAt": due, "assets": a, "metadata": {c["service"]: dict({"type": "story"}, **({"shouldShareToFeed": False} if c["service"] == "instagram" else {}))}}
    r = gql(MUTATION, {"input": inp})["createPost"]
    if "message" in r:
        raise RuntimeError(f"{c['service']} story : {r['message']}")
    return r["post"]["id"]


def supprimer(post_id):
    gql('mutation($i: PostId!) { deletePost(input:{id:$i}) { __typename } }', {"i": post_id})


def utc(when):
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def completer(etat, chans, now, cat):
    """Un compte ajouté après coup reçoit aussi les contenus déjà programmés à venir."""
    by_id = {it["id"]: it for it in cat}
    for cid, p in sorted(etat["posts"].items(), key=lambda kv: kv[1]["date"]):
        when = dt.datetime.fromisoformat(p["date"])
        it = by_id.get(cid)
        if when <= now + dt.timedelta(minutes=30) or not it:
            continue
        for c in chans:
            if c["id"] in p["canaux"] or not compatible(c, it):
                continue
            try:
                p["canaux"][c["id"]] = post_one(c, it, utc(when), liens(it))
                ecrire("etat.json", etat)
                log(f"OK Buffer {cid} → {c['service']} le {when:%d/%m %H:%M} (complément)")
            except Exception as e:
                log(f"ERREUR complément {cid} → {c['service']} : {e}")
                return [str(e)]
    return []


def remplir(essai=False, limite=None):
    """Programme les prochains contenus du catalogue. Renvoie (nombre programmé, erreurs)."""
    cat = lire("catalogue.json", {"contenus": []})["contenus"]
    etat = lire("etat.json", {"posts": {}})
    now = dt.datetime.now(MQ)
    chans = [c for c in channels() if c["service"] in CONF["services"]]
    if not chans:
        raise RuntimeError("aucun compte Buffer compatible trouvé")
    erreurs = [] if essai else completer(etat, chans, now, cat)
    # en attente = programmé, pas encore passé, stories comprises (YouTube ne reçoit qu'un contenu sur trois : pas limitant)
    pending = {c["id"]: sum((c["id"] in p["canaux"]) + (c["id"] + ":story" in p["canaux"]) for p in etat["posts"].values()
                            if dt.datetime.fromisoformat(p["date"]) > now) for c in chans if c["service"] != "youtube"}
    room = min(CONF["max_en_attente"] - n for n in pending.values()) if pending else CONF["max_en_attente"]
    if limite is not None:
        room = min(room, limite)
    rest = [it for it in cat if it["id"] not in etat["posts"]]
    if room <= 0 or not rest:
        log(f"Buffer : file pleine ou plus rien à programmer ({len(rest)} contenus en ligne en réserve)")
        return 0, erreurs
    if len(rest) < 9:
        log(f"ATTENTION : plus que {len(rest)} contenus en ligne en réserve, il faut allumer le PC (fabrique + réserve)")
    last = max([dt.datetime.fromisoformat(p["date"]) for p in etat["posts"].values()] + [now + dt.timedelta(hours=1)])
    gen = slots(last)
    n = 0
    for _ in range(room):
        when = next(gen)
        it = pick(rest, when)
        if not it:
            break
        targets = [c for c in chans if compatible(c, it)]
        # place dans la file gratuite (10 par compte) : le post + sa story éventuelle
        besoin = {c["id"]: 1 + stories_pour(c, it) for c in targets if c["id"] in pending}
        if any(pending[cid] + k > CONF["max_en_attente"] for cid, k in besoin.items()):
            break
        if essai:
            st = [c["service"] for c in targets if stories_pour(c, it)]
            print(f"[essai] {when:%a %d/%m %H:%M}  {it['kind']:<6} {it['id']}  → {', '.join(c['service'] for c in targets)}"
                  + (f"  + story : {', '.join(st)}" if st else ""))
            for cid, k in besoin.items():
                pending[cid] += k
            rest.remove(it)
            continue
        done = {}
        try:
            for c in targets:
                done[c["id"]] = post_one(c, it, utc(when), liens(it))
                log(f"OK Buffer {it['id']} → {c['service']} le {when:%d/%m %H:%M}")
                if stories_pour(c, it):
                    quand = when + dt.timedelta(minutes=5)
                    try:  # une story refusée ne doit pas bloquer la publication principale
                        done[c["id"] + ":story"] = post_story(c, it, utc(quand), liens(it))
                        log(f"OK Buffer {it['id']} → {c['service']} story le {quand:%d/%m %H:%M}")
                    except Exception as e:
                        log(f"STORY IGNORÉE {it['id']} → {c['service']} : {e}")
            for cid, k in besoin.items():
                pending[cid] += k
            etat["posts"][it["id"]] = {"date": when.isoformat(), "canaux": done}
            ecrire("etat.json", etat)
            rest.remove(it)
            n += 1
        except Exception as e:  # on note et on s'arrête : nouvel essai au prochain passage, sans trou dans le calendrier
            log(f"ERREUR Buffer {it['id']} : {e}")
            for pid in done.values():  # pas de publication à moitié faite (sinon doublon au prochain passage)
                supprimer(pid)
            erreurs.append(str(e))
            break
    return n, erreurs


def main():
    """Passage du robot GitHub : programme, puis note l'heure du passage dans cloud.json (le PC la lit)."""
    essai = "--essai" in sys.argv
    lim = next((int(x.split("=")[1]) for x in sys.argv if x.startswith("--limite=")), None)
    if not os.environ.get("BUFFER_API_KEY"):
        msg = "clé Buffer absente : ajoute le secret BUFFER_API_KEY dans GitHub (voir LISEZMOI.md)"
        log(msg)
        if not essai:
            ecrire("cloud.json", {"dernier_passage": dt.datetime.now(MQ).isoformat(timespec="seconds"), "ok": False, "message": msg})
        return 0  # pas d'alerte e-mail tant que le secret n'est pas posé
    try:
        n, erreurs = remplir(essai, lim)
    except Exception as e:
        n, erreurs = 0, [str(e)]
        log(f"ERREUR : {e}")
    if not essai:
        ecrire("cloud.json", {"dernier_passage": dt.datetime.now(MQ).isoformat(timespec="seconds"), "ok": not erreurs,
                              "programmes": n, "message": "; ".join(erreurs)[:500] or "ok"})
    return 1 if erreurs else 0  # échec → GitHub envoie un e-mail d'alerte


if __name__ == "__main__":
    sys.exit(main())
