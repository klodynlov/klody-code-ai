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


def test_server_policy_does_not_leak_to_other_servers():
    # « laser_arm » sur un autre serveur : règle générique (verbe de tête inconnu, pas de verbe fort).
    assert not requires_approval("mcp__gadget__laser_arm")
