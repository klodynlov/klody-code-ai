"""Tests de la politique d'approbation humaine (agent/approval.py).

Vérifie que les actions à effet de bord sont gardées par une validation
utilisateur, et que la lecture/recherche/inspection passe librement — y compris
pour les outils MCP, classés par leur verbe de tête.
"""
from agent.approval import requires_approval


def test_side_effect_tools_require_approval():
    for name in [
        "execute_command", "write_file", "run_in_sandbox", "save_skill",
        "delete_skill", "import_llm_export", "clone_github_repo",
        "index_github_repo", "create_project", "learn_from_books",
        "edit_wav", "mix_stems", "generate_silence", "convert_format",
    ]:
        assert requires_approval(name), f"devrait être gardé : {name}"


def test_generate_excel_n_est_pas_garde():
    # Choix produit : l'Excel est confiné à _downloads/ (nom basenamé, jamais
    # d'écrasement d'un fichier projet) et l'utilisateur l'a explicitement
    # demandé → pas d'approbation. Cf. agent/approval.py.
    assert not requires_approval("generate_excel")


def test_read_only_tools_pass():
    for name in [
        "read_file", "list_files", "search_in_files", "find_symbol",
        "find_references", "find_relevant_files", "await_distillation",
        "preview_file", "preview_code", "list_previews", "stop_preview_server",
        "remember_fact", "forget_fact", "list_skills", "search_books",
        "get_skills", "analyze_audio", "get_waveform_data", "browse_repo",
        "read_github_file", "list_indexed_repos",
    ]:
        assert not requires_approval(name), f"ne devrait PAS être gardé : {name}"


def test_mcp_writes_require_approval():
    for leaf in [
        "create_event", "send_email", "update_label", "delete_label",
        "label_message", "export_design", "generate_design", "create_file",
        "comment_on_design", "upload_asset_from_url",
        "commit_editing_transaction", "cancel_editing_transaction",
    ]:
        assert requires_approval(f"mcp__srv__{leaf}"), f"MCP write : {leaf}"


def test_mcp_reads_pass():
    for leaf in [
        "search_threads", "list_labels", "get_thread", "read_file_content",
        "list_events", "suggest_libraries", "resolve_shortlink", "search_files",
        "get_design", "list_drafts", "get_file_metadata", "get_file_permissions",
    ]:
        assert not requires_approval(f"mcp__srv__{leaf}"), f"MCP read : {leaf}"


def test_speak_n_est_pas_garde():
    # Même choix produit que generate_excel : WAV confiné à ~/.vocalbrain/audio,
    # lecture explicitement demandée, quelques secondes → pas d'approbation.
    assert not requires_approval("speak")


def test_mcp_verbes_francais_write():
    # Les serveurs MCP maison nomment en français : generer_chanson lance des
    # minutes de calcul + écrit sur le disque → gardé.
    for leaf in ["generer_chanson", "entrainer_voix", "creer_projet",
                 "supprimer_session", "envoyer_message"]:
        assert requires_approval(f"mcp__vocalbrain__{leaf}"), f"MCP write FR : {leaf}"


def test_mcp_verbes_francais_read():
    for leaf in ["lister_voix", "lister_sessions", "statut_generation",
                 "resultat_generation", "etat_systeme", "statut_entrainement"]:
        assert not requires_approval(f"mcp__vocalbrain__{leaf}"), f"MCP read FR : {leaf}"


def test_unknown_internal_tool_defaults_to_pass():
    # Outil interne inconnu (non MCP) → pas de gate (lecture présumée).
    assert not requires_approval("some_future_inspect_tool")


def test_mcp_unknown_head_falls_back_to_strong_write_verb():
    # Verbe de tête inconnu mais verbe mutateur fort présent → gardé.
    assert requires_approval("mcp__srv__bulk_create_items")
    # Verbe de tête inconnu sans verbe mutateur fort → laissé passer.
    assert not requires_approval("mcp__srv__fuzzy_lookup_entries")


