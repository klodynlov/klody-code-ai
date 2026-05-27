# Politique de sécurité

## Versions supportées

Seule la branche `main` reçoit des correctifs de sécurité. Les anciennes
versions ne sont pas maintenues — utiliser le `HEAD` de `main`.

## Signaler une vulnérabilité

**Ne pas ouvrir d'issue publique** pour une vulnérabilité de sécurité.

Utiliser le canal privé GitHub :

1. Aller sur https://github.com/klodynlov/klody-code-ai/security/advisories
2. Cliquer sur **"Report a vulnerability"**
3. Décrire le problème : impact, étapes de reproduction, version concernée

J'accuse réception sous **72h** et publie le correctif + l'advisory dans
les **30 jours** quand la vulnérabilité est confirmée.

## Périmètre

Klody est un agent local exécutant du code sur la machine de l'utilisateur.
Sont considérés comme vulnérabilités :

- **Évasion de sandbox** : un prompt qui ferait écrire/lire hors de
  `PROJECT_ROOT` malgré [`tools/file_manager.py`](tools/file_manager.py).
- **Injection / exécution arbitraire** non médiée par la confirmation
  utilisateur dans [`tools/terminal.py`](tools/terminal.py).
- **Fuite de secrets** : un chemin permettant à un tiers de lire `.env`
  ou les clés malgré la blocklist.
- **Désérialisation non sûre** dans les imports d'historique LLM.
- **MCP server** : qu'un client MCP puisse provoquer une exécution
  hors-périmètre via les 8 outils exposés.

Ne sont **pas** considérés comme vulnérabilités :

- Le fait que l'utilisateur exécute volontairement du code via `terminal` —
  c'est la fonction principale de l'outil.
- Les comportements du modèle LLM (hallucinations, mauvaises décisions de
  routing) qui n'entraînent pas d'évasion de sandbox.

## Bonnes pratiques pour les contributeurs

- **Commits signés** : configurer GPG ou SSH signing
  (`git config commit.gpgsign true`).
- **Pas de secrets en clair** : utiliser `.env` (gitignored) ou des secrets
  GitHub Actions.
- **Dépendances** : passer par les PRs Dependabot, vérifier les CVE.
- **Tests sécurité** : ajouter un test dans `tests/test_security.py` pour
  toute nouvelle surface d'attaque.

## Repo jacking

Le compte GitHub `klodynlov` est l'unique propriétaire des dépôts officiels :

- https://github.com/klodynlov/klody-code-ai
- https://github.com/klodynlov/klody-ui

Tout fork ou clone hébergé ailleurs n'est pas un release officiel.
