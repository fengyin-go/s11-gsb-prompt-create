import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import github_helpers as hub

class GithubRetry(unittest.TestCase):
    def result(self, code, err=''):
        return subprocess.CompletedProcess(['gh'], code, '', err)

    def test_throttled_create_retries_and_succeeds(self):
        with patch.object(hub, 'gh_binary', return_value='gh'), patch.object(hub, 'run_cmd', side_effect=[self.result(1, 'secondary rate limit; retry-after: 90'), self.result(0)]) as run, patch.object(hub.time, 'sleep') as sleep:
            hub.create_repo('selftest', 'task', 'public')
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), 90)
        self.assertTrue(all(c.args[0] <= 30 for c in sleep.call_args_list))

    def test_repository_creation_throttle_is_retried(self):
        message = 'GraphQL: You have created too many repositories, too quickly. Please try again later. (createRepository)'
        with patch.object(hub, 'gh_binary', return_value='gh'), patch.object(hub, 'run_cmd', side_effect=[self.result(1, message), self.result(0)]) as run, patch.object(hub.time, 'sleep') as sleep:
            hub.create_repo('selftest', 'task', 'public')
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), 60)

    def test_permission_error_is_not_retried_or_treated_as_absent(self):
        with patch.object(hub, 'gh_binary', return_value='gh'), patch.object(hub, 'run_cmd', return_value=self.result(1, 'HTTP 403: forbidden')) as run, patch.object(hub.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                hub.repo_exists('selftest', 'task')
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_budget_stops_before_next_retry(self):
        with patch.dict(hub.os.environ, {'S11_GITHUB_RETRY_BUDGET_SECONDS':'0'}), patch.object(hub, 'gh_binary', return_value='gh'), patch.object(hub, 'run_cmd', return_value=self.result(1, 'HTTP 429: too many requests')), patch.object(hub.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '预算'):
                hub.run_gh(['api', 'user'])
        sleep.assert_not_called()
