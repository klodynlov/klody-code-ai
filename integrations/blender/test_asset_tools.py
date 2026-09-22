import asyncio
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
import asset_library_tools as tools


class AssetToolsTest(unittest.TestCase):
    def test_only_allowlisted_operation(self):
        with self.assertRaises(ValueError):
            tools.make_code('__import__("os")', {})

    def test_json_keeps_collection_names_as_data(self):
        evil = 'Crane " \\ \'\nraise RuntimeError("injected")'
        code = tools.make_code('import_asset_collection', {'collection_name': evil})
        # Replace runtime body, then execute the actual generated dispatch.
        runtime = tools._RUNTIME.read_text()
        captured = {}
        def fake(**args):
            captured.update(args)
        # nosec B102 - exec volontaire : ce test VÉRIFIE que la charge d'injection
        # ci-dessus est bien échappée par make_code. Le code exécuté est celui que
        # produit le dispatch, avec un stub à la place du runtime Blender.
        exec(code[len(runtime):], {'import_asset_collection': fake})  # nosec B102
        self.assertEqual(captured, {'collection_name': evil})

    def test_schema_and_dispatch(self):
        async def run():
            server = FastMCP('test-assets')
            tools.register(server)
            schema = {t.name: t for t in await server.list_tools()}
            self.assertEqual(set(schema), tools.TOOL_NAMES)
            self.assertTrue(schema['list_asset_library_assets'].annotations.readOnlyHint)
            self.assertIn('collection_name', schema['import_asset_collection'].inputSchema['required'])
            with patch.object(tools, 'send_code', return_value={'status':'ok','result':{}}) as send:
                await server.call_tool('import_asset_collection', {'collection_name':'Crane'})
                self.assertEqual(send.call_count,1)
                self.assertTrue(send.call_args.kwargs['strict_json'])
                self.assertIn('"collection_name": "Crane"', send.call_args.args[0])
        asyncio.run(run())


if __name__=='__main__':
    unittest.main()
