"""Blender MCP tools backed by the native asset-library data API."""
import json
from pathlib import Path

from blmcp.tools_helpers.connection import send_code
from mcp.types import ToolAnnotations

_RUNTIME = Path(__file__).with_name('asset_library_runtime.py')
TOOL_NAMES = {'list_asset_library_assets', 'import_asset_collection'}


def make_code(operation, arguments):
    if operation not in TOOL_NAMES:
        raise ValueError('Unknown asset-library operation')
    # A JSON string literal keeps quotes, paths and collection names as data.
    payload = repr(json.dumps(arguments, ensure_ascii=False))
    return (_RUNTIME.read_text() + '\nimport json\n'
            + f'result = {operation}(**json.loads({payload}))\n')


def register(mcp):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def list_asset_library_assets(library_name: str = 'Z-Anatomy') -> dict:
        """Liste les collections importables d'une bibliothèque d'assets Blender (Z-Anatomy).

        Z-Anatomy est une asset library, pas un addon. Lire cette liste avant de
        conclure qu'elle manque. Renvoie collection_name et blend_file exacts pour
        import_asset_collection. library_name vide liste les bibliothèques enregistrées.
        Lecture seule ; fonctionne sans ouvrir l'Asset Browser.
        """
        return send_code(make_code('list_asset_library_assets', locals()), strict_json=True)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    def import_asset_collection(collection_name: str, library_name: str = 'Z-Anatomy',
                                blend_file: str = '', scene_name: str = '',
                                reuse_existing: bool = True) -> dict:
        """Importe par script une collection d'assets Z-Anatomy/Blender, sans interface graphique.

        Appeler list_asset_library_assets puis reprendre collection_name et blend_file
        exacts. Utilise bpy.data.libraries.load(link=False) et relie la collection à la
        scène actuelle (ou scene_name existante). Préserve objets, sélection et fichier
        actuels, ne sauvegarde pas. Les annotations importées sont masquées. Une répétition
        réutilise l'import existant par défaut ; false demande explicitement une copie.
        Après timeout, relire la scène avant de réessayer. Vérifier status/result.
        """
        return send_code(make_code('import_asset_collection', locals()), strict_json=True)
