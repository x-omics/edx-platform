"""
Test the various password reset flows
"""
import json
import re
import unittest

from django.core.cache import cache
from django.conf import settings
from django.test import TestCase
from django.test.client import RequestFactory
from django.contrib.auth.models import User
from django.contrib.auth.hashers import UNUSABLE_PASSWORD
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import int_to_base36

from mock import Mock, patch
import ddt

from student.views import password_reset, password_reset_confirm_wrapper
from student.tests.factories import UserFactory
from student.tests.test_email import mock_render_to_string


@ddt.ddt
class ResetPasswordTests(TestCase):
    """ Tests that clicking reset password sends email, and doesn't activate the user
    """
    request_factory = RequestFactory()

    def setUp(self):
        self.user = UserFactory.create()
        self.user.is_active = False
        self.user.save()
        self.token = default_token_generator.make_token(self.user)
        self.uidb36 = int_to_base36(self.user.id)

        self.user_bad_passwd = UserFactory.create()
        self.user_bad_passwd.is_active = False
        self.user_bad_passwd.password = UNUSABLE_PASSWORD
        self.user_bad_passwd.save()

    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_user_bad_password_reset(self):
        """Tests password reset behavior for user with password marked UNUSABLE_PASSWORD"""

        bad_pwd_req = self.request_factory.post('/password_reset/', {'email': self.user_bad_passwd.email})
        bad_pwd_resp = password_reset(bad_pwd_req)
        # If they've got an unusable password, we return a successful response code
        self.assertEquals(bad_pwd_resp.status_code, 200)
        obj = json.loads(bad_pwd_resp.content)
        self.assertEquals(obj, {
            'success': True,
            'value': "('registration/password_reset_done.html', [])",
        })

    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_nonexist_email_password_reset(self):
        """Now test the exception cases with of reset_password called with invalid email."""

        bad_email_req = self.request_factory.post('/password_reset/', {'email': self.user.email + "makeItFail"})
        bad_email_resp = password_reset(bad_email_req)
        # Note: even if the email is bad, we return a successful response code
        # This prevents someone potentially trying to "brute-force" find out which
        # emails are and aren't registered with edX
        self.assertEquals(bad_email_resp.status_code, 200)
        obj = json.loads(bad_email_resp.content)
        self.assertEquals(obj, {
            'success': True,
            'value': "('registration/password_reset_done.html', [])",
        })

    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_password_reset_ratelimited(self):
        """ Try (and fail) resetting password 30 times in a row on an non-existant email address """
        cache.clear()

        for i in xrange(30):
            good_req = self.request_factory.post('/password_reset/', {
                'email': 'thisdoesnotexist{0}@foo.com'.format(i)
            })
            good_resp = password_reset(good_req)
            self.assertEquals(good_resp.status_code, 200)

        # then the rate limiter should kick in and give a HttpForbidden response
        bad_req = self.request_factory.post('/password_reset/', {'email': 'thisdoesnotexist@foo.com'})
        bad_resp = password_reset(bad_req)
        self.assertEquals(bad_resp.status_code, 403)

        cache.clear()

    @unittest.skipUnless(settings.ROOT_URLCONF == 'lms.urls', "Test only valid in LMS")
    @patch('django.core.mail.send_mail')
    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_reset_password_email(self, send_email):
        """Tests contents of reset password email, and that user is not active"""

        good_req = self.request_factory.post('/password_reset/', {'email': self.user.email})
        good_resp = password_reset(good_req)
        self.assertEquals(good_resp.status_code, 200)
        obj = json.loads(good_resp.content)
        self.assertEquals(obj, {
            'success': True,
            'value': "('registration/password_reset_done.html', [])",
        })

        (subject, msg, from_addr, to_addrs) = send_email.call_args[0]
        self.assertIn("Password reset", subject)
        self.assertIn("You're receiving this e-mail because you requested a password reset", msg)
        self.assertEquals(from_addr, settings.DEFAULT_FROM_EMAIL)
        self.assertEquals(len(to_addrs), 1)
        self.assertIn(self.user.email, to_addrs)

        #test that the user is not active
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(self.user.is_active)
        re.search(r'password_reset_confirm/(?P<uidb36>[0-9A-Za-z]+)-(?P<token>.+)/', msg).groupdict()

    @unittest.skipUnless(settings.ROOT_URLCONF == 'lms.urls', "Test only valid in LMS")
    @patch('django.core.mail.send_mail')
    @ddt.data((False, 'http://'), (True, 'https://'))
    @ddt.unpack
    def test_reset_password_email_https(self, is_secure, protocol, send_email):
        """
        Tests that the right url protocol is included in the reset password link
        """
        req = self.request_factory.post(
            '/password_reset/', {'email': self.user.email}
        )
        req.is_secure = Mock(return_value=is_secure)
        resp = password_reset(req)
        _, msg, _, _ = send_email.call_args[0]
        expected_msg = "Please go to the following page and choose a new password:\n\n" + protocol

        self.assertIn(expected_msg, msg)

    @patch('student.views.password_reset_confirm')
    def test_reset_password_bad_token(self, reset_confirm):
        """Tests bad token and uidb36 in password reset"""

        bad_reset_req = self.request_factory.get('/password_reset_confirm/NO-OP/')
        password_reset_confirm_wrapper(bad_reset_req, 'NO', 'OP')
        confirm_kwargs = reset_confirm.call_args[1]
        self.assertEquals(confirm_kwargs['uidb36'], 'NO')
        self.assertEquals(confirm_kwargs['token'], 'OP')
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(self.user.is_active)

    @patch('student.views.password_reset_confirm')
    def test_reset_password_good_token(self, reset_confirm):
        """Tests good token and uidb36 in password reset"""

        good_reset_req = self.request_factory.get('/password_reset_confirm/{0}-{1}/'.format(self.uidb36, self.token))
        password_reset_confirm_wrapper(good_reset_req, self.uidb36, self.token)
        confirm_kwargs = reset_confirm.call_args[1]
        self.assertEquals(confirm_kwargs['uidb36'], self.uidb36)
        self.assertEquals(confirm_kwargs['token'], self.token)
        self.user = User.objects.get(pk=self.user.pk)
        self.assertTrue(self.user.is_active)

    @patch('student.views.password_reset_confirm')
    def test_reset_password_with_reused_password(self, reset_confirm):
        """Tests good token and uidb36 in password reset"""

        good_reset_req = self.request_factory.get('/password_reset_confirm/{0}-{1}/'.format(self.uidb36, self.token))
        password_reset_confirm_wrapper(good_reset_req, self.uidb36, self.token)
        confirm_kwargs = reset_confirm.call_args[1]
        self.assertEquals(confirm_kwargs['uidb36'], self.uidb36)
        self.assertEquals(confirm_kwargs['token'], self.token)
        self.user = User.objects.get(pk=self.user.pk)
        self.assertTrue(self.user.is_active)
