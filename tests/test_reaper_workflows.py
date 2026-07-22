"""Tests des workflows agentiques REAPER (klody_mcp.reaper_workflows).

Les workflows sont de l'orchestration pure : ils n'appellent JAMAIS REAPER, mais
un callable `call(cmd, args) -> dict` injecté. On injecte donc un FAUX pont
(FakeBridge) qui simule l'état projet et l'idempotence des primitives — la suite
tourne en CI sans REAPER. On vérifie : composition correcte, dégradation propre
quand un plugin manque (jamais de supposition), idempotence (rejeu sans doublon),
et le comptage `undo_steps`.
"""
from __future__ import annotations

import asyncio

import pytest
from klody_mcp import reaper_workflows as wf


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch, tmp_path):
    """Registre plugins VIDE par défaut -> build_vocal_chain (prefer_installed=True
    par défaut) retombe sur le stock Rea* de façon DÉTERMINISTE, hermétique quelle
    que soit la machine (sinon il lirait les vrais plugins installés). Les tests qui
    veulent un plugin installé monkeypatchent resolve_plugin eux-mêmes."""
    monkeypatch.setenv("KLODY_REAPER_RESOURCE", str(tmp_path / "empty_registry"))


class FakeBridge:
    """Faux pont REAPER : état projet en mémoire + primitives idempotentes.

    `installed_fx=None` -> tous les effets sont « installés » ; sinon seuls ceux du
    set le sont (add_fx renvoie une erreur pour les autres, comme le vrai pont quand
    un plugin n'est pas présent).
    """

    def __init__(self, installed_fx: set[str] | None = None, tempo: float = 120.0):
        self.tracks: list[dict] = []
        self.tempo = tempo
        self.regions: list[tuple[float, float, str]] = []
        self.installed_fx = installed_fx
        self._guid = 0
        self.calls: list[str] = []

    # -- utilitaires ---------------------------------------------------------
    def _new_guid(self) -> str:
        self._guid += 1
        return f"{{GUID-{self._guid:04d}}}"

    def add_existing_track(self, name: str, item_count: int = 0) -> dict:
        tr = {"index": len(self.tracks), "guid": self._new_guid(), "name": name,
              "item_count": item_count, "fx": [], "sends": [], "armed": False}
        self.tracks.append(tr)
        return tr

    def _resolve(self, args: dict) -> dict:
        guid = (args.get("guid") or "").strip()
        if guid:
            for t in self.tracks:
                if t["guid"] == guid:
                    return t
            raise KeyError(guid)
        idx = args.get("index", args.get("track_index"))
        if idx is None or int(idx) < 0 or int(idx) >= len(self.tracks):
            raise IndexError(idx)
        return self.tracks[int(idx)]

    # -- dispatch ------------------------------------------------------------
    async def __call__(self, cmd: str, args: dict | None = None) -> dict:
        self.calls.append(cmd)
        args = args or {}
        return getattr(self, "_cmd_" + cmd)(args)

    def _cmd_list_tracks(self, args: dict) -> dict:
        return {"count": len(self.tracks),
                "tracks": [{"index": t["index"], "guid": t["guid"], "name": t["name"]}
                           for t in self.tracks]}

    def _cmd_get_project_snapshot(self, args: dict) -> dict:
        full = str(args.get("detail")) == "full"
        tracks = []
        for t in self.tracks:
            d = {"index": t["index"], "guid": t["guid"], "name": t["name"]}
            if full:
                d["item_count"] = t["item_count"]
                d["fx_count"] = len(t["fx"])
            tracks.append(d)
        return {"project": {"tempo": self.tempo, "track_count": len(self.tracks)},
                "tracks": tracks}

    def _cmd_add_track(self, args: dict) -> dict:
        tr = self.add_existing_track(args.get("name") or "")
        return {"inserted_index": tr["index"], "guid": tr["guid"],
                "name": tr["name"], "track_count": len(self.tracks)}

    def _cmd_arm_track(self, args: dict) -> dict:
        t = self._resolve(args)
        t["armed"] = bool(args.get("armed", True))
        return {"index": t["index"], "guid": t["guid"], "armed": t["armed"],
                "rec_input": int(args.get("input", 0)), "monitor": bool(args.get("monitor", True))}

    def _cmd_set_tempo(self, args: dict) -> dict:
        bpm = float(args.get("bpm", 0))
        if not (1.0 <= bpm <= 960.0):
            return {"error": "bpm hors plage"}
        self.tempo = bpm
        return {"bpm": bpm}

    def _cmd_add_fx(self, args: dict) -> dict:
        t = self._resolve(args)
        name = (args.get("name") or "").strip()
        if self.installed_fx is not None and name not in self.installed_fx:
            return {"error": f"effet introuvable: {name!r} (installé ?)"}
        if name in t["fx"]:
            return {"track_index": t["index"], "guid": t["guid"],
                    "fx_index": t["fx"].index(name), "fx_name": name, "created": False}
        t["fx"].append(name)
        return {"track_index": t["index"], "guid": t["guid"],
                "fx_index": len(t["fx"]) - 1, "fx_name": name, "created": True}

    def _cmd_create_bus(self, args: dict) -> dict:
        name = (args.get("name") or "").strip()
        for t in self.tracks:
            if t["name"] == name:
                return {"index": t["index"], "guid": t["guid"], "name": name, "created": False}
        tr = self.add_existing_track(name)
        return {"index": tr["index"], "guid": tr["guid"], "name": name, "created": True}

    def _cmd_create_send(self, args: dict) -> dict:
        src = self._resolve(args)
        dest_guid = (args.get("dest_guid") or "").strip()
        if dest_guid in src["sends"]:
            return {"send_index": src["sends"].index(dest_guid), "created": False}
        src["sends"].append(dest_guid)
        return {"send_index": len(src["sends"]) - 1, "created": True}

    def _cmd_add_region(self, args: dict) -> dict:
        start = float(args.get("start", 0))
        end = float(args.get("end", 0))
        name = args.get("name") or ""
        for (s, e, _n) in self.regions:
            if abs(s - start) < 1e-6 and abs(e - end) < 1e-6:
                return {"start": start, "end": end, "name": name, "created": False}
        self.regions.append((start, end, name))
        return {"start": start, "end": end, "name": name,
                "region_id": len(self.regions), "created": True}

    def _cmd_render_track_isolated(self, args: dict) -> dict:
        t = self._resolve(args)
        out = args.get("out_path")
        if t["item_count"] <= 0:
            return {"track_index": t["index"], "guid": t["guid"], "out_path": out,
                    "rendered": False, "output_files": []}
        return {"track_index": t["index"], "guid": t["guid"], "out_path": out,
                "rendered": True, "output_files": [out]}

    def _cmd_set_fx_param(self, args: dict) -> dict:
        t = self._resolve(args)
        fx = args.get("fx")
        names = t["fx"]
        if isinstance(fx, str) and not fx.strip().lstrip("-").isdigit():
            fxn = next((n for n in names if fx.lower() in n.lower()), None)
            if fxn is None:
                return {"error": f"fx introuvable: {fx!r}"}
        else:
            i = int(fx)
            if i < 0 or i >= len(names):
                return {"error": "fx index hors borne"}
            fxn = names[i]
        t.setdefault("params", []).append({
            "fx": fxn, "param": args.get("param"),
            "value": args.get("value"), "raw": bool(args.get("raw")),
        })
        return {"fx_name": fxn, "param_name": args.get("param"), "normalized": 0.5}

    def _cmd_insert_media(self, args: dict) -> dict:
        t = self._resolve(args)
        path = (args.get("path") or "").strip()
        if not path:
            return {"error": "path requis"}
        pos = float(args.get("position", 0.0))
        t.setdefault("items", []).append(path)
        t["item_count"] += 1
        return {"track_index": t["index"], "guid": t["guid"], "path": path,
                "inserted": True, "item_index": len(t["items"]) - 1,
                "position": pos, "length": 1.0}


