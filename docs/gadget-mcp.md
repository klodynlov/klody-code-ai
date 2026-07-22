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
| `list_gadgets` | instruments KORG installés (scan disque) |
| `list_gadget_projects` | projets .gdproj2/.gdproj sous les racines autorisées |
| `read_gadget_project` | tonalité, tempo, pistes→gadget, bus, master (lecture seule) |
| `create_gadget_track` | piste REAPER + VSTi `<Gadget> (KORG)` chargé |
| `import_gadget_project_to_reaper` | tempo + marqueur tonalité + une piste par gadget |
| `gadget_status` | app/VST/pont REAPER/racines — diagnostic |

Principes : pas de mapping 1:1, projets Gadget jamais écrits, mutations côté
REAPER uniquement (blocs Undo du pont), gadgets manquants → `missing` sans
interrompre l'import, `max_tracks` garde-fou.

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
défaut) — le render aboutit côté REAPER, seul le client a lâché ; relancer avec
`REAPER_BRIDGE_TIMEOUT=30` pour les renders.
