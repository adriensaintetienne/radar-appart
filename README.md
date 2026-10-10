# 🏠 Radar Appart

Surveille automatiquement les annonces d'achat d'appartements (Paris Est) toutes les 2 heures, les filtre
selon vos critères, les classe, et vous alerte sur WhatsApp. Tout est gratuit.

```
 Portails (SeLoger, Leboncoin, PAP, Logic-Immo, Figaro…) ──► alertes email ──► boîte Gmail dédiée ─┐
 Bien'ici (API directe) ───────────────────────────────────────────────────────────────────────────┤
 Sites d'agences (Espaces Atypiques…) ─────────────────────────────────────────────────────────────┼─► Robot (GitHub Actions, toutes les 2 h)
 Recherche web « filet de sécurité » (1×/jour) ──► nouveaux sites découverts ─────────────────────┘        │
                                                                                                           ▼
                                  filtrage + coût total + score + dédoublonnage + santé des sources
                                                                                                           │
                                         ┌─────────────────────────────────┬───────────────────────────────┘
                                         ▼                                 ▼
                         WhatsApp (CallMeBot), email en secours   Tableau de bord (carte, zone, critères, suivi)
```

**Deux dépôts GitHub :**
- `radar-appart` (**public**) : le code et le tableau de bord. Aucune donnée personnelle dedans.
- `radar-appart-data` (**privé**) : vos critères, votre zone, les annonces et votre suivi.

**Ce qui est lu directement / par email** (testé en conditions réelles le 10/10/2026) :
- Bien'ici et Espaces Atypiques : collectés directement sur leurs sites.
- SeLoger, Leboncoin, Logic-Immo, PAP : leurs sites bloquent les robots, ils passent donc par les alertes email.

Un dépôt public donne des minutes de calcul GitHub illimitées et un tableau de bord gratuit (GitHub Pages).
Vos données restent dans le dépôt privé, lisible uniquement avec votre jeton.

---

## Installation (environ 45 minutes, une seule fois)

### Étape 1 — L'adresse email des alertes
Deux options au choix.

**Option A — votre Gmail avec `+appart` (le plus simple)**
1. Votre adresse d'alertes devient `votre.adresse+appart@gmail.com`. Les emails arrivent dans votre boîte habituelle.
2. Dans Gmail, créez un **filtre** : ⚙ › *Voir tous les paramètres* › *Filtres et adresses bloquées* › *Créer un filtre*,
   puis À : `votre.adresse+appart@gmail.com` › *Créer un filtre* › cochez **Ignorer la boîte de réception**
   et **Appliquer le libellé** › *Nouveau libellé* : `Radar` › *Créer un filtre*.
   Les alertes sont rangées dans « Radar » sans encombrer votre boîte.
3. Activez la **validation en deux étapes** si ce n'est pas déjà fait : https://myaccount.google.com/signinoptions/two-step-verification
4. Créez un **mot de passe d'application** : https://myaccount.google.com/apppasswords (nom : « Radar »).
   Google affiche 16 lettres : **notez-les**, c'est `GMAIL_APP_PASSWORD`.

> Le robot ne lit que le libellé « Radar » et que les emails adressés à `+appart`. Le mot de passe d'application
> donne techniquement accès à toute la boîte : il est stocké chiffré dans les secrets GitHub et vous pouvez le
> révoquer à tout moment sur la même page Google. Si cela vous gêne, choisissez l'option B.

**Option B — une adresse Gmail dédiée**
1. Créez un compte Gmail, par exemple `radar.appart.xxx@gmail.com`.
2. Activez la validation en deux étapes, puis créez un mot de passe d'application (étapes 3 et 4 ci-dessus).

