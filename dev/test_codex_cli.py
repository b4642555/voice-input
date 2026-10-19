"""Transport lifecycle, subscription-only routing and isolation of private context."""
from collections import deque
import os
import queue
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch
from voiceinput import codex_cli as gpt


class AccountStatusTests(unittest.TestCase):
    def test_identity_uses_official_account_without_inference_and_closes_worker(self):
        with patch.object(gpt, 'login_state', return_value='ok'), patch.object(gpt, '_Server') as factory:
            factory.return_value.account = {'type': 'chatgpt', 'email': 'person@example.test'}
            self.assertEqual(gpt.login_status(), ('ok', 'person@example.test'))
            factory.return_value.ask.assert_not_called()
            factory.return_value.close.assert_called_once()

    def test_signed_out_does_not_start_account_worker(self):
        for stage in ('out', 'missing'):
            with patch.object(gpt, 'login_state', return_value=stage), patch.object(gpt, '_Server') as factory:
                self.assertEqual(gpt.login_status(), (stage, ''))
                factory.assert_not_called()

    def test_failed_identity_lookup_cannot_show_previous_account(self):
        with patch.object(gpt, 'login_state', return_value='ok'), \
                patch.object(gpt, '_Server', side_effect=gpt.CodexError('unavailable')):
            self.assertEqual(gpt.login_status(), ('unknown', ''))


class ServerProtocolTests(unittest.TestCase):
    def server(self, account='chatgpt', status='completed', partial=False):
        server = gpt._Server.__new__(gpt._Server)
        server.events, server.pending, server.seq = queue.Queue(), deque(), 0
        server.config, server.folder = {}, Mock(name='empty folder')
        server.folder.name = 'empty-folder'
        self.sent = []
        def send(event):
            self.sent.append(event)
            method, seq = event.get('method'), event.get('id')
            if method == 'account/read':
                result = {'account': {'type': account}}
            elif method == 'thread/start':
                result = {'thread': {'id': 'this-thread'}}
            else:
                result = {'turn': {'id': 'this-turn'}}
            server.events.put({'id': seq, 'result': result})
            if method == 'turn/start':
                # Foreign and previous-turn messages must never reach the paste.
                for tid, turn, text in [('other-thread', 'this-turn', 'foreign'),
                                         ('this-thread', 'old-turn', 'stale'),
                                         ('this-thread', 'this-turn', '正確的文字')]:
                    server.events.put({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': turn,
                        'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': text}}})
                if not partial:
                    server.events.put({'method': 'turn/completed', 'params': {'threadId': 'this-thread',
                        'turn': {'id': 'this-turn', 'status': status}}})
        server._send = send
        return server

    def test_prompt_and_context_use_ephemeral_thread_over_stdin(self):
        server = self.server()
        result = server.ask([{'role': 'system', 'content': '只潤稿'},
                             {'role': 'user', 'content': '私人畫面文字'}], gpt.MODEL, time.monotonic()+1)
        self.assertEqual(result, '正確的文字')
        params = self.sent[1]['params']
        self.assertEqual(params['baseInstructions'], '只潤稿')
        self.assertTrue(params['ephemeral'])
        self.assertEqual(params['environments'], [])
        self.assertEqual(self.sent[2]['params']['input'][0]['text'], '私人畫面文字')
        self.assertEqual(self.sent[2]['params']['serviceTierForTurn'], 'default')

    def test_fast_tier_is_set_on_the_actual_turn(self):
        self.server().ask([],gpt.MODEL,time.monotonic()+1,fast=True)
        self.assertEqual(self.sent[2]['params']['serviceTierForTurn'],'priority')

    def test_api_login_never_receives_private_text(self):
        server = self.server(account='apiKey')
        with self.assertRaises(gpt.CodexError):
            server.ask([{'role':'user','content':'private'}],gpt.MODEL,time.monotonic()+1)
        self.assertEqual([x['method'] for x in self.sent], ['account/read'])

    def test_partial_or_failed_turn_never_pasted(self):
        for status, partial in [('failed',False),('interrupted',False),('completed',True)]:
            with self.subTest(status=status,partial=partial), self.assertRaises(gpt.CodexError):
                self.server(status=status,partial=partial).ask([],gpt.MODEL,time.monotonic()+.02)

    def test_rpc_error_does_not_echo_private_screen(self):
        server=self.server()
        server._send=lambda e: server.events.put({'id':e['id'],'error':{'message':'private screen'}})
        with self.assertRaises(gpt.CodexError) as caught:
            server._rpc('test',{},time.monotonic()+1)
        self.assertNotIn('private screen',str(caught.exception))

    def test_environment_removes_keys_and_parent_chat(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret','CODEX_API_KEY':'secret','CODEX_HOME':'saved-auth',
                                  'CODEX_APP_TOOLS_PIPE_PATH':'parent','CODEX_THREAD_ID':'parent'}):
            env=gpt._environment()
        self.assertEqual(env['CODEX_HOME'],'saved-auth')
        for key in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_APP_TOOLS_PIPE_PATH','CODEX_THREAD_ID'):
            self.assertNotIn(key,env)

    def test_config_disables_existing_servers_without_copying_secrets(self):
        with patch.object(gpt.Path,'read_text',return_value='[mcp_servers."a.b"]\nurl="https://private"\nbearer_token="secret"'):
            config=gpt._config()
        self.assertEqual(config['mcp_servers'],{'a.b':{'enabled':False}})
        self.assertNotIn('secret',str(config))
        self.assertEqual(config['forced_login_method'],'chatgpt')
        self.assertEqual(config['history.persistence'],'none')

    def test_login_status_rejects_api_key(self):
        with patch.object(gpt,'cli_path',return_value='codex.exe'),patch.object(gpt.subprocess,'run') as run:
            for text, expected in [('Logged in using ChatGPT','ok'),('Logged in using an API key','out')]:
                run.return_value=subprocess.CompletedProcess([],0,'',text)
                self.assertEqual(gpt.login_state(),expected)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        gpt._closed=False
        gpt._starting=False
        gpt._standby=None
        gpt._retry_at=0
        gpt._ready.clear()

    def tearDown(self):
        gpt._ready.wait(1)
        gpt.shutdown()
        gpt._closed=False
        gpt._retry_at=0

    def test_preparation_does_not_infer_and_each_used_worker_is_disposed(self):
        first, second=Mock(), Mock()
        first.ask.return_value='完成'
        with patch.object(gpt,'_Server',side_effect=[first,second]):
            gpt.warm_up(); self.assertTrue(gpt._ready.wait(1))
            first.ask.assert_not_called()
            self.assertEqual(gpt.chat([],1),'完成')
            first.close.assert_called_once()
            self.assertTrue(gpt._ready.wait(1))
            self.assertIs(gpt._standby,second)

    def test_failure_cooldown_skips_model_and_can_be_reset(self):
        first, second=Mock(),Mock()
        first.ask.side_effect=gpt.CodexError('逾時')
        with patch.object(gpt,'_Server',side_effect=[first,second,Mock()]):
            gpt.warm_up();gpt._ready.wait(1)
            with self.assertRaises(gpt.CodexError):gpt.chat([],1)
            gpt._ready.wait(1)
            started=time.monotonic()
            with self.assertRaises(gpt.CodexError):gpt.chat([],1)
            self.assertLess(time.monotonic()-started,.1)
            second.ask.assert_not_called()
            gpt.retry_now();gpt._ready.wait(1)
            self.assertEqual(gpt._retry_at,0)

if __name__=='__main__': unittest.main()
