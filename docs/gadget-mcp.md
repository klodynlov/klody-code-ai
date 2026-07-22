# Serveur MCP KORG Gadget — pilotage indirect

`klody_mcp/gadget_server.py` pilote **KORG Gadget** (l'application macOS) sans la
scripter — elle ne le permet pas (aucun dictionnaire AppleScript, aucune API).
Deux portes indirectes, toutes deux validées in-vivo le 22/07/2026 :

1. **Instruments** : les ~43 gadgets « villes » (Chicago, London, Phoenix…) sont
   installés en VST dans `/Library/Audio/Plug-Ins/VST/KORG/`. Le serveur les
   charge dans REAPER via le pont socket existant (`reaper_bridge/`, :9000) —
   la piste devient alors pilotable par les outils `reaper` (MIDI, mix, render).
2. **Projets** : le format `.gdproj2` est lu nativement (lecture seule stricte).

## Format .gdproj2 (rétro-ingénierie, Gadget 2.9.5)

```
<Nom>.gdproj2/               package (dossier)
└── <Nom>.gddat              NSKeyedArchiver (plist binaire)
    ├── project_scale_data   → {Key: "C", Scale: "Dorian"}
    └── project_data         → ZIP embarqué (magic PK\x03\x04) :
        root/seqs/seq.dat            binaire ; @0 int32 LE = version (1|2),
                                     @8 float32 LE = tempo BPM
                                     (@44 = 120.0 constant : leurre, ignorer)
        root/midiOutInfo.plist       canal MIDI par piste
        root/tracks/<n>/plugins/<m>/plugin_info   plist XML {"Name": "<gadget>"}
                                     slot 0 = instrument, slots suivants = FX
        root/buses/…, root/master/…  chaînes internes (ChannelStrip,
                                     MasterLimiter, GenericMixer, HallReverb…)
```

Offsets tempo vérifiés sur 8 projets réels (96–197 BPM, versions 1 et 2).
Les presets (`program.bin`) sont un chunk propriétaire : non transférés.
Piste future : injecter `program.bin` comme chunk d'état VST via
`TrackFX_SetNamedConfigParm` (non tenté).

## Outils exposés (6)

| Outil | Effet |
|---|---|
| `list_gadgets` | instruments KORG installés + catégorie + `by_role` |
| `list_gadget_projects` | projets .gdproj2/.gdproj sous les racines autorisées |
| `read_gadget_project` | tonalité, tempo, pistes→gadget, bus, master (lecture seule) |
| `create_gadget_track` | piste REAPER + VSTi `<Gadget> (KORG)` chargé |
| `import_gadget_project_to_reaper` | tempo + marqueur tonalité + une piste par gadget |
| `analyze_midi_structure` | score SMS Libretto d'un MIDI (29 axes) |
| `forge_song_with_gadgets` | commande musicale → Forge → Libretto (gate) → gadgets → REAPER |
| `gadget_status` | app/VST/pont REAPER/Libretto/racines — diagnostic |

Principes : pas de mapping 1:1, projets Gadget jamais écrits, mutations côté
REAPER uniquement (blocs Undo du pont), gadgets manquants → `missing` sans
interrompre l'import, `max_tracks` garde-fou.

## Chaîne Forge → Libretto → Gadget → REAPER

```
Forge (N ébauches)  →  Libretto (SMS, 29 axes)  →  GATE  →  Gadget  →  REAPER
   génère                    juge                     ↓        timbre    montage
                                          rien ne passe sans score fiable
```

[Libretto](https://github.com/klodynlov/Libretto) (dépôt voisin, `LIBRETTO_ROOT`
sinon `~/Projets/Libretto`) note la **structure** d'un MIDI ; Forge
(`examples/forge.py`) s'en sert comme fonction de fitness : N ébauches, la
meilleure gagne — fiabilité d'abord, score ensuite. `forge_song_with_gadgets`
enchaîne le tout et **ne monte rien dans REAPER si aucun candidat ne franchit
le gate** : on n'envoie pas au DAW une structure qu'on ne sait pas juger.

Dépendance **optionnelle** : Libretto est stdlib pur, hors requirements, importé
paresseusement (patron des extras d'`audio_analysis`). Absent → seuls les deux
outils concernés répondent une erreur actionnable, le reste tourne.

**Rôle → gadget, sans deviner.** Chaque VST déclare son caractère dans son
`CFBundleName` : `London (Drum)`, `Madrid (Bass)`, `Brussels (Lead)`,
`Glasgow (Keys)`… `_gadget_categories()` lit ces plists et `_ROLE_CATEGORIES`
donne l'ordre de préférence par rôle (repli sur la catégorie suivante si
l'utilisateur n'a pas le gadget typé). Le rôle d'une piste MIDI vient du
fichier : canal 10 (index 9) = percussions GM ; sinon la plus grave = `bass`,
la plus aiguë = `lead`, le reste = `chords`. Forçage possible via
`instruments={"bass": "Chicago"}`.

Notes envoyées par lots de 120 (garde 64 KiB/ligne du pont) ; timeouts dédiés
(`GADGET_INSERT_TIMEOUT` 30 s, `GADGET_RENDER_TIMEOUT` 300 s) car les 5 s par
défaut du pont font lâcher le client sur une opération que REAPER termine.

## Commander la musique, pas seulement la juger

```python
forge_song_with_gadgets(key="Fa", mode="mineur", bars=16, style="rnb")
```

Sans contrainte, Forge tire **tout** au sort — tonique (1/12), mode, tempo,
métrique, forme, mesures par section : la graine était la seule poignée et
elle ne dit rien. Ces paramètres imposent la demande au générateur.

| Paramètre | Accepte | Note |
|---|---|---|
| `key` | `F`, `Fa`, `Fa#`, `Sib`, `ré`, `0-11` | piège traité : `b` seul = si bécarre, `bb` = si bémol |
| `mode` | `minor`/`mineur`, `major`/`majeur`, `dorien`, `mixolydien` | |
| `bars` | longueur **totale exacte** | 16, 20, 24, 32 atteignables ; 17 non → l'erreur donne les voisines |
| `bpm` | tempo | désactive la dérive de tempo |
| `meter` | `4/4`, `3/4`, `6/8`, `12/8`, `5/4` | `7/8` **refusé** : absent du générateur, pas d'invention |
| `style` | `rnb`, `lofi`, `house`, `ballade`, `valse` | carrure seulement — voir plus bas |

Les noms de notes et de modes ne sont **pas** réinterprétés côté Klody :
Forge a ses parseurs, ils restent la seule autorité — un second vocabulaire
divergerait. Toute valeur donnée nommément l'emporte sur le preset de
`style` : « R&B en fa mineur à 90 bpm » sort à 90.

**Ce qu'un `style` ne fait pas.** Les presets règlent la CARRURE (mode,
tempo, métrique, swing, syncope, batterie). Pas la couleur harmonique : le
générateur de Forge ne construit que des **triades**, sans septièmes ni
neuvièmes. `style="rnb"` donne donc un mineur lent swingué syncopé — la
charpente du genre, pas son harmonie. Une vraie couleur R&B demanderait un
générateur dédié ; Libretto n'y changerait rien, il juge la structure, pas
le genre.

