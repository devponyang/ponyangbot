import unittest
from unittest.mock import Mock
from requests import HTTPError
from sheets import sync_error_message


class SyncErrorTests(unittest.TestCase):
    def test_protected_title_identified_without_response_leak(self):
        response = Mock(status_code=400)
        response.json.return_value = {'error': {'message': 'Invalid data[1]: You are trying to edit a protected cell or object. private-detail'}}
        result = sync_error_message(HTTPError(response=response))
        self.assertIn('F2', result)
        self.assertIn('HTTP 400', result)
        self.assertNotIn('private-detail', result)

    def test_unknown_error_does_not_print_request_url(self):
        response = Mock(status_code=503)
        response.json.side_effect = ValueError('not JSON')
        result = sync_error_message(HTTPError('private-url', response=response))
        self.assertIn('HTTP 503', result)
        self.assertNotIn('private-url', result)
