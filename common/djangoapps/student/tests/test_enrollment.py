"""
Tests for student enrollment.
"""
import ddt
import unittest

from django.test.utils import override_settings
from django.conf import settings
from django.core.urlresolvers import reverse
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase, mixed_store_config
)
from xmodule.modulestore.tests.factories import CourseFactory
from social.strategies.django_strategy import DjangoStrategy
from django.test.client import RequestFactory
from student.tests.factories import UserFactory, CourseModeFactory
from student.models import CourseEnrollment
from student.views import register_user
from third_party_auth.pipeline import change_enrollment as change_enrollment_third_party

# Since we don't need any XML course fixtures, use a modulestore configuration
# that disables the XML modulestore.
MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {}, include_xml=False)


@ddt.ddt
@override_settings(MODULESTORE=MODULESTORE_CONFIG)
@unittest.skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
class EnrollmentTest(ModuleStoreTestCase):
    """
    Test student enrollment, especially with different course modes.
    """
    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    def setUp(self):
        """ Create a course and user, then log in. """
        super(EnrollmentTest, self).setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

        self.urls = [
            reverse('course_modes_choose', kwargs={'course_id': unicode(self.course.id)})
        ]

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that we're redirected to the dashboard
        # and automatically enrolled as "honor"
        ([], '', 'honor'),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as "honor" by default.
        (['honor', 'verified', 'audit'], 'course_modes_choose', 'honor'),

        # Professional ed
        # Expect that we're sent to the "choose your track" page
        # (which will, in turn, redirect us to a page where we can verify/pay)
        # We should NOT be auto-enrolled, because that would be giving 
        # away an expensive course for free :)
        (['professional'], 'course_modes_choose', None),
    )
    @ddt.unpack
    def test_enroll(self, course_modes, next_url, enrollment_mode):
        # Create the course modes (if any) required for this test case
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

        # Reverse the expected next URL, if one is provided
        # (otherwise, use an empty string, which the JavaScript client
        # interprets as a redirect to the dashboard)
        full_url = (
            reverse(next_url, kwargs={'course_id': unicode(self.course.id)})
            if next_url else next_url
        )

        # Enroll in the course and verify the URL we get sent to
        resp = self._change_enrollment('enroll')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, full_url)

        # If we're not expecting to be enrolled, verify that this is the case
        if enrollment_mode is None:
            self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course.id))

        # Otherwise, verify that we're enrolled with the expected course mode
        else:
            self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course.id))
            course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
            self.assertTrue(is_active)
            self.assertEqual(course_mode, enrollment_mode)

    def test_enroll_from_third_party_redirect(self):
        """
        Test that, when a user visits the registration page *after* visiting a course,
        if they go on to register and/or log in via third-party auth, they'll be enrolled
        in that course.

        The testing here is a bit hackish, since we just ping the registration page, then
        directly call the step in the third party pipeline that registers the user if
        `registration_course_id` is set in the session, but it should catch any major breaks.
        """
        self.client.logout()
        self.client.get(reverse('register_user'), {'course_id': self.course.id})
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.dummy_request = RequestFactory().request()
        self.dummy_request.session = self.client.session
        strategy = DjangoStrategy(RequestFactory, request=self.dummy_request)
        change_enrollment_third_party(is_register=True, strategy=strategy, user=self.user)
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course.id))

    def test_no_prof_ed_third_party_autoenroll(self):
        """
        Test that a user authenticating via third party auth while attempting to enroll
        in a professional education course is not automatically enrolled in the course.
        """
        self.client.logout()

        # Create the course mode required for this test case
        CourseModeFactory(course_id=self.course.id, mode_slug='professional')

        self.client.get(reverse('register_user'), {'course_id': self.course.id})
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.dummy_request = RequestFactory().request()
        self.dummy_request.session = self.client.session
        strategy = DjangoStrategy(RequestFactory, request=self.dummy_request)
        change_enrollment_third_party(is_register=True, strategy=strategy, user=self.user)

        # Verify that the user has not been enrolled in the course
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course.id))

    def test_unenroll(self):
        # Enroll the student in the course
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")

        # Attempt to unenroll the student
        resp = self._change_enrollment('unenroll')
        self.assertEqual(resp.status_code, 200)

        # Expect that we're no longer enrolled
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course.id))

    def test_user_not_authenticated(self):
        # Log out, so we're no longer authenticated
        self.client.logout()

        # Try to enroll, expecting a forbidden response
        resp = self._change_enrollment('enroll')
        self.assertEqual(resp.status_code, 403)

    def test_missing_course_id_param(self):
        resp = self.client.post(
            reverse('change_enrollment'),
            {'enrollment_action': 'enroll'}
        )
        self.assertEqual(resp.status_code, 400)

    def test_unenroll_not_enrolled_in_course(self):
        # Try unenroll without first enrolling in the course
        resp = self._change_enrollment('unenroll')
        self.assertEqual(resp.status_code, 400)

    def test_invalid_enrollment_action(self):
        resp = self._change_enrollment('not_an_action')
        self.assertEqual(resp.status_code, 400)

    def test_with_invalid_course_id(self):
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")
        resp = self._change_enrollment('unenroll', course_id="edx/")
        self.assertEqual(resp.status_code, 400)

    def _change_enrollment(self, action, course_id=None):
        """Change the student's enrollment status in a course.

        Args:
            action (string): The action to perform (either "enroll" or "unenroll")

        Keyword Args:
            course_id (unicode): If provided, use this course ID.  Otherwise, use the
                course ID created in the setup for this test.

        Returns:
            Response

        """
        if course_id is None:
            course_id = unicode(self.course.id)

        params = {
            'enrollment_action': action,
            'course_id': course_id
        }
        return self.client.post(reverse('change_enrollment'), params)