def run(coro):
    return asyncio.run(coro)


# -- prepare_vocal_recording -------------------------------------------------


def test_prepare_vocal_recording_creates_and_arms():
    fb = FakeBridge()
    r = run(wf.prepare_vocal_recording(fb, name="Lead", input_channel=0))
    assert r["track"]["created"] is True
    assert r["track"]["guid"].startswith("{GUID")
    assert r["armed"]["armed"] is True
    # création (1) + armement (1)
    assert r["undo_steps"] == 2
    assert len(fb.tracks) == 1 and fb.tracks[0]["armed"] is True


def test_prepare_vocal_recording_idempotent_track():
    fb = FakeBridge()
    fb.add_existing_track("Lead")
    r = run(wf.prepare_vocal_recording(fb, name="Lead"))
    assert r["track"]["created"] is False
    assert len(fb.tracks) == 1  # pas de doublon
    assert r["undo_steps"] == 1  # juste l'armement


def test_prepare_vocal_recording_with_chain():
    fb = FakeBridge()
    r = run(wf.prepare_vocal_recording(fb, name="Lead", build_chain=True))
    assert r["chain"] is not None
    assert [a["fx"] for a in r["chain"]["added"]] == ["ReaEQ", "ReaComp"]
    # track + arm + 2 fx + bus + reverb fx + send
    assert r["undo_steps"] >= 5