**Contraindre restreint l'espace de recherche sans l'annuler** : motifs,
progressions, arc, effectif et la forme (tant que `bars` ne la dicte pas)
restent tirés — c'est ce qui fait que les `n` candidats diffèrent encore et
que la sélection garde un sens. Le gate ne bouge pas pour autant : une
demande très contrainte peut ne rien produire, et c'est un résultat.

`structure` et `constraints` reviennent dans la réponse : on vérifie la
commande, on ne se contente pas de constater le résultat.

Le dépôt Libretto est **voisin et optionnel** : il peut être en retard. Une
version sans les options de contrainte est détectée (`unrecognized
arguments`) et renvoie « mettre à jour `~/Projets/Libretto` », pas une trace
d'argparse.

## Démarrage

```bash
./scripts/start-gadget-mcp.sh --http      # :8093 (GADGET_MCP_PORT)
```

Déclaré dans `.env` : `KLODY_MCP_SERVERS["gadget"] = http://127.0.0.1:8093/mcp`.
Comme `reaper_server`, PAS de LaunchAgent : dépend d'un REAPER vivant, restart
manuel (`kill` + relancer le script). Après un (re)démarrage du serveur,
`launchctl kickstart -k gui/$(id -u)/com.klody.api` pour que Klody redécouvre
les outils.

