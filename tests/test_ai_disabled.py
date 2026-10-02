import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ai import interaction_agent

class AIDisabledTests(unittest.TestCase):
    def test_user_pause_blocks_provider_and_network(self):
        with patch.dict('os.environ', {'SOLAR_SYSTEM_AI_DISABLED':'1'}), patch.object(interaction_agent, 'urlopen') as network:
            self.assertFalse(interaction_agent.get_ai_status()['ready'])
            with self.assertRaisesRegex(RuntimeError, 'ausgeschaltet'):
                interaction_agent._call_responses_api({'input': []})
            network.assert_not_called()

    def test_file_pause_blocks_local_model_without_loading_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'.env.local').write_text('SOLAR_SYSTEM_AI_DISABLED=1\nSOLAR_SYSTEM_AI_PROVIDER=ollama\n',encoding='utf-8')
            with patch.dict('os.environ', {}, clear=True), patch.object(interaction_agent,'PROJECT_ROOT',Path(tmp)), patch.object(interaction_agent,'urlopen') as network:
                status=interaction_agent.get_ai_status()
                self.assertFalse(status['ready'])
                self.assertIn('ausgeschaltet',status['message'])
                network.assert_not_called()