# -- build_vocal_chain -------------------------------------------------------


def test_build_vocal_chain_full():
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"]))
    assert [a["fx"] for a in r["added"]] == ["ReaEQ", "ReaComp"]
    assert r["missing"] == []
    assert r["reverb"]["bus"]["created"] is True
    assert r["reverb"]["send"]["created"] is True
    assert "ReaEQ" in tr["fx"] and "ReaComp" in tr["fx"]


def test_build_vocal_chain_requires_target():
    fb = FakeBridge()
    r = run(wf.build_vocal_chain(fb))  # ni guid ni index
    assert "error" in r


def test_build_vocal_chain_degrades_on_missing_plugin():
    # Seul ReaEQ installé -> ReaComp doit être collecté dans `missing`, pas planter.
    fb = FakeBridge(installed_fx={"ReaEQ", "ReaVerbate"})
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"]))
    added = [a["fx"] for a in r["added"]]
    missing = [m["fx"] for m in r["missing"]]
    assert "ReaEQ" in added
    assert "ReaComp" in missing
    assert r["reverb"]["bus"]["created"] is True  # le reste du workflow continue


def test_build_vocal_chain_idempotent():
    # tune=False : on isole l'idempotence de l'AJOUT (set_fx_param du tune n'est pas
    # idempotent — il re-règle les mêmes valeurs à chaque appel, ce qui est voulu).
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    run(wf.build_vocal_chain(fb, guid=tr["guid"], tune=False))
    r2 = run(wf.build_vocal_chain(fb, guid=tr["guid"], tune=False))
    # 2e passage : tout existe déjà -> rien créé -> aucun undo
    assert all(a["created"] is False for a in r2["added"])
    assert r2["undo_steps"] == 0
    assert tr["fx"] == ["ReaEQ", "ReaComp"]  # pas de doublon


# -- create_zouk_arrangement -------------------------------------------------


def test_create_zouk_arrangement_default():
    fb = FakeBridge()
    r = run(wf.create_zouk_arrangement(fb, bpm=120.0))
    assert r["bpm"] == 120.0
    assert r["seconds_per_bar"] == 2.0  # 60/120 * 4
    assert len(r["regions"]) == 8
    # régions contiguës : la fin de l'une = le début de la suivante
    for a, b in zip(r["regions"], r["regions"][1:], strict=False):
        assert a["end"] == b["start"]
    total_bars = sum(bars for _n, bars in wf._DEFAULT_ZOUK)
    assert r["total_seconds"] == pytest.approx(total_bars * 2.0)
    assert fb.tempo == 120.0