# --- politique par serveur : organe laser (effets physiques sans verbe dans le nom)

def test_laser_firing_and_motion_tools_require_approval():
    for leaf in ["laser_arm", "laser_run", "laser_dot", "laser_frame", "laser_jog", "laser_goto",
                 "laser_send", "laser_reset", "laser_unlock", "laser_resume", "laser_set_origin",
                 "work_set_from_dots", "work_reset", "work_confirm", "camera_align", "camera_align_auto",
                 "job_goto_op"]:
        assert requires_approval(f"mcp__laser__{leaf}"), f"doit être gardé : {leaf}"


def test_laser_stop_and_hold_never_wait():
    # Arrêt d'urgence : une validation humaine ferait perdre des secondes avec le laser allumé.
    assert not requires_approval("mcp__laser__laser_stop")
    assert not requires_approval("mcp__laser__laser_hold")


def test_laser_preparation_tools_are_free():
    # job_add_* aurait été gardé par le verbe « add » : préparer un job ne tire jamais.
    for leaf in ["job_add_vector", "job_add_raster", "job_new", "job_preview", "text_to_svg",
                 "image_to_svg", "mesh_to_svg", "camera_capture", "camera_bed_view", "laser_connect",
                 "laser_status", "work_info", "job_add_text", "job_offset_op", "job_boolean", "job_array",
                 "job_add_material_test", "material_apply", "list_fonts"]:
        assert not requires_approval(f"mcp__laser__{leaf}"), f"doit être libre : {leaf}"


def test_laser_unknown_tool_falls_back_to_generic_rule():
    assert requires_approval("mcp__laser__delete_everything")     # verbe de tête mutateur
    assert not requires_approval("mcp__laser__lookup_material")    # lecture probable


# --- politique par serveur : organe KLOD Live Brain (Ventuno Q, Link dans Live, GrooveDNA)

def test_klod_flash_tempo_and_midi_require_approval():
    # ventuno_app_start flashe le STM32H5 ; live_link_set change le tempo de tous les pairs Link.
    for leaf in ["ventuno_app_start", "ventuno_link_start", "live_link_set", "play_groove",
                 "send_to_reaper", "capture_groove", "import_midi_groove", "apply_groove",
                 "morph_grooves"]:
        assert requires_approval(f"mcp__klod__{leaf}"), f"doit être gardé : {leaf}"


def test_klod_generic_rule_would_have_let_them_pass():
    # Prémisse de la politique : sans elle, aucun verbe de tête ne les garde.
    for leaf in ["ventuno_app_start", "live_link_set", "play_groove", "morph_grooves"]:
        assert not requires_approval(f"mcp__autre__{leaf}"), f"prémisse fausse : {leaf}"


def test_klod_stops_and_reads_are_free():
    for leaf in ["ventuno_app_stop", "ventuno_link_stop", "klod_status", "list_grooves",
                 "get_groove", "find_matching_samples", "ventuno_status", "ventuno_logs",
                 "ventuno_link_status", "live_link_status", "export_midi"]:
        assert not requires_approval(f"mcp__klod__{leaf}"), f"doit être libre : {leaf}"


def test_server_policy_does_not_leak_to_other_servers():
    # « laser_arm » sur un autre serveur : règle générique (verbe de tête inconnu, pas de verbe fort).
    assert not requires_approval("mcp__gadget__laser_arm")


# ── 2026-11-19 : outils natifs à effet de bord qui passaient sans validation ──
# Relevé lors du spike iMessage : 54 outils natifs sur 69 étaient libres, dont
# AppleScript, Raccourcis, MQTT (domotique) et les modes écriture de git/docker/sql.


def test_pilotage_mac_et_domotique_sont_gardes():
    for name in ["run_applescript", "run_shortcut", "mqtt_publish",
                 "backup_directory", "scaffold_tool"]:
        assert requires_approval(name), f"devrait être gardé : {name}"


