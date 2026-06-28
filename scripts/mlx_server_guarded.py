#!/usr/bin/env python
"""Lanceur mlx_lm.server SUPERVISÉ (anti-wedge).

Problème (investigation library-brain 2026-06-28) : mlx_lm 0.31.3 fait tourner la
génération dans UN seul thread `ResponseGenerator._generate`. Sa boucle BATCHÉE
(`server.py:853 batch_generator.next()`) n'a AUCUN try/except — contrairement à
`_serve_single`. Une exception y (bug `extend()` posant [None]*n sur les
logits_processors → TypeError ; ou autre) **tue le thread** : plus aucun sentinel
n'est jamais déposé sur les `response_queue`, donc TOUTE requête suivante (même
triviale) bloque indéfiniment → worker à 0 % CPU, wedge GLOBAL jusqu'au restart.

Ce wrapper enrobe `_generate` dans un superviseur : si la boucle crashe, on log et
on la REDÉMARRE (état frais : batch_generator/batch_results recréés). `load()`
court-circuite quand le modèle est déjà chargé (server.py:393) → le redémarrage ne
recharge PAS les 37 Go, il coûte ~rien. Les requêtes déjà en vol au moment du crash
perdent leur sentinel et expirent côté client (library-brain `llm_timeout=120s`) ;
les requêtes en attente dans `self.requests` sont servies dès la reprise.

NB : ne couvre QUE le mode « thread mort → wedge permanent ». Un abort process
(OOM Metal C++) tue tout le process → déjà relancé par launchd (KeepAlive).

Durable : appelé par scripts/start-mlx.sh à la place de `python -m mlx_lm.server`,
donc ré-appliqué à chaque démarrage, y compris après un `pip install -U mlx_lm`
(monkeypatch runtime, aucune édition de fichier vendored). Défensif : si la cible
a changé de nom (upgrade mlx_lm), on log un warning et on démarre NON patché.
"""
import logging

import mlx_lm.server as S

log = logging.getLogger("mlx_wedge_guard")


def _install_guard() -> None:
    try:
        _orig_generate = S.ResponseGenerator._generate
    except AttributeError:
        # print : visible dans le log même avant que main() configure le logging
        print("[wedge-guard] ResponseGenerator._generate introuvable "
              "(mlx_lm a changé ?) — serveur démarré NON patché", flush=True)
        log.warning("[wedge-guard] cible introuvable — NON patché")
        return

    def _guarded_generate(self):
        while not getattr(self, "_stop", False):
            try:
                _orig_generate(self)
                return  # sortie propre (self._stop posé au shutdown)
            except Exception:
                log.exception(
                    "[wedge-guard] _generate a crashé — redémarrage de la boucle "
                    "de génération (les requêtes en vol expirent côté client)"
                )
                if getattr(self, "_stop", False):
                    return
                # boucle → relance _orig_generate avec un état local neuf
        return

    S.ResponseGenerator._generate = _guarded_generate
    print("[wedge-guard] installé : _generate supervisé (anti-wedge)", flush=True)
    log.info("[wedge-guard] installé : _generate supervisé (anti-wedge)")


_install_guard()

# Délègue au point d'entrée standard (argparse lit sys.argv comme `-m mlx_lm.server`)
S.main()