def test_create_zouk_arrangement_custom_sections_and_idempotent():
    fb = FakeBridge()
    secs = [{"name": "A", "bars": 4}, {"name": "B", "bars": 8}]
    r1 = run(wf.create_zouk_arrangement(fb, bpm=100.0, sections=secs))
    assert [x["name"] for x in r1["regions"]] == ["A", "B"]
    assert r1["seconds_per_bar"] == pytest.approx(2.4)  # 60/100 * 4
    r2 = run(wf.create_zouk_arrangement(fb, bpm=100.0, sections=secs))
    assert all(x["created"] is False for x in r2["regions"])  # idempotent
    assert r2["undo_steps"] == 0
    assert len(fb.regions) == 2  # pas de doublon


def test_create_zouk_arrangement_uses_project_tempo():
    fb = FakeBridge(tempo=90.0)
    r = run(wf.create_zouk_arrangement(fb, sections=[{"name": "X", "bars": 1}]))
    assert r["bpm"] == 90.0
    assert r["seconds_per_bar"] == pytest.approx(60.0 / 90.0 * 4, abs=1e-3)


# -- prepare_mix -------------------------------------------------------------


def test_prepare_mix_creates_buses():
    fb = FakeBridge()
    r = run(wf.prepare_mix(fb))
    names = [b["bus"] for b in r["buses"]]
    assert names == ["Reverb", "Delay"]
    assert all(b["created"] is True for b in r["buses"])
    assert r["buses"][0]["fx"] == "ReaVerbate"
    assert r["buses"][1]["fx"] == "ReaDelay"


def test_prepare_mix_idempotent():
    fb = FakeBridge()
    run(wf.prepare_mix(fb))
    r2 = run(wf.prepare_mix(fb))
    assert all(b["created"] is False for b in r2["buses"])
    assert r2["undo_steps"] == 0
    assert len([t for t in fb.tracks if t["name"] in ("Reverb", "Delay")]) == 2


def test_prepare_mix_degrades_on_missing_fx():
    fb = FakeBridge(installed_fx=set())  # aucun effet installé
    r = run(wf.prepare_mix(fb))
    # les bus sont quand même créés ; le fx manquant est signalé, pas fatal
    assert all(b["created"] is True for b in r["buses"])
    assert all(b["fx"] is None and b["fx_error"] for b in r["buses"])


# -- render_all_stems --------------------------------------------------------


def test_render_all_stems_skips_empty(tmp_path):
    fb = FakeBridge()
    fb.add_existing_track("Vox", item_count=3)
    fb.add_existing_track("Drums", item_count=2)
    fb.add_existing_track("Reverb", item_count=0)  # bus vide -> sauté
    out = tmp_path / "stems"
    r = run(wf.render_all_stems(fb, str(out)))
    assert r["track_count"] == 3
    assert r["rendered"] == 2
    skipped = [s for s in r["stems"] if s.get("skipped")]
    assert len(skipped) == 1 and skipped[0]["name"] == "Reverb"
    # noms de fichiers dérivés (index zéro-paddé + nom assaini)
    paths = [s["output_files"][0] for s in r["stems"] if s.get("rendered")]
    assert any("00_Vox.wav" in p for p in paths)
    assert out.is_dir()


def test_render_all_stems_include_empty(tmp_path):
    fb = FakeBridge()
    fb.add_existing_track("Vox", item_count=1)
    fb.add_existing_track("Bus", item_count=0)
    r = run(wf.render_all_stems(fb, str(tmp_path), include_empty=True))
    # la piste vide est tentée (rendered False) mais pas sautée
    assert all("skipped" not in s for s in r["stems"])
    assert r["rendered"] == 1


def test_render_all_stems_requires_out_dir():
    fb = FakeBridge()
    r = run(wf.render_all_stems(fb, ""))
    assert "error" in r


# -- build_vocal_chain : presets (#3) ----------------------------------------


