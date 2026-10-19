# Vidéos IloTech

Hébergement des vidéos et images publiées sur les réseaux d'IloTech (Martinique), et robot qui les programme dans Buffer.

## Le robot : publication sans PC

- `.github/workflows/remplir-buffer.yml` tourne tous les jours à **6 h 30 et 18 h 30** (heure de Martinique).
- Il lit `catalogue.json` (contenus en ligne, styles en alternance) et programme les suivants dans Buffer : 7 créneaux
  par jour (`reglages.json`), 10 posts d'avance au maximum par compte (plan gratuit). Il note tout dans `etat.json` et
  `journal.txt`, et l'heure de son passage dans `cloud.json`.
- Le PC d'IloTech met les nouveaux contenus en ligne quand il est allumé (tâche Windows de 7 h et 19 h). Tant que le
  catalogue a de l'avance, le PC peut rester éteint. Si le robot ne tourne pas, le PC programme Buffer lui-même.
- Le robot et le PC utilisent le même programme (`outils/planif_buffer.py`) et le même état (`etat.json`) : pas de doublon.

## À faire une seule fois : donner la clé Buffer au robot

1. Sur GitHub, ouvre ce dépôt → **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret** : nom `BUFFER_API_KEY`, valeur = ta clé Buffer (Buffer → Settings → API).
3. Onglet **Actions** → **Remplir Buffer** → **Run workflow** pour un premier passage.

La clé reste secrète : elle n'apparaît ni dans les fichiers ni dans les journaux. Si un passage échoue, GitHub envoie un
e-mail. GitHub met le robot en pause après 60 jours sans activité sur le dépôt : chaque nouvelle vidéo remet le compteur
à zéro.
