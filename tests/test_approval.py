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


def test_unknown_internal_tool_defaults_to_pass():
    # Outil interne inconnu (non MCP) → pas de gate (lecture présumée).
    assert not requires_approval("some_future_inspect_tool")


def test_mcp_unknown_head_falls_back_to_strong_write_verb():
    # Verbe de tête inconnu mais verbe mutateur fort présent → gardé.
    assert requires_approval("mcp__srv__bulk_create_items")
    # Verbe de tête inconnu sans verbe mutateur fort → laissé passer.
    assert not requires_approval("mcp__srv__fuzzy_lookup_entries")