### Étape 2 — Les alertes sur les portails
Sur chaque site, créez un compte **avec l'adresse d'alertes de l'étape 1** (`…+appart@gmail.com` ou l'adresse dédiée) et enregistrez une recherche avec alerte email.
Si un site refuse le `+` dans l'adresse (c'est le cas de SeLoger), utilisez votre adresse normale pour ce site, puis ajoutez un filtre Gmail
**De : `nom-du-site.com`** › *Ignorer la boîte de réception* + *Appliquer le libellé* (le même que celui de l'étape 1).
Gardez des critères **un peu plus larges** que les vôtres : le radar affine ensuite.

Critères à saisir partout : **Achat · Appartement (+ loft si proposé) · Paris 10e, 11e, 19e, 20e · prix max 1 350 000 € ·
surface min 80 m²**. Choisissez la fréquence la plus rapide proposée (instantanée ou quotidienne).

| Site | Où créer l'alerte |
|---|---|
| SeLoger | lancer la recherche → « Créer une alerte » |
| Leboncoin | Immobilier › Ventes → recherche → « Sauvegarder la recherche » + notifications par email |
| PAP | recherche → « Recevoir les nouvelles annonces par email » |
| Logic-Immo | recherche → « Créer une alerte » |
| Figaro Immobilier | recherche → « Créer une alerte » |
| Belles Demeures | recherche → « Créer une alerte » |
| Bien'ici | (déjà collecté directement, mais une alerte en double ne fait pas de mal) |
| Jinka | agrégateur : créez une alerte, il couvre beaucoup de sites |
| Espaces Atypiques | inscrivez-vous à leurs alertes / newsletter si le site le propose |

> Certains sites envoient d'abord un email de confirmation : ouvrez la boîte Gmail dédiée et cliquez dessus.

### Étape 3 — Les dépôts GitHub
Claude a préparé le code. Il reste à créer les deux dépôts :
1. https://github.com/new → nom `radar-appart`, **Public**, sans README → *Create repository*.
2. https://github.com/new → nom `radar-appart-data`, **Private**, sans README → *Create repository*.
3. Prévenez Claude : il envoie le code dans le premier dépôt et les fichiers de départ dans le second.

### Étape 4 — Le jeton GitHub (la « clé » du robot et du tableau de bord)
1. https://github.com/settings/personal-access-tokens/new
2. Nom : `radar-appart`. Expiration : la plus longue proposée (notez la date dans votre agenda pour la renouveler).
3. *Repository access* → **Only select repositories** → cochez `radar-appart` **et** `radar-appart-data`.
4. *Permissions › Repository permissions* :
   - **Contents** : *Read and write*
   - **Actions** : *Read and write* (pour le bouton ▶ Lancer)
5. *Generate token* → copiez le jeton (`github_pat_…`). Il ne sera plus affiché.

### Étape 5 — Les secrets du robot
Dans le dépôt `radar-appart` : **Settings › Secrets and variables › Actions › New repository secret**. Créez :

| Nom | Valeur |
|---|---|
| `DATA_REPO_TOKEN` | le jeton de l'étape 4 |
| `GMAIL_ADDRESS` | votre adresse Gmail (sans le `+appart`) ou l'adresse dédiée |
| `GMAIL_APP_PASSWORD` | les 16 lettres de l'étape 1 |
| `GMAIL_ALIAS` | *(option A, facultatif)* à ne créer **que si tous les sites ont accepté** l'adresse `+appart` ; sinon le robot se limite au libellé |
| `GMAIL_FOLDER` | *(option A seulement)* `Radar` |
| `GEMINI_API_KEY` | *(facultatif)* clé gratuite sur https://aistudio.google.com/apikey pour l'analyse IA |
| `BRAVE_API_KEY` | *(facultatif)* clé gratuite sur https://brave.com/search/api/ : active la recherche web « filet de sécurité » (les moteurs gratuits bloquent les robots) |

### Étape 6 — Activer le tableau de bord
Dans `radar-appart` : **Settings › Pages** → *Source* : **Deploy from a branch** → *Branch* : `main`, dossier **/docs** → *Save*.
Au bout d'une à deux minutes, le tableau de bord est en ligne à l'adresse `https://VOTRE-COMPTE.github.io/radar-appart/`.
Ajoutez-la à l'écran d'accueil de vos téléphones.

### Étape 7 — WhatsApp (chacun de vous deux)
1. Suivez la page officielle https://www.callmebot.com/blog/free-api-whatsapp-messages/ : ajoutez le numéro
   CallMeBot indiqué à vos contacts, puis envoyez-lui sur WhatsApp le message `I allow callmebot to send me messages`.
2. Vous recevez en réponse votre **clé personnelle** (apikey).

### Étape 8 — Premier réglage dans le tableau de bord
1. Ouvrez le tableau de bord, puis collez le **jeton** (les noms des dépôts sont pré-remplis) → *Se connecter*.
2. **Zone** : tracez votre zone sur la carte → *Enregistrer la zone*.
3. **Alertes** : ajoutez chaque personne (numéro au format `+336…` et clé CallMeBot) → *Tester* → *Enregistrer*.
   Ajoutez aussi vos emails personnels (secours si WhatsApp échoue).
4. **Critères** : relisez ; tout est déjà prérempli avec ce que vous avez demandé.
5. Cliquez sur **▶ Lancer**. Après quelques minutes, vous recevez un message « le radar est en route ✅ ».
   Ce premier passage n'envoie pas d'alerte pour chaque annonce déjà en ligne : elles sont toutes dans le tableau de bord.

Le robot tourne ensuite seul **toutes les 2 heures de 7 h à 23 h**.

---

## Utilisation au quotidien

- **WhatsApp** : un message par nouvelle annonce, baisse de prix ou remise en ligne, avec le score, le coût total estimé et le lien.
- **Annonces** : les biens classés par score. Pour chacun, vous pouvez choisir un statut de suivi
  (⭐ intéressant, 📞 contacté, 📅 visite, ✖ pas pour nous) et ajouter des notes. Le suivi est partagé entre vous deux.
  « Pas pour nous » coupe les alertes sur ce bien.
- **Écartées** : chaque annonce rejetée affiche son motif. Vous pouvez vérifier que le filtre ne se trompe pas.
- **Critères / Alertes / Zone** : modifiables à tout moment, pris en compte au passage suivant.

### Statuts
| Statut | Signification |
|---|---|
| ✅ Correspond | tous les critères connus sont respectés |
| 🔎 À vérifier | rien d'éliminatoire, mais une info manque (étage, ascenseur, chambres…) ou la position est en bordure de zone |
| 💶 Négociable | coût total jusqu'à 5 % au-dessus du budget |
| Écartée | un critère éliminatoire n'est pas respecté (le motif est affiché) |

### Coût total estimé
`prix affiché + 7,5 % de notaire + travaux`. Les travaux dépendent de l'état détecté dans l'annonce :
1 500 €/m² si « à rénover », 500 €/m² si « à rafraîchir », 0 si « refait », 500 €/m² si l'état est inconnu
(tous ces montants sont modifiables dans Critères).

---

## Ne rien rater : contrôle de couverture

1. **Sources** : l'état de chaque source (dernier passage, dernière annonce reçue, erreurs). Le radar vous prévient
   sur WhatsApp si une source tombe en panne (3 passages en erreur) ou devient muette (par exemple, plus aucun email
   SeLoger depuis 72 h, ce qui arrive quand une alerte expire sur le site).
2. **Contrôle croisé** : si un bien d'une agence surveillée apparaît sur un portail mais pas dans la collecte directe
   de son site, c'est signalé.
3. **Vérifier** (pendant les deux premières semaines) : chaque fois que vous trouvez une annonce par vous-mêmes, collez
   son lien dans l'onglet *Vérifier*. Le tableau de bord mesure le **taux de couverture réel**, site par site.
4. **Nouveaux sites découverts** (nécessite la clé `BRAVE_API_KEY`) : la recherche web quotidienne liste les sites immobiliers non surveillés.
   Un clic suffit pour en ajouter un comme agence.

**Limite :** les biens « off-market » (proposés par les agences à leurs clients avant toute publication) ne sont
visibles par aucun outil. Inscrivez-vous directement auprès des agences du quartier. Le nom de l'agence affiché sur
chaque annonce (et la recherche par mot-clé) vous aide à repérer les plus actives dans votre zone.

---

## En cas de problème

| Symptôme | Solution |
|---|---|
| « Jeton invalide » sur le tableau de bord | le jeton a expiré : refaites l'étape 4, puis mettez à jour le secret `DATA_REPO_TOKEN` et le tableau de bord (⚙) |
| Source « gmail » en erreur | mot de passe d'application révoqué : refaites l'étape 1.3 et mettez à jour `GMAIL_APP_PASSWORD` |
| Source silencieuse | ouvrez le site concerné, vérifiez que l'alerte existe toujours |
| Agence en erreur ou 0 fiche trouvée | le site a changé de présentation : ajustez la page de liste ou le motif dans Sources (ou demandez à Claude) |
| Plus aucun passage | onglet **Actions** du dépôt `radar-appart` : réactivez le workflow s'il a été désactivé |
| Trop d'alertes | Alertes → augmentez le score minimum, ou décochez « À vérifier » |

---

## Partie technique

- `radar/` : le robot en Python (`python -m radar.main --data ./data [--dry-run] [--only bienici]`).
  - `sources/` : `gmail_alerts.py` (emails des portails), `bienici.py` (API JSON), `agencies.py` (sites d'agences), `websearch.py`
  - `analyze.py` : critères éliminatoires, coût total, score · `store.py` : dédoublonnage, historique des prix, retraits
  - `health.py` : santé des sources · `notify.py` : WhatsApp / email · `ai.py` : Gemini (facultatif)
- `docs/` : le tableau de bord (HTML/JS statique, GitHub Pages). Il lit et écrit le dépôt privé via l'API GitHub.
- `data-template/` : contenu initial du dépôt privé.
- `tests/` : `pip install pytest && python -m pytest`.
- Les journaux du robot (onglet Actions, publics) n'affichent que des compteurs, jamais le contenu des annonces.