Prérequis pilotage : REAPER lancé (`open -a REAPER` recharge le pont via
`__startup.lua`). La lecture de projets fonctionne sans REAPER.

Racines projets : `GADGET_PROJECT_ROOTS` (os.pathsep) sinon racines audio du
pathguard + `~/Desktop` + iCloud Drive. Suffixe `.gdproj2/.gdproj/.gddat`
imposé (ASI02 : un chemin LLM ne peut pas lire un fichier arbitraire).

## Validation in-vivo (22/07/2026)

- `create_gadget_track("chicago")` → `VSTi: Chicago (KORG)` chargé, cleanup par GUID.
- Import de `Dark Pole.gdproj2` (C Dorien, 197 BPM) → 4/4 pistes (Marseille,
  Chicago, London, Phoenix), tempo réglé, marqueur `Key: C Dorian`.
- Riff MIDI 8 notes sur Chicago → render WAV 2.4 s, peak −13 dBFS,
  silence_ratio 0.0 : le gadget SONNE.

Gotcha vécu : `render_project` peut dépasser `REAPER_BRIDGE_TIMEOUT` (5 s
défaut) — le render aboutit côté REAPER, seul le client a lâché ; d'où le
paramètre `timeout` par appel ajouté à `reaper_server._bridge_call`.

### Chaîne Forge → Libretto → Gadget (22/07/2026)

`forge_song_with_gadgets(n=8, seed=3, render_to=…)` :
- 8 ébauches générées et notées ; gagnante SMS **0.8443**, fiabilité 0.9246
  (« élevée ») — forme binaire, mode dorien, 3/4 à 168 bpm
- 4/4 pistes montées sur gadgets choisis par catégorie : Glasgow (Keys) ×2,
  Brussels (Lead), Madrid (Bass) — **672 notes**, 4 marqueurs de section
- render → WAV **51.6 s**, peak −4.32 dBFS, LUFS −20.1, silence_ratio 0.0

Le gate a été vérifié dans l'autre sens (test `test_gate_bloque_avant_reaper`) :
sans candidat fiable, **zéro appel au pont** — le projet REAPER n'est pas touché.

### Commande musicale honorée (22/07/2026)

`forge_song_with_gadgets(key="Fa", mode="mineur", bars=16, style="rnb", n=10)` :
- gagnante `verse_chorus_court` · **F min** · 4/4 · **72 bpm** · **16 mes.** —
  SMS 0.7826, fiabilité 0.9235 (« élevée ») : la demande sort telle quelle
- 4/4 pistes montées : Glasgow (Keys), Brussels (Lead), Madrid (Bass),
  Gladstone (Drum) — **416 notes**, 4 marqueurs
- render : audio continu de 0 à **54.7 s**, aucun trou interne, peak −1.23 dBFS
  (attendu 53.3 s pour 16 mesures à 72 bpm en 4/4, + queue de réverbération)

Gotcha vécu : `render_project` rend **tout le projet**, y compris les pistes
d'une session précédente ; la durée du WAV est fixée par l'élément le plus
tardif, pas par ce qu'on vient de monter. Mesurer la plage réellement sonore,
pas la longueur du fichier — ou muter les pistes étrangères le temps du rendu
(`set_track_mute`, réversible) plutôt que les supprimer.

### Le gate confondu avec une panne (corrigé)

`forge.py` sort en **code 2** quand aucun candidat ne passe le gate.
`run_forge` traitait tout code non nul comme un échec : le message honnête
« aucun candidat n'a passé le gate — rien envoyé à REAPER » était donc
**inatteignable** en vrai, remplacé par « Forge indisponible : Forge a échoué
(code 2) ». Ce qui départage désormais : le **rapport a été écrit**. Écrit =
gate (un résultat) ; rien d'écrit = échec réel (contrainte refusée, dépôt
cassé, crash). Un rapport d'un appel précédent est effacé avant lancement —
sinon il se lirait comme le résultat de celui-ci, avec un gagnant qui
n'existe plus.
