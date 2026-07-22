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
| `forge_song_with_gadgets` | Forge → Libretto (gate) → gadgets → REAPER |
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
