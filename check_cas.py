#!/usr/bin/env python3
"""
Surveille la rubrique "Communiqués de presse" du site du TAS/CAS
et envoie un mail quand un nouveau communiqué apparaît.

Exécuté toutes les 15 min par GitHub Actions (voir .github/workflows/check.yml).
L'état (communiqués déjà vus) est stocké dans seen.json, commité dans le dépôt.

Variables d'environnement :
  GMAIL_USER          adresse Gmail utilisée pour envoyer (obligatoire)
  GMAIL_APP_PASSWORD  "mot de passe d'application" Google à 16 caractères (obligatoire)
  MAIL_TO             destinataire (défaut : saufianas03@gmail.com)
  CAS_URL             page à surveiller (défaut : https://www.tas-cas.org/fr)
  TEST_EMAIL=1        envoie un mail de test avec le dernier communiqué, sans toucher à l'état
"""
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

URL = os.getenv("CAS_URL", "https://www.tas-cas.org/fr")
STATE_FILE = Path(os.getenv("STATE_FILE", "seen.json"))
MAIL_TO = os.getenv("MAIL_TO", "saufianas03@gmail.com")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TEST_EMAIL = os.getenv("TEST_EMAIL") == "1"

# Mots-clés : si un titre en contient un, le sujet du mail est marqué [CAN] (insensible à la casse)
KEYWORDS = ["afcon", "can 2025", "africa cup", "coupe d'afrique", "morocco", "maroc", "senegal", "sénégal"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cas-press-watcher/1.0)"}


def fetch_releases():
    """Télécharge la page et renvoie la liste des communiqués (du plus récent au plus ancien)."""
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    base = f"{urlparse(URL).scheme}://{urlparse(URL).netloc}/"
    releases = []
    # Structure du site : <div class="list-media-release"><a href="..."> ... .mediaTitle / .mediaDate / .mediaLanguage
    for a in soup.select("div.list-media-release > a"):
        title_el = a.select_one(".mediaTitle")
        if not title_el or not a.get("href"):
            continue
        # Les href contiennent des "\" Windows et des espaces -> on normalise
        path = a["href"].replace("\\", "/")
        link = urljoin(base, quote(path, safe="/:%&=?#"))
        date_el = a.select_one(".mediaDate")
        lang_el = a.select_one(".mediaLanguage")
        releases.append({
            "id": link,  # l'URL du PDF sert d'identifiant unique
            "title": " ".join(title_el.get_text().split()),
            "date": date_el.get_text(strip=True) if date_el else "",
            "lang": lang_el.get_text(strip=True) if lang_el else "",
            "link": link,
        })
    return releases


def send_mail(subject, body):
    """Envoie un mail via le SMTP de Gmail."""
    if not (GMAIL_USER and GMAIL_APP_PASSWORD):
        sys.exit("GMAIL_USER / GMAIL_APP_PASSWORD manquants")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = MAIL_TO
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def format_item(it):
    return f"- {it['title']}\n  {it['date']} ({it['lang']})\n  {it['link']}"


def main():
    releases = fetch_releases()

    # Garde-fou : si le parsing ne trouve rien (site en maintenance, structure modifiée...),
    # on échoue bruyamment plutôt que de croire qu'il n'y a "rien de nouveau".
    # Le job GitHub passe en rouge et GitHub t'envoie un mail d'échec.
    if not releases:
        sys.exit("Aucun communiqué trouvé : la structure de la page a peut-être changé.")

    if TEST_EMAIL:
        send_mail("[TEST] Surveillance TAS/CAS opérationnelle",
                  "Ceci est un mail de test. Dernier communiqué actuellement en ligne :\n\n"
                  + format_item(releases[0]))
        print("Mail de test envoyé.")
        return

    # Premier lancement : on mémorise l'existant comme référence, sans envoyer de mails
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps([r["id"] for r in releases], indent=2))
        print(f"Initialisation : {len(releases)} communiqués mémorisés, aucun mail envoyé.")
        return

    seen = set(json.loads(STATE_FILE.read_text()))
    new = [r for r in releases if r["id"] not in seen]

    if not new:
        print("Rien de nouveau.")
        return

    flagged = any(k in r["title"].lower() for r in new for k in KEYWORDS)
    prefix = "[CAN] " if flagged else ""
    if len(new) == 1:
        subject = f"{prefix}TAS/CAS – Nouveau communiqué : {new[0]['title']}"
    else:
        subject = f"{prefix}TAS/CAS – {len(new)} nouveaux communiqués : {new[0]['title']}"
    body = "Nouveau(x) communiqué(s) de presse sur le site du TAS :\n\n" \
           + "\n\n".join(format_item(r) for r in new) \
           + f"\n\nPage : {URL}\n"

    # On envoie le mail AVANT d'enregistrer l'état : si l'envoi échoue, on réessaiera au prochain passage
    send_mail(subject, body)
    print(f"{len(new)} nouveau(x) communiqué(s), mail envoyé.")

    # On conserve les identifiants déjà vus + les nouveaux (limité à 500 pour ne pas grossir indéfiniment)
    all_ids = [r["id"] for r in releases] + [i for i in seen if i not in {r["id"] for r in releases}]
    STATE_FILE.write_text(json.dumps(all_ids[:500], indent=2))


if __name__ == "__main__":
    main()
