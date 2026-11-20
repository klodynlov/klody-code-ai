# Actualiser LibraryBrain depuis Klody

Exemple : « Recherche des sources officielles sur le Code de la route et ajoute
les fiches pertinentes dans LibraryBrain. »

Le parcours natif est :

1. `library_web_search(query, official_only=True)` : rechercher des URL officielles.
2. `library_import_source(url, official_only=True)` : importer le document intégral.
3. `library_source_status(book_id)` : lire URL, dates, versions et état d'indexation.
4. `library_refresh_source(book_id)` : vérifier et actualiser cette source.

`library_import_source` et `library_refresh_source` demandent la validation de
l'utilisateur (téléchargement web + nouvelle version indexée) ; la recherche et le
statut passent librement. Avec le routage progressif, les quatre outils arrivent
avec le groupe `library` (« LibraryBrain », « sources », « Code de la route »…).

Les recherches de droit routier filtrent automatiquement les domaines officiels.
Pour un autre domaine documentaire, `official_only=False` permet les URL publiques.
Les données sont téléchargées par LibraryBrain ; le modèle n'envoie pas de prose
générée comme contenu de source. Le token existant `LIBRARYBRAIN_TOKEN` est réutilisé : il doit valoir `api_token`
du `config.yaml` de LibraryBrain, sinon chaque appel répond 401.

La source garde le même identifiant entre les versions. Les anciens instantanés
restent archivés ; seuls les passages courants entrent dans la recherche. Une
erreur d'acquisition garde la version précédente. Les anciens livres locaux sont
distincts : ajouter une fiche ne met pas à jour un code complet déjà présent.

La disponibilité des passages et des vecteurs est annoncée séparément. Citer
l'URL et la date de récupération sans assimiler cette date à une date d'entrée
en vigueur. Un blocage 403 de Légifrance doit être annoncé comme un échec.

Pré-requis : services Klody et LibraryBrain redémarrés après installation, migration
LibraryBrain `027_web_sources.sql` appliquée au démarrage. Les modèles restent
locaux ; seuls la recherche et les téléchargements documentaires utilisent le web.

Voir aussi `library-brain/docs/sources-web.md` pour les API et la sauvegarde des archives.

## Validation réelle du 15 septembre 2026

- Recherche officielle : 5 résultats via le repli Service Public après un blocage DuckDuckGo.
- Fiche `F20443` importée dans LibraryBrain, `book_id=26829` : 3 passages,
  3 vecteurs, job `21653` terminé. Date déclarée de la fiche : 23 juin 2026.
- Nouvelle récupération : `unchanged`, une seule révision, aucun doublon.
- Fiche vérifiée dans le navigateur après rechargement : URL et dates visibles.
- Modèle Klody testé via son API WebSocket : appel réel à `library_source_status`
  avec `book_id=26829`, résultat reçu et tour terminé (session `7b6b1928`).
- Suites ciblées : 180 tests LibraryBrain et 96 tests Klody réussis ; lint et
  vérification des différences sans erreur. Les scénarios couvrent aussi les
  redirections locales refusées, les téléchargements incomplets, le remplacement
  atomique des passages/vecteurs et la conservation de la version après échec.

Les traces brutes de cette validation ne sont pas versionnées. Le Code de la route intégral n'a
pas été actualisé : Légifrance renvoie HTTP 403 sur l'accès direct testé.

## Reprise du 20 novembre 2026

Le repli Service Public visait `/particuliers/recherche`, devenu une page
« Démarches et outils » : aucune fiche n'en sortait. Il interroge maintenant
`/particuliers/recherche-generale` (3 fiches officielles pour « Code de la route
vitesse maximale autorisée », dont F19460). DuckDuckGo renvoyait une page
anti-bot ce jour-là : le repli a donc été exercé en réel.
