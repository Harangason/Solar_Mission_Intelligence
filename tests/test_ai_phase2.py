import tempfile
import unittest
import io
import json
from pathlib import Path
from unittest.mock import patch

from ai import audit_log
from ai import interaction_agent
from ai.interaction_agent import generate_mission_chat


def mission_state():
    return {
        "schemaVersion": "1.0",
        "startDate": "2026-07-31",
        "originId": "earth",
        "targetId": "jupiter",
        "waypointIds": ["jupiter"],
        "routeSections": [{"id": "section-1", "originId": "earth", "targetId": "jupiter"}],
        "constraints": {"maxDeltaVKmS": 2.0, "maxDurationDays": 3650},
        "solverRunId": "route-123",
    }


def solver_result():
    return {
        "schemaVersion": "1.0",
        "runId": "route-123",
        "solverType": "segmented-route",
        "status": "best-effort",
        "result": {"totalFlightDays": 900, "targetCorrectionDeltaVKmS": 0.4},
        "validation": {
            "solverValid": False,
            "nBodyValid": None,
            "errors": [],
            "warnings": ["Keine Flugfreigabe."],
        },
    }


def api_response(structured):
    import json
    return {
        "id": "resp-123",
        "model": "test-model",
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": json.dumps(structured)}],
        }],
    }


class InteractionAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.logs = {role: root / f"{role}.jsonl" for role in audit_log.AI_AUDIT_LOGS}
        self.patches = [
            patch.object(audit_log, "PROJECT_ROOT", root),
            patch.object(audit_log, "AI_AUDIT_LOGS", self.logs),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_solver_context_and_allowlisted_action_are_forwarded(self):
        captured = {}

        def fake_api(request_payload):
            captured.update(request_payload)
            return api_response({
                "reply": "Der Lauf route-123 hat keine Flugfreigabe.",
                "basedOnSolverRunIds": ["route-123"],
                "proposedActions": [{
                    "type": "focus-route-section",
                    "sectionId": "section-1",
                    "projection": None,
                    "requiresConfirmation": True,
                }],
            })

        result = generate_mission_chat({
            "message": "Erklaere die Route.",
            "missionState": mission_state(),
            "solverResult": solver_result(),
            "viewState": {"projection": "top", "activeRouteSectionId": "section-1"},
        }, api_caller=fake_api)

        self.assertEqual(result["basedOnSolverRunIds"], ["route-123"])
        self.assertEqual(result["proposedActions"][0]["type"], "focus-route-section")
        self.assertIn("route-123", captured["input"][0]["content"])
        self.assertFalse(captured["store"])
        self.assertEqual(audit_log.read_latest_ai_audit("interaction")["status"], "success")

    def test_unknown_action_is_rejected_and_audited(self):
        def fake_api(_request_payload):
            return api_response({
                "reply": "Ich starte etwas.",
                "basedOnSolverRunIds": [],
                "proposedActions": [{
                    "type": "delete-project",
                    "sectionId": None,
                    "projection": None,
                    "requiresConfirmation": True,
                }],
            })

        with self.assertRaisesRegex(ValueError, "Nicht erlaubte"):
            generate_mission_chat({
                "message": "Loesche das Projekt.",
                "missionState": mission_state(),
            }, api_caller=fake_api)
        self.assertEqual(audit_log.read_latest_ai_audit("interaction")["status"], "rejected")

    def test_unprovided_solver_reference_is_rejected(self):
        def fake_api(_request_payload):
            return api_response({
                "reply": "Lauf erfunden-1 sagt 12 Tage.",
                "basedOnSolverRunIds": ["erfunden-1"],
                "proposedActions": [],
            })

        with self.assertRaisesRegex(ValueError, "nicht uebergebenen Solver-Lauf"):
            generate_mission_chat({
                "message": "Wie lange dauert es?",
                "missionState": mission_state(),
            }, api_caller=fake_api)

    def test_blank_project_cannot_start_solver(self):
        state = mission_state()
        state['routeSections'] = []
        with self.assertRaisesRegex(ValueError, 'Ohne Routenabschnitt'):
            generate_mission_chat({'message': 'Berechne die Route.', 'missionState': state},
                api_caller=lambda _: api_response({'reply': 'Solver starten.', 'basedOnSolverRunIds': [],
                    'proposedActions': [{'type': 'run-route-solver', 'sectionId': None,
                        'projection': None, 'requiresConfirmation': True}]}))

    def test_invalid_model_output_is_rejected_and_audited(self):
        with self.assertRaisesRegex(ValueError, 'JSON-Objekt'):
            generate_mission_chat({'message': 'Erklaere die Ansicht.', 'missionState': mission_state()},
                api_caller=lambda _: api_response([]))
        self.assertEqual(audit_log.read_latest_ai_audit('interaction')['status'], 'rejected')

    def test_blank_project_receives_real_context_without_solver(self):
        state = mission_state()
        state['routeSections'] = []
        state['solverRunId'] = None
        captured = {}
        def fake_api(payload):
            captured.update(payload)
            return api_response({'reply': 'Leeres Projekt. Lege mit + Neu einen Abschnitt an.',
                'basedOnSolverRunIds': [], 'proposedActions': []})
        result = generate_mission_chat({'message': 'Was soll ich tun?', 'missionState': state,
            'viewState': {'projection': 'corridor'}}, api_caller=fake_api)
        self.assertEqual(result['basedOnSolverRunIds'], [])
        context = json.loads(captured['input'][0]['content'].split('\n', 1)[1])
        self.assertEqual(context['missionState']['routeSections'], [])
        self.assertIsNone(context['solverResult'])


class LocalModelTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict('os.environ', {'SOLAR_SYSTEM_AI_PROVIDER': 'ollama',
            'OLLAMA_MODEL': 'qwen3.8:27b', 'OLLAMA_URL': 'http://127.0.0.1:11434',
            'OPENAI_API_KEY': 'DEIN_API_KEY'}, clear=True)
        self.root = patch.object(interaction_agent, 'PROJECT_ROOT', Path(self.directory.name))
        self.env.start()
        self.root.start()

    def tearDown(self):
        self.root.stop()
        self.env.stop()
        self.directory.cleanup()

    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode('utf-8'))

    def payload(self):
        return {'model': 'ignored-openai-model', 'instructions': 'Keine Solverwerte erfinden.',
            'input': [{'role': 'developer', 'content': 'Aktueller Zustand'},
                      {'role': 'user', 'content': 'Erklaere die Ansicht.'}],
            'text': {'format': {'schema': interaction_agent.INTERACTION_RESPONSE_SCHEMA}},
            'max_output_tokens': 600}

    def test_local_status_checks_completion_without_generation_or_key(self):
        with patch.object(interaction_agent, 'urlopen', return_value=self.response({'capabilities': ['completion']})) as call:
            status = interaction_agent.get_ai_status()
        self.assertTrue(status['ready'])
        self.assertEqual(status['provider'], 'ollama')
        self.assertEqual(status['model'], 'qwen3.8:27b')
        self.assertFalse(status['audio']['transcription'])
        self.assertEqual(call.call_args.args[0].full_url, 'http://127.0.0.1:11434/api/show')

    def test_local_structured_response_uses_shared_contract(self):
        reply = {'reply': 'Die Ansicht zeigt den Korridor.', 'basedOnSolverRunIds': [], 'proposedActions': []}
        with patch.object(interaction_agent, 'urlopen', side_effect=[
            self.response({'capabilities': ['completion']}),
            self.response({'model': 'qwen3.8:27b', 'message': {'content': json.dumps(reply)}, 'done_reason': 'stop'})
        ]) as call:
            result = interaction_agent._call_responses_api(self.payload())
        request = call.call_args.args[0]
        local = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://127.0.0.1:11434/api/chat')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(local['model'], 'qwen3.8:27b')
        self.assertFalse(local['stream'])
        self.assertFalse(local['think'])
        self.assertEqual(local['format'], interaction_agent.INTERACTION_RESPONSE_SCHEMA)
        self.assertEqual(local['messages'][1]['role'], 'system')
        self.assertEqual(json.loads(interaction_agent._extract_output_text(result)), reply)

    def test_cloud_alias_is_rejected_before_generation(self):
        with patch.object(interaction_agent, 'urlopen', return_value=self.response(
            {'capabilities': ['completion'], 'remote_host': 'https://ollama.com'})) as call:
            self.assertFalse(interaction_agent.get_ai_status()['ready'])
        self.assertEqual(call.call_count, 1)

    def test_local_calculation_limits_output_without_mutating_contract(self):
        from ai.calculation_agent import CALCULATION_RESPONSE_SCHEMA
        payload = self.payload()
        payload['text']['format'] = {'name': 'calculation_suggestion_response', 'schema': CALCULATION_RESPONSE_SCHEMA}
        with patch.object(interaction_agent, 'urlopen', side_effect=[
            self.response({'capabilities': ['completion']}),
            self.response({'message': {'content': '{}'}, 'done_reason': 'stop'})
        ]) as call:
            interaction_agent._call_responses_api(payload)
        schema = json.loads(call.call_args.args[0].data)['format']
        self.assertEqual(schema['properties']['candidateSeeds']['maxItems'], 1)
        self.assertEqual(schema['properties']['searchWindows']['maxItems'], 1)
        self.assertEqual(CALCULATION_RESPONSE_SCHEMA['properties']['candidateSeeds']['maxItems'], 12)
        self.assertEqual(CALCULATION_RESPONSE_SCHEMA['properties']['searchWindows']['maxItems'], 6)

    def test_non_loopback_provider_is_rejected(self):
        with patch.dict('os.environ', {'OLLAMA_URL': 'https://example.com'}), patch.object(interaction_agent, 'urlopen') as call:
            self.assertFalse(interaction_agent.get_ai_status()['ready'])
        call.assert_not_called()

    def test_offline_server_is_not_ready(self):
        from urllib.error import URLError
        with patch.object(interaction_agent, 'urlopen', side_effect=URLError('offline')):
            status = interaction_agent.get_ai_status()
        self.assertFalse(status['ready'])
        self.assertIn('nicht erreichbar', status['message'])

    def test_missing_model_is_not_ready(self):
        from urllib.error import HTTPError
        with patch.object(interaction_agent, 'urlopen', side_effect=HTTPError('http://localhost', 404, '', {}, None)):
            status = interaction_agent.get_ai_status()
        self.assertFalse(status['ready'])
        self.assertIn('nicht installiert', status['message'])

    def test_truncated_model_reply_is_recoverable_error(self):
        with patch.object(interaction_agent, 'urlopen', side_effect=[
            self.response({'capabilities': ['completion']}),
            self.response({'message': {'content': '{"reply":'}, 'done_reason': 'length'})
        ]):
            with self.assertRaisesRegex(RuntimeError, 'vollstaendige Antwort'):
                interaction_agent._call_responses_api(self.payload())

    def test_placeholder_is_rejected_without_network(self):
        with patch.dict('os.environ', {'SOLAR_SYSTEM_AI_PROVIDER': 'openai'}), patch.object(interaction_agent, 'urlopen') as call:
            status = interaction_agent.get_ai_status()
        self.assertFalse(status['ready'])
        self.assertIn('Kein gueltiger', status['message'])
        call.assert_not_called()

    def test_local_config_is_loaded_from_ignored_file(self):
        (Path(self.directory.name) / '.env.local').write_text('SOLAR_SYSTEM_AI_PROVIDER=ollama\nOLLAMA_MODEL=test-local\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(interaction_agent.configured_model('interaction'), 'test-local')


if __name__ == "__main__":
    unittest.main()
