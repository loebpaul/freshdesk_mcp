"""Exercise the real registered MCP server with an in-memory protocol and offline HTTP."""
import json
import unittest
from unittest.mock import patch

import httpx
from jsonschema import Draft202012Validator
from mcp.shared.memory import create_connected_server_and_client_session

import freshdesk_mcp.server as server


class MCPCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_and_ticket_transport(self):
        requests = []
        real_client = httpx.AsyncClient

        def respond(request):
            requests.append(request)
            self.assertEqual(request.url.host, "security-contract.invalid")
            self.assertEqual(request.url.path, "/api/v2/tickets")
            self.assertEqual(request.url.params["page"], "1")
            return httpx.Response(
                200, json=[{"id": 42, "subject": "Synthetic offline ticket"}],
                headers={"Link": '<https://security-contract.invalid/api/v2/tickets?page=2>; rel="next"'},
            )

        with patch.object(server, "FRESHDESK_DOMAIN", "security-contract.invalid"), \
             patch.object(server, "FRESHDESK_API_KEY", "synthetic-offline-key"), \
             patch.object(server.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                 transport=httpx.MockTransport(respond), **kw
             )):
            async with create_connected_server_and_client_session(server.mcp) as client:
                catalog = await client.list_tools()
                tools = {tool.name: tool for tool in catalog.tools}
                self.assertTrue({"get_tickets", "create_ticket", "delete_ticket", "search_solution_articles"} <= tools.keys())
                for tool in catalog.tools:
                    Draft202012Validator.check_schema(tool.inputSchema)
                result = await client.call_tool("get_tickets", {"page": 1, "per_page": 30})
                self.assertFalse(result.isError)
                data = json.loads(result.content[0].text)
                self.assertEqual(data["tickets"][0]["id"], 42)
                self.assertEqual(data["pagination"]["next_page"], 2)
                self.assertEqual(len(requests), 1)
                invalid = await client.call_tool("get_tickets", {"page": 0})
                self.assertIn("Page number", json.loads(invalid.content[0].text)["error"])
                missing = await client.call_tool("get_ticket", {})
                self.assertTrue(missing.isError)
                self.assertEqual(len(requests), 1, "invalid requests must not reach HTTP")

    async def test_http_failure_is_returned_over_mcp(self):
        real_client = httpx.AsyncClient
        with patch.object(server, "FRESHDESK_DOMAIN", "security-contract.invalid"), \
             patch.object(server, "FRESHDESK_API_KEY", "synthetic-offline-key"), \
             patch.object(server.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                 transport=httpx.MockTransport(lambda request: httpx.Response(403, json={"error": "denied"})), **kw
             )):
            async with create_connected_server_and_client_session(server.mcp) as client:
                result = await client.call_tool("get_tickets", {})
                self.assertIn("Failed to fetch tickets", json.loads(result.content[0].text)["error"])