def test_build_vocal_chain_tunes_stock_reacomp():
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"], chain=["ReaEQ", "ReaComp"],
                                 reverb_send=False, tune=True))
    assert r["chain_source"] == "explicit"
    assert [a["fx"] for a in r["added"]] == ["ReaEQ", "ReaComp"]
    # tune règle Ratio + Threshold sur ReaComp, en unités natives (raw=True)
    assert {p["param"] for p in r["tuned"]} == {"Ratio", "Thresh"}
    params = tr.get("params", [])
    assert any(p["raw"] and p["param"] == "Ratio" and p["value"] == 3.0 for p in params)
    assert any(p["raw"] and p["param"] == "Thresh" and p["value"] == -18.0 for p in params)


def test_build_vocal_chain_tune_skips_unknown_comp():
    # KaribVoice Compressor : layout inconnu -> on n'y touche PAS (spec : ne pas prétendre)
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"],
                                 chain=["KaribVoice EQ", "KaribVoice Compressor"],
                                 reverb_send=False, tune=True))
    assert r["tuned"] == []
    assert "params" not in tr or tr["params"] == []


def test_build_vocal_chain_prefers_installed(monkeypatch):
    def fake_resolve(role, installed=None):
        return {
            "eq": {"name": "KaribVoice EQ"}, "comp": {"name": "KaribVoice Compressor"},
            "reverb": {"name": "KaribVoice Reverb"},
        }.get(role)
    monkeypatch.setattr(wf.reaper_plugins, "resolve_plugin", fake_resolve)
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"], prefer_installed=True))
    assert r["chain_source"] == "installed"
    added = [a["fx"] for a in r["added"]]
    assert "KaribVoice EQ" in added and "KaribVoice Compressor" in added
    assert r["reverb"]["fx"] == "KaribVoice Reverb"
    assert r["tuned"] == []  # KaribVoice != ReaComp -> pas de tune


def test_build_vocal_chain_installed_falls_back_to_stock(monkeypatch):
    monkeypatch.setattr(wf.reaper_plugins, "resolve_plugin",
                        lambda role, installed=None: None)
    fb = FakeBridge()
    tr = fb.add_existing_track("Vox")
    r = run(wf.build_vocal_chain(fb, guid=tr["guid"], prefer_installed=True))
    assert [a["fx"] for a in r["added"]] == ["ReaEQ", "ReaComp"]
    assert r["reverb"]["fx"] == "ReaVerbate"


# -- place_sample (#1) -------------------------------------------------------


def test_place_sample_imports_best(monkeypatch):
    fake_hits = [
        {"path": "/lib/kick_01.wav", "name": "kick_01.wav", "rel": "kick_01.wav", "root": "/lib", "score": 6},
        {"path": "/lib/kick_02.wav", "name": "kick_02.wav", "rel": "kick_02.wav", "root": "/lib", "score": 3},
    ]
    monkeypatch.setattr(wf.reaper_samples, "search_samples",
                        lambda q, root=None, limit=20: fake_hits)
    fb = FakeBridge()
    tr = fb.add_existing_track("Drums")
    r = run(wf.place_sample(fb, query="kick", guid=tr["guid"], position=2.0))
    assert r["chosen"]["name"] == "kick_01.wav"
    assert r["provenance"] == "/lib/kick_01.wav"  # source exacte
    assert r["placed"]["inserted"] is True
    assert r["undo_steps"] == 1
    assert tr["item_count"] == 1
    assert len(r["candidates"]) == 2


def test_place_sample_no_hits(monkeypatch):
    monkeypatch.setattr(wf.reaper_samples, "search_samples",
                        lambda q, root=None, limit=20: [])
    fb = FakeBridge()
    tr = fb.add_existing_track("Drums")
    r = run(wf.place_sample(fb, query="xyz", guid=tr["guid"]))
    assert "error" in r and r["candidates"] == []


def test_place_sample_validation():
    fb = FakeBridge()
    assert "error" in run(wf.place_sample(fb, query="", guid="x"))  # query vide
    assert "error" in run(wf.place_sample(fb, query="kick"))  # ni guid ni index


