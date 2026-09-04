#!/usr/bin/env bash
# Empêche le Mac de se rendormir après le réveil planifié par pmset repeat.
#
# Le nightly bench (cron 03:00 UTC) tourne sur le runner self-hosted Mac.
# En été (CEST, UTC+2) c'est 05:00 local, en hiver (CET, UTC+1) c'est
# 04:00 local.  pmset repeat wakeorpoweron est réglé à 03:50 local, ce qui
# couvre les deux cas — mais sans caffeinate le Mac se rendort au bout d'une
# minute d'inactivité (sleep 1), bien avant que le cron ne tire.
#
# caffeinate -u simule une activité utilisateur et empêche le sommeil d'inactivité.
# 5400 s = 90 min : le Mac reste éveillé de 03:50 à ~05:20, le bench démarre
# au plus tard à 05:00 et prend ensuite le relais CPU.
#
# Piloté par launchagents/com.klody.bench-wake.plist (StartCalendarInterval
# 03:52 local — 2 min après le réveil pmset pour laisser le réseau monter).
#
# Prérequis (une seule fois, requiert sudo) :
#   sudo pmset repeat wakeorpoweron MTWRFSU 03:50:00
#
# Vérification :
#   pmset -g sched | grep wakeorpoweron

exec caffeinate -u -t 5400