def test_lecture_voisine_reste_libre():
    for name in ["mqtt_subscribe", "list_shortcuts", "spotlight_search",
                 "kubectl_control", "scaffold_api", "reveal_in_finder"]:
        assert not requires_approval(name), f"ne devrait PAS être gardé : {name}"


def test_dry_run_par_defaut_libre_execution_reelle_gardee():
    for name in ["sync_directories", "organize_directory", "batch_rename"]:
        assert not requires_approval(name, {"directory": "x"}), name            # défaut dry_run=True
        assert not requires_approval(name, {"dry_run": True}), name
        assert requires_approval(name, {"dry_run": False}), name
        assert requires_approval(name, {"dry_run": "false"}), name              # LLM qui envoie une chaîne


def test_git_docker_sql_selon_action():
    assert not requires_approval("git_control", {"action": "status"})
    assert not requires_approval("git_control", {"action": "diff"})
    assert requires_approval("git_control", {"action": "commit", "message": "x"})
    assert requires_approval("git_control", {"action": "ADD"})
    assert not requires_approval("docker_control", {"action": "ps"})
    assert requires_approval("docker_control", {"action": "run", "image": "x"})
    assert not requires_approval("run_sql", {"query": "select 1"})            # mode read par défaut
    assert requires_approval("run_sql", {"query": "delete", "mode": "write"})


def test_sans_arguments_on_garde_par_prudence():
    # Appel par le seul nom : l'humain tranche plutôt que de deviner l'option.
    for name in ["sync_directories", "git_control", "docker_control", "run_sql"]:
        assert requires_approval(name), name
        assert requires_approval(name, None), name
        assert requires_approval(name, "pas un dict"), name  # type: ignore[arg-type]


def test_chaque_outil_natif_libre_est_un_choix_ecrit():
    """Un nouvel outil natif doit être classé ICI, pas laissé libre par défaut.

    C'est le défaut « libre si inconnu » qui avait laissé passer AppleScript et
    les Raccourcis. Ajouter un outil au registre fait rougir ce test tant que
    personne n'a décidé s'il est gardé.
    """
    import tools.registry as registre

    noms: set[str] = set()
    for valeur in vars(registre).values():
        if isinstance(valeur, list):
            for outil in valeur:
                if isinstance(outil, dict) and "function" in outil:
                    noms.add(outil["function"]["name"])
    libres_attendus = {
        # lecture, recherche, inspection
        "analyser_image", "analyze_audio", "analyze_dependencies", "await_distillation",
        "browse_repo", "code_graph", "extract_best_practices", "find_references",
        "find_relevant_files", "find_symbol", "get_skills", "get_waveform_data",
        "kubectl_control", "library_catalog", "list_files", "list_imports",
        "list_indexed_repos", "list_previews", "list_shortcuts", "list_skills",
        "list_tool_kinds", "mqtt_subscribe", "preview_file", "rappeler_memoire",
        "read_file", "read_github_file", "search_books", "search_in_files",
        "spotlight_search", "library_web_search", "library_source_status",
        # artefacts confinés ou code rendu sans écriture (choix documentés)
        "bundle_zip", "generate_excel", "generate_text_file", "generate_uml",
        "preview_code", "scaffold_api", "scaffold_nosql", "scaffold_sdk", "speak",
        # UI locale bénigne, mémoire demandée explicitement
        "open_in_pycharm", "reveal_in_finder", "stop_preview_server",
        "remember_fact", "forget_fact", "ask_user",
        # gardés selon leurs arguments (cf. _ARG_GATED_TOOLS) : libres sans option d'écriture
        "batch_rename", "docker_control", "git_control", "organize_directory",
        "run_sql", "sync_directories",
    }
    args_lecture = {"git_control": {"action": "status"}, "docker_control": {"action": "ps"},
                    "run_sql": {"mode": "read"}}
    libres = {n for n in noms if not requires_approval(n, args_lecture.get(n, {}))}
    assert libres <= libres_attendus, f"outils libres non classés : {sorted(libres - libres_attendus)}"