# -- create_instrument_track (piste AVEC instrument KORG, corrige le rendu muet) ----


def test_create_instrument_track_creates_and_loads():
    fb = FakeBridge()
    r = run(wf.create_instrument_track(fb, name="Basse", gadget="Madrid"))
    assert r["created"] is True
    assert r["status"] == "ok"
    assert r["fx_loaded"] is True
    # gadget_resolved = nom du VSTi réellement chargé (suffixe « (KORG) » ajouté)
    assert r["gadget_resolved"] == "Madrid (KORG)"
    assert r["guid"].startswith("{GUID")
    # création piste (1) + chargement instrument (1)
    assert r["undo_steps"] == 2
    assert len(fb.tracks) == 1
    assert fb.tracks[0]["name"] == "Basse"
    assert fb.tracks[0]["fx"] == ["Madrid (KORG)"]  # instrument bien posé


def test_create_instrument_track_suffix_not_doubled():
    # gadget déjà suffixé « (KORG) » -> on n'ajoute pas un 2e suffixe.
    fb = FakeBridge()
    r = run(wf.create_instrument_track(fb, name="Keys", gadget="Chicago (KORG)"))
    assert r["gadget_resolved"] == "Chicago (KORG)"
    assert fb.tracks[0]["fx"] == ["Chicago (KORG)"]


def test_create_instrument_track_missing_gadget():
    # Le VST KORG n'est pas résolu par REAPER (absent du set installé) : la piste est
    # quand même créée, le manque est signalé (jamais de supposition, cf. spec 7.6).
    fb = FakeBridge(installed_fx={"ReaEQ"})  # « Marseille (KORG) » non installé
    r = run(wf.create_instrument_track(fb, name="Lead", gadget="Marseille"))
    assert r["status"] == "missing"
    assert r["fx_loaded"] is False
    assert r["gadget_resolved"] is None
    assert r["gadget_requested"] == "Marseille (KORG)"
    assert r["error_fx"]  # message d'erreur du pont conservé
    assert r["created"] is True  # PISTE créée malgré tout
    assert len(fb.tracks) == 1 and fb.tracks[0]["fx"] == []
    assert r["undo_steps"] == 1  # juste la piste (pas d'instrument)


def test_create_instrument_track_idempotent_replay():
    # Rejouer le MÊME appel ne duplique ni la piste ni l'instrument.
    fb = FakeBridge()
    run(wf.create_instrument_track(fb, name="Basse", gadget="Madrid"))
    r2 = run(wf.create_instrument_track(fb, name="Basse", gadget="Madrid"))
    assert r2["created"] is False  # piste retrouvée par nom
    assert r2["fx_loaded"] is True
    assert r2["undo_steps"] == 0  # rien de neuf -> aucun undo
    assert len(fb.tracks) == 1  # pas de doublon de piste
    assert fb.tracks[0]["fx"] == ["Madrid (KORG)"]  # pas de doublon d'instrument


def test_create_instrument_track_loads_on_existing_track():
    # Piste préexistante SANS instrument (cas du bug in-vivo : Mélodie/Basse/Accords
    # créées mais muettes) -> on ne recrée pas la piste, on charge juste l'instrument.
    fb = FakeBridge()
    fb.add_existing_track("Mélodie")
    r = run(wf.create_instrument_track(fb, name="Mélodie", gadget="Marseille"))
    assert r["created"] is False  # piste réutilisée
    assert r["fx_loaded"] is True
    assert r["undo_steps"] == 1  # seulement l'instrument
    assert len(fb.tracks) == 1
    assert fb.tracks[0]["fx"] == ["Marseille (KORG)"]


def test_create_instrument_track_validation():
    fb = FakeBridge()
    assert "error" in run(wf.create_instrument_track(fb, name="", gadget="Madrid"))
    assert "error" in run(wf.create_instrument_track(fb, name="Basse", gadget=""))
    assert fb.tracks == []  # aucun effet de bord sur argument invalide
