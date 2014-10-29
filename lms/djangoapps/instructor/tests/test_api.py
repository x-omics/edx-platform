# -*- coding: utf-8 -*-
"""
Unit tests for instructor.api methods.
"""
# pylint: disable=E1111
import unittest
import json
import requests
import datetime
import ddt
import random
import io
from urllib import quote
from django.test import TestCase
from nose.tools import raises
from mock import Mock, patch
from django.conf import settings
from django.test.utils import override_settings
from django.core.urlresolvers import reverse
from django.http import HttpRequest, HttpResponse
from django_comment_common.models import FORUM_ROLE_COMMUNITY_TA
from django_comment_common.utils import seed_permissions_roles
from django.core import mail
from django.utils.timezone import utc
from django.test import RequestFactory

from django.contrib.auth.models import User
from courseware.tests.modulestore_config import TEST_DATA_MIXED_MODULESTORE
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from courseware.tests.helpers import LoginEnrollmentTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from student.tests.factories import UserFactory
from courseware.tests.factories import StaffFactory, InstructorFactory, BetaTesterFactory
from student.roles import CourseBetaTesterRole
from microsite_configuration import microsite
from instructor.tests.utils import FakeContentTask, FakeEmail, FakeEmailInfo

from student.models import CourseEnrollment, CourseEnrollmentAllowed
from courseware.models import StudentModule

# modules which are mocked in test cases.
import instructor_task.api
import instructor.views.api
from instructor.views.api import generate_unique_password
from instructor.views.api import _split_input_list, common_exceptions_400
from instructor_task.api_helper import AlreadyRunningError
from opaque_keys.edx.locations import SlashSeparatedCourseKey
from shoppingcart.models import (
    RegistrationCodeRedemption, Order,
    PaidCourseRegistration, Coupon, Invoice, CourseRegistrationCode
)
from course_modes.models import CourseMode
from django.core.files.uploadedfile import SimpleUploadedFile
from student.models import NonExistentCourseError

from .test_tools import msk_from_problem_urlname
from ..views.tools import get_extended_due

EXPECTED_CSV_HEADER = '"code","course_id","company_name","created_by","redeemed_by","invoice_id","purchaser","customer_reference_number","internal_reference"'
EXPECTED_COUPON_CSV_HEADER = '"course_id","percentage_discount","code_redeemed_count","description"'

# ddt data for test cases involving reports
REPORTS_DATA = (
    {
        'report_type': 'grade',
        'instructor_api_endpoint': 'calculate_grades_csv',
        'task_api_endpoint': 'instructor_task.api.submit_calculate_grades_csv',
        'extra_instructor_api_kwargs': {}
    },
    {
        'report_type': 'enrolled student profile',
        'instructor_api_endpoint': 'get_students_features',
        'task_api_endpoint': 'instructor_task.api.submit_calculate_students_features_csv',
        'extra_instructor_api_kwargs': {'csv': '/csv'}
    }
)


@common_exceptions_400
def view_success(request):  # pylint: disable=W0613
    "A dummy view for testing that returns a simple HTTP response"
    return HttpResponse('success')


@common_exceptions_400
def view_user_doesnotexist(request):  # pylint: disable=W0613
    "A dummy view that raises a User.DoesNotExist exception"
    raise User.DoesNotExist()


@common_exceptions_400
def view_alreadyrunningerror(request):  # pylint: disable=W0613
    "A dummy view that raises an AlreadyRunningError exception"
    raise AlreadyRunningError()


class TestCommonExceptions400(unittest.TestCase):
    """
    Testing the common_exceptions_400 decorator.
    """

    def setUp(self):
        self.request = Mock(spec=HttpRequest)
        self.request.META = {}

    def test_happy_path(self):
        resp = view_success(self.request)
        self.assertEqual(resp.status_code, 200)

    def test_user_doesnotexist(self):
        self.request.is_ajax.return_value = False
        resp = view_user_doesnotexist(self.request)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("User does not exist", resp.content)

    def test_user_doesnotexist_ajax(self):
        self.request.is_ajax.return_value = True
        resp = view_user_doesnotexist(self.request)
        self.assertEqual(resp.status_code, 400)
        result = json.loads(resp.content)
        self.assertIn("User does not exist", result["error"])

    def test_alreadyrunningerror(self):
        self.request.is_ajax.return_value = False
        resp = view_alreadyrunningerror(self.request)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Task is already running", resp.content)

    def test_alreadyrunningerror_ajax(self):
        self.request.is_ajax.return_value = True
        resp = view_alreadyrunningerror(self.request)
        self.assertEqual(resp.status_code, 400)
        result = json.loads(resp.content)
        self.assertIn("Task is already running", result["error"])


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@patch.dict(settings.FEATURES, {'ENABLE_INSTRUCTOR_EMAIL': True, 'REQUIRE_COURSE_EMAIL_AUTH': False})
class TestInstructorAPIDenyLevels(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Ensure that users cannot access endpoints they shouldn't be able to.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        CourseEnrollment.enroll(self.user, self.course.id)

        self.problem_location = msk_from_problem_urlname(
            self.course.id,
            'robot-some-problem-urlname'
        )
        self.problem_urlname = self.problem_location.to_deprecated_string()
        _module = StudentModule.objects.create(
            student=self.user,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )

        # Endpoints that only Staff or Instructors can access
        self.staff_level_endpoints = [
            ('students_update_enrollment', {'identifiers': 'foo@example.org', 'action': 'enroll'}),
            ('get_grading_config', {}),
            ('get_students_features', {}),
            ('get_distribution', {}),
            ('get_student_progress_url', {'unique_student_identifier': self.user.username}),
            ('reset_student_attempts',
             {'problem_to_reset': self.problem_urlname, 'unique_student_identifier': self.user.email}),
            ('update_forum_role_membership',
             {'unique_student_identifier': self.user.email, 'rolename': 'Moderator', 'action': 'allow'}),
            ('list_forum_members', {'rolename': FORUM_ROLE_COMMUNITY_TA}),
            ('proxy_legacy_analytics', {'aname': 'ProblemGradeDistribution'}),
            ('send_email', {'send_to': 'staff', 'subject': 'test', 'message': 'asdf'}),
            ('list_instructor_tasks', {}),
            ('list_background_email_tasks', {}),
            ('list_report_downloads', {}),
            ('calculate_grades_csv', {}),
            ('get_students_features', {}),
        ]
        # Endpoints that only Instructors can access
        self.instructor_level_endpoints = [
            ('bulk_beta_modify_access', {'identifiers': 'foo@example.org', 'action': 'add'}),
            ('modify_access', {'unique_student_identifier': self.user.email, 'rolename': 'beta', 'action': 'allow'}),
            ('list_course_role_members', {'rolename': 'beta'}),
            ('rescore_problem',
             {'problem_to_reset': self.problem_urlname, 'unique_student_identifier': self.user.email}),
        ]

    def _access_endpoint(self, endpoint, args, status_code, msg):
        """
        Asserts that accessing the given `endpoint` gets a response of `status_code`.

        endpoint: string, endpoint for instructor dash API
        args: dict, kwargs for `reverse` call
        status_code: expected HTTP status code response
        msg: message to display if assertion fails.
        """
        url = reverse(endpoint, kwargs={'course_id': self.course.id.to_deprecated_string()})
        if endpoint in ['send_email', 'students_update_enrollment', 'bulk_beta_modify_access']:
            response = self.client.post(url, args)
        else:
            response = self.client.get(url, args)
        self.assertEqual(
            response.status_code,
            status_code,
            msg=msg
        )

    def test_student_level(self):
        """
        Ensure that an enrolled student can't access staff or instructor endpoints.
        """
        self.client.login(username=self.user.username, password='test')

        for endpoint, args in self.staff_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Student should not be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Student should not be allowed to access endpoint " + endpoint
            )

    def test_staff_level(self):
        """
        Ensure that a staff member can't access instructor endpoints.
        """
        staff_member = StaffFactory(course_key=self.course.id)
        CourseEnrollment.enroll(staff_member, self.course.id)
        self.client.login(username=staff_member.username, password='test')
        # Try to promote to forums admin - not working
        # update_forum_role(self.course.id, staff_member, FORUM_ROLE_ADMINISTRATOR, 'allow')

        for endpoint, args in self.staff_level_endpoints:
            # TODO: make these work
            if endpoint in ['update_forum_role_membership', 'proxy_legacy_analytics', 'list_forum_members']:
                continue
            self._access_endpoint(
                endpoint,
                args,
                200,
                "Staff member should be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Staff member should not be allowed to access endpoint " + endpoint
            )

    def test_instructor_level(self):
        """
        Ensure that an instructor member can access all endpoints.
        """
        inst = InstructorFactory(course_key=self.course.id)
        CourseEnrollment.enroll(inst, self.course.id)
        self.client.login(username=inst.username, password='test')

        for endpoint, args in self.staff_level_endpoints:
            # TODO: make these work
            if endpoint in ['update_forum_role_membership', 'proxy_legacy_analytics']:
                continue
            self._access_endpoint(
                endpoint,
                args,
                200,
                "Instructor should be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            # TODO: make this work
            if endpoint in ['rescore_problem']:
                continue
            self._access_endpoint(
                endpoint,
                args,
                200,
                "Instructor should be allowed to access endpoint " + endpoint
            )


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': True})
class TestInstructorAPIBulkAccountCreationAndEnrollment(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test Bulk account creation and enrollment from csv file
    """
    def setUp(self):
        self.request = RequestFactory().request()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')
        self.url = reverse('register_and_enroll_students', kwargs={'course_id': self.course.id.to_deprecated_string()})

        self.not_enrolled_student = UserFactory(
            username='NotEnrolledStudent',
            email='nonenrolled@test.com',
            first_name='NotEnrolled',
            last_name='Student'
        )

    @patch('instructor.views.api.log.info')
    def test_account_creation_and_enrollment_with_csv(self, info_log):
        """
        Happy path test to create a single new user
        """
        csv_content = "test_student@example.com,test_student_1,tester1,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['row_errors']), 0)
        self.assertEquals(len(data['warnings']), 0)
        self.assertEquals(len(data['general_errors']), 0)

        # test the log for email that's send to new created user.
        info_log.assert_called_with('email sent to new created user at test_student@example.com')

    @patch('instructor.views.api.log.info')
    def test_account_creation_and_enrollment_with_csv_with_blank_lines(self, info_log):
        """
        Happy path test to create a single new user
        """
        csv_content = "\ntest_student@example.com,test_student_1,tester1,USA\n\n"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['row_errors']), 0)
        self.assertEquals(len(data['warnings']), 0)
        self.assertEquals(len(data['general_errors']), 0)

        # test the log for email that's send to new created user.
        info_log.assert_called_with('email sent to new created user at test_student@example.com')

    @patch('instructor.views.api.log.info')
    def test_email_and_username_already_exist(self, info_log):
        """
        If the email address and username already exists
        and the user is enrolled in the course, do nothing (including no email gets sent out)
        """
        csv_content = "test_student@example.com,test_student_1,tester1,USA\n" \
                      "test_student@example.com,test_student_1,tester2,US"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['row_errors']), 0)
        self.assertEquals(len(data['warnings']), 0)
        self.assertEquals(len(data['general_errors']), 0)

        # test the log for email that's send to new created user.
        info_log.assert_called_with("user already exists with username '{username}' and email '{email}'".format(username='test_student_1', email='test_student@example.com'))

    def test_bad_file_upload_type(self):
        """
        Try uploading some non-CSV file and verify that it is rejected
        """
        uploaded_file = SimpleUploadedFile("temp.jpg", io.BytesIO(b"some initial binary data: \x00\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotEquals(len(data['general_errors']), 0)
        self.assertEquals(data['general_errors'][0]['response'], 'Could not read uploaded file.')

    def test_insufficient_data(self):
        """
        Try uploading a CSV file which does not have the exact four columns of data
        """
        csv_content = "test_student@example.com,test_student_1\n"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['row_errors']), 0)
        self.assertEquals(len(data['warnings']), 0)
        self.assertEquals(len(data['general_errors']), 1)
        self.assertEquals(data['general_errors'][0]['response'], 'Data in row #1 must have exactly four columns: email, username, full name, and country')

    def test_invalid_email_in_csv(self):
        """
        Test failure case of a poorly formatted email field
        """
        csv_content = "test_student.example.com,test_student_1,tester1,USA"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertNotEquals(len(data['row_errors']), 0)
        self.assertEquals(len(data['warnings']), 0)
        self.assertEquals(len(data['general_errors']), 0)
        self.assertEquals(data['row_errors'][0]['response'], 'Invalid email {0}.'.format('test_student.example.com'))

    @patch('instructor.views.api.log.info')
    def test_csv_user_exist_and_not_enrolled(self, info_log):
        """
        If the email address and username already exists
        and the user is not enrolled in the course, enrolled him/her and iterate to next one.
        """
        csv_content = "nonenrolled@test.com,NotEnrolledStudent,tester1,USA"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        info_log.assert_called_with('user {username} enrolled in the course {course}'.format(username='NotEnrolledStudent', course=self.course.id))

    def test_user_with_already_existing_email_in_csv(self):
        """
        If the email address already exists, but the username is different,
        assume it is the correct user and just register the user in the course.
        """
        csv_content = "test_student@example.com,test_student_1,tester1,USA\n" \
                      "test_student@example.com,test_student_2,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        warning_message = 'An account with email {email} exists but the provided username {username} ' \
                          'is different. Enrolling anyway with {email}.'.format(email='test_student@example.com', username='test_student_2')
        self.assertNotEquals(len(data['warnings']), 0)
        self.assertEquals(data['warnings'][0]['response'], warning_message)
        user = User.objects.get(email='test_student@example.com')
        self.assertTrue(CourseEnrollment.is_enrolled(user, self.course.id))

    def test_user_with_already_existing_username_in_csv(self):
        """
        If the username already exists (but not the email),
        assume it is a different user and fail to create the new account.
        """
        csv_content = "test_student1@example.com,test_student_1,tester1,USA\n" \
                      "test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)

        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotEquals(len(data['row_errors']), 0)
        self.assertEquals(data['row_errors'][0]['response'], 'Username {user} already exists.'.format(user='test_student_1'))

    def test_csv_file_not_attached(self):
        """
        Test when the user does not attach a file
        """
        csv_content = "test_student1@example.com,test_student_1,tester1,USA\n" \
                      "test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)

        response = self.client.post(self.url, {'file_not_found': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotEquals(len(data['general_errors']), 0)
        self.assertEquals(data['general_errors'][0]['response'], 'File is not attached.')

    def test_raising_exception_in_auto_registration_and_enrollment_case(self):
        """
        Test that exceptions are handled well
        """
        csv_content = "test_student1@example.com,test_student_1,tester1,USA\n" \
                      "test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        with patch('instructor.views.api.create_and_enroll_user') as mock:
            mock.side_effect = NonExistentCourseError()
            response = self.client.post(self.url, {'students_list': uploaded_file})

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotEquals(len(data['row_errors']), 0)
        self.assertEquals(data['row_errors'][0]['response'], 'NonExistentCourseError')

    def test_generate_unique_password(self):
        """
        generate_unique_password should generate a unique password string that excludes certain characters.
        """
        password = generate_unique_password([], 12)
        self.assertEquals(len(password), 12)
        for letter in password:
            self.assertNotIn(letter, 'aAeEiIoOuU1l')

    def test_users_created_and_enrolled_successfully_if_others_fail(self):

        csv_content = "test_student1@example.com,test_student_1,tester1,USA\n" \
                      "test_student3@example.com,test_student_1,tester3,CA\n" \
                      "test_student2@example.com,test_student_2,tester2,USA"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotEquals(len(data['row_errors']), 0)
        self.assertEquals(data['row_errors'][0]['response'], 'Username {user} already exists.'.format(user='test_student_1'))
        self.assertTrue(User.objects.filter(username='test_student_1', email='test_student1@example.com').exists())
        self.assertTrue(User.objects.filter(username='test_student_2', email='test_student2@example.com').exists())
        self.assertFalse(User.objects.filter(email='test_student3@example.com').exists())

    @patch.object(instructor.views.api, 'generate_random_string',
                  Mock(side_effect=['first', 'first', 'second']))
    def test_generate_unique_password_no_reuse(self):
        """
        generate_unique_password should generate a unique password string that hasn't been generated before.
        """
        generated_password = ['first']
        password = generate_unique_password(generated_password, 12)
        self.assertNotEquals(password, 'first')

    @patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': False})
    def test_allow_automated_signups_flag_not_set(self):
        csv_content = "test_student1@example.com,test_student_1,tester1,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        self.assertEquals(response.status_code, 403)


@ddt.ddt
@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPIEnrollment(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test enrollment modification endpoint.

    This test does NOT exhaustively test state changes, that is the
    job of test_enrollment. This tests the response and action switch.
    """

    def setUp(self):
        self.request = RequestFactory().request()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        self.enrolled_student = UserFactory(username='EnrolledStudent', first_name='Enrolled', last_name='Student')
        CourseEnrollment.enroll(
            self.enrolled_student,
            self.course.id
        )
        self.notenrolled_student = UserFactory(username='NotEnrolledStudent', first_name='NotEnrolled',
                                               last_name='Student')

        # Create invited, but not registered, user
        cea = CourseEnrollmentAllowed(email='robot-allowed@robot.org', course_id=self.course.id)
        cea.save()
        self.allowed_email = 'robot-allowed@robot.org'

        self.notregistered_email = 'robot-not-an-email-yet@robot.org'
        self.assertEqual(User.objects.filter(email=self.notregistered_email).count(), 0)

        # Email URL values
        self.site_name = microsite.get_value(
            'SITE_NAME',
            settings.SITE_NAME
        )
        self.about_path = '/courses/{}/about'.format(self.course.id)
        self.course_path = '/courses/{}/'.format(self.course.id)

        # uncomment to enable enable printing of large diffs
        # from failed assertions in the event of a test failure.
        # (comment because pylint C0103)
        # self.maxDiff = None

    def tearDown(self):
        """
        Undo all patches.
        """
        patch.stopall()

    def test_missing_params(self):
        """ Test missing all query parameters. """
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_bad_action(self):
        """ Test with an invalid action. """
        action = 'robot-not-an-action'
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': action})
        self.assertEqual(response.status_code, 400)

    def test_invalid_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': 'percivaloctavius@', 'action': 'enroll', 'email_students': False})
        self.assertEqual(response.status_code, 200)

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": 'percivaloctavius@',
                    "invalidIdentifier": True,
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_invalid_username(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': 'percivaloctavius', 'action': 'enroll', 'email_students': False})
        self.assertEqual(response.status_code, 200)

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": 'percivaloctavius',
                    "invalidIdentifier": True,
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_enroll_with_username(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'enroll', 'email_students': False})
        self.assertEqual(response.status_code, 200)

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": self.notenrolled_student.username,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_enroll_without_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'enroll', 'email_students': False})
        print "type(self.notenrolled_student.email): {}".format(type(self.notenrolled_student.email))
        self.assertEqual(response.status_code, 200)

        # test that the user is now enrolled
        user = User.objects.get(email=self.notenrolled_student.email)
        self.assertTrue(CourseEnrollment.is_enrolled(user, self.course.id))

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 0)

    @ddt.data('http', 'https')
    def test_enroll_with_email(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)

        print "type(self.notenrolled_student.email): {}".format(type(self.notenrolled_student.email))
        self.assertEqual(response.status_code, 200)

        # test that the user is now enrolled
        user = User.objects.get(email=self.notenrolled_student.email)
        self.assertTrue(CourseEnrollment.is_enrolled(user, self.course.id))

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            u'You have been enrolled in {}'.format(self.course.display_name)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear NotEnrolled Student\n\nYou have been enrolled in {} "
            "at edx.org by a member of the course staff. "
            "The course should now appear on your edx.org dashboard.\n\n"
            "To start accessing course materials, please visit "
            "{proto}://{site}{course_path}\n\n----\n"
            "This email was automatically sent from edx.org to NotEnrolled Student".format(
                self.course.display_name,
                proto=protocol, site=self.site_name, course_path=self.course_path
            )
        )

    @ddt.data('http', 'https')
    def test_enroll_with_email_not_registered(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        self.assertEqual(response.status_code, 200)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            u'You have been invited to register for {}'.format(self.course.display_name)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {} at edx.org by a member of the course staff.\n\n"
            "To finish your registration, please visit {proto}://{site}/register and fill out the "
            "registration form making sure to use robot-not-an-email-yet@robot.org in the E-mail field.\n"
            "Once you have registered and activated your account, "
            "visit {proto}://{site}{about_path} to join the course.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                self.course.display_name, proto=protocol, site=self.site_name, about_path=self.about_path
            )
        )

    @ddt.data('http', 'https')
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_enroll_email_not_registered_mktgsite(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {display_name} at edx.org by a member of the course staff.\n\n"
            "To finish your registration, please visit {proto}://{site}/register and fill out the registration form "
            "making sure to use robot-not-an-email-yet@robot.org in the E-mail field.\n"
            "You can then enroll in {display_name}.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                display_name=self.course.display_name, proto=protocol, site=self.site_name
            )
        )

    @ddt.data('http', 'https')
    def test_enroll_with_email_not_registered_autoenroll(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        print "type(self.notregistered_email): {}".format(type(self.notregistered_email))
        self.assertEqual(response.status_code, 200)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            u'You have been invited to register for {}'.format(self.course.display_name)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {display_name} at edx.org by a member of the course staff.\n\n"
            "To finish your registration, please visit {proto}://{site}/register and fill out the registration form "
            "making sure to use robot-not-an-email-yet@robot.org in the E-mail field.\n"
            "Once you have registered and activated your account, you will see {display_name} listed on your dashboard.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                proto=protocol, site=self.site_name, display_name=self.course.display_name
            )
        )

    def test_unenroll_without_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': 'unenroll', 'email_students': False})
        print "type(self.enrolled_student.email): {}".format(type(self.enrolled_student.email))
        self.assertEqual(response.status_code, 200)

        # test that the user is now unenrolled
        user = User.objects.get(email=self.enrolled_student.email)
        self.assertFalse(CourseEnrollment.is_enrolled(user, self.course.id))

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.enrolled_student.email,
                    "before": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 0)

    def test_unenroll_with_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': 'unenroll', 'email_students': True})
        print "type(self.enrolled_student.email): {}".format(type(self.enrolled_student.email))
        self.assertEqual(response.status_code, 200)

        # test that the user is now unenrolled
        user = User.objects.get(email=self.enrolled_student.email)
        self.assertFalse(CourseEnrollment.is_enrolled(user, self.course.id))

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.enrolled_student.email,
                    "before": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been un-enrolled from {display_name}'.format(display_name=self.course.display_name,)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear Enrolled Student\n\nYou have been un-enrolled in {display_name} "
            "at edx.org by a member of the course staff. "
            "The course will no longer appear on your edx.org dashboard.\n\n"
            "Your other courses have not been affected.\n\n----\n"
            "This email was automatically sent from edx.org to Enrolled Student".format(
                display_name=self.course.display_name,
            )
        )

    def test_unenroll_with_email_allowed_student(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.allowed_email, 'action': 'unenroll', 'email_students': True})
        print "type(self.allowed_email): {}".format(type(self.allowed_email))
        self.assertEqual(response.status_code, 200)

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.allowed_email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": True,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": False,
                    }
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been un-enrolled from {display_name}'.format(display_name=self.course.display_name,)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear Student,\n\nYou have been un-enrolled from course {display_name} by a member of the course staff. "
            "Please disregard the invitation previously sent.\n\n----\n"
            "This email was automatically sent from edx.org to robot-allowed@robot.org".format(
                display_name=self.course.display_name,
            )
        )

    @ddt.data('http', 'https')
    @patch('instructor.enrollment.uses_shib')
    def test_enroll_with_email_not_registered_with_shib(self, protocol, mock_uses_shib):
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        self.assertEqual(response.status_code, 200)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been invited to register for {display_name}'.format(display_name=self.course.display_name,)
        )

        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {display_name} at edx.org by a member of the course staff.\n\n"
            "To access the course visit {proto}://{site}{about_path} and register for the course.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                proto=protocol, site=self.site_name, about_path=self.about_path,
                display_name=self.course.display_name,
            )
        )

    @patch('instructor.enrollment.uses_shib')
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_enroll_email_not_registered_shib_mktgsite(self, mock_uses_shib):
        # Try with marketing site enabled and shib on
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        # Try with marketing site enabled
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
            response = self.client.post(url, {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {} at edx.org by a member of the course staff.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                self.course.display_name,
            )
        )

    @ddt.data('http', 'https')
    @patch('instructor.enrollment.uses_shib')
    def test_enroll_with_email_not_registered_with_shib_autoenroll(self, protocol, mock_uses_shib):
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        print "type(self.notregistered_email): {}".format(type(self.notregistered_email))
        self.assertEqual(response.status_code, 200)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been invited to register for {display_name}'.format(display_name=self.course.display_name,)
        )

        self.assertEqual(
            mail.outbox[0].body,
            "Dear student,\n\nYou have been invited to join {display_name} at edx.org by a member of the course staff.\n\n"
            "To access the course visit {proto}://{site}{course_path} and login.\n\n----\n"
            "This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org".format(
                display_name=self.course.display_name,
                proto=protocol, site=self.site_name, course_path=self.course_path
            )
        )


@ddt.ddt
@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPIBulkBetaEnrollment(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test bulk beta modify access endpoint.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        self.beta_tester = BetaTesterFactory(course_key=self.course.id)
        CourseEnrollment.enroll(
            self.beta_tester,
            self.course.id
        )
        self.assertTrue(CourseBetaTesterRole(self.course.id).has_user(self.beta_tester))

        self.notenrolled_student = UserFactory(username='NotEnrolledStudent')

        self.notregistered_email = 'robot-not-an-email-yet@robot.org'
        self.assertEqual(User.objects.filter(email=self.notregistered_email).count(), 0)

        self.request = RequestFactory().request()

        # Email URL values
        self.site_name = microsite.get_value(
            'SITE_NAME',
            settings.SITE_NAME
        )
        self.about_path = '/courses/{}/about'.format(self.course.id)
        self.course_path = '/courses/{}/'.format(self.course.id)

        # uncomment to enable enable printing of large diffs
        # from failed assertions in the event of a test failure.
        # (comment because pylint C0103)
        # self.maxDiff = None

    def test_missing_params(self):
        """ Test missing all query parameters. """
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_bad_action(self):
        """ Test with an invalid action. """
        action = 'robot-not-an-action'
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.beta_tester.email, 'action': action})
        self.assertEqual(response.status_code, 400)

    def add_notenrolled(self, response, identifier):
        """
        Test Helper Method (not a test, called by other tests)

        Takes a client response from a call to bulk_beta_modify_access with 'email_students': False,
        and the student identifier (email or username) given as 'identifiers' in the request.

        Asserts the reponse returns cleanly, that the student was added as a beta tester, and the
        response properly contains their identifier, 'error': False, and 'userDoesNotExist': False.
        Additionally asserts no email was sent.
        """
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student))
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": identifier,
                    "error": False,
                    "userDoesNotExist": False
                }
            ]
        }

        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 0)

    def test_add_notenrolled_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': False})
        self.add_notenrolled(response, self.notenrolled_student.email)
        self.assertFalse(CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id))

    def test_add_notenrolled_email_autoenroll(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': False, 'auto_enroll': True})
        self.add_notenrolled(response, self.notenrolled_student.email)
        self.assertTrue(CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id))

    def test_add_notenrolled_username(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'add', 'email_students': False})
        self.add_notenrolled(response, self.notenrolled_student.username)
        self.assertFalse(CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id))

    def test_add_notenrolled_username_autoenroll(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'add', 'email_students': False, 'auto_enroll': True})
        self.add_notenrolled(response, self.notenrolled_student.username)
        self.assertTrue(CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id))

    @ddt.data('http', 'https')
    def test_add_notenrolled_with_email(self, protocol):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        self.assertEqual(response.status_code, 200)

        self.assertTrue(CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student))
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "error": False,
                    "userDoesNotExist": False
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been invited to a beta test for {display_name}'.format(display_name=self.course.display_name,)
        )

        self.assertEqual(
            mail.outbox[0].body,
            u"Dear {student_name}\n\nYou have been invited to be a beta tester "
            "for {display_name} at edx.org by a member of the course staff.\n\n"
            "Visit {proto}://{site}{about_path} to join "
            "the course and begin the beta test.\n\n----\n"
            "This email was automatically sent from edx.org to {student_email}".format(
                display_name=self.course.display_name,
                student_name=self.notenrolled_student.profile.name,
                student_email=self.notenrolled_student.email,
                proto=protocol,
                site=self.site_name,
                about_path=self.about_path
            )
        )

    @ddt.data('http', 'https')
    def test_add_notenrolled_with_email_autoenroll(self, protocol):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        self.assertEqual(response.status_code, 200)

        self.assertTrue(CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student))
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "error": False,
                    "userDoesNotExist": False
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            'You have been invited to a beta test for {display_name}'.format(display_name=self.course.display_name)
        )

        self.assertEqual(
            mail.outbox[0].body,
            u"Dear {student_name}\n\nYou have been invited to be a beta tester "
            "for {display_name} at edx.org by a member of the course staff.\n\n"
            "To start accessing course materials, please visit "
            "{proto}://{site}{course_path}\n\n----\n"
            "This email was automatically sent from edx.org to {student_email}".format(
                display_name=self.course.display_name,
                student_name=self.notenrolled_student.profile.name,
                student_email=self.notenrolled_student.email,
                proto=protocol,
                site=self.site_name,
                course_path=self.course_path
            )
        )

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_add_notenrolled_email_mktgsite(self):
        # Try with marketing site enabled
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mail.outbox[0].body,
            u"Dear {}\n\nYou have been invited to be a beta tester "
            "for {} at edx.org by a member of the course staff.\n\n"
            "Visit edx.org to enroll in the course and begin the beta test.\n\n----\n"
            "This email was automatically sent from edx.org to {}".format(
                self.notenrolled_student.profile.name,
                self.course.display_name,
                self.notenrolled_student.email,
            )
        )

    def test_enroll_with_email_not_registered(self):
        # User doesn't exist
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.notregistered_email, 'action': 'add', 'email_students': True})
        self.assertEqual(response.status_code, 200)
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notregistered_email,
                    "error": True,
                    "userDoesNotExist": True
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 0)

    def test_remove_without_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.beta_tester.email, 'action': 'remove', 'email_students': False})
        self.assertEqual(response.status_code, 200)

        # Works around a caching bug which supposedly can't happen in prod. The instance here is not ==
        # the instance fetched from the email above which had its cache cleared
        if hasattr(self.beta_tester, '_roles'):
            del self.beta_tester._roles
        self.assertFalse(CourseBetaTesterRole(self.course.id).has_user(self.beta_tester))

        # test the response data
        expected = {
            "action": "remove",
            "results": [
                {
                    "identifier": self.beta_tester.email,
                    "error": False,
                    "userDoesNotExist": False
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

        # Check the outbox
        self.assertEqual(len(mail.outbox), 0)

    def test_remove_with_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {'identifiers': self.beta_tester.email, 'action': 'remove', 'email_students': True})
        self.assertEqual(response.status_code, 200)

        # Works around a caching bug which supposedly can't happen in prod. The instance here is not ==
        # the instance fetched from the email above which had its cache cleared
        if hasattr(self.beta_tester, '_roles'):
            del self.beta_tester._roles
        self.assertFalse(CourseBetaTesterRole(self.course.id).has_user(self.beta_tester))

        # test the response data
        expected = {
            "action": "remove",
            "results": [
                {
                    "identifier": self.beta_tester.email,
                    "error": False,
                    "userDoesNotExist": False
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)
        # Check the outbox
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            u'You have been removed from a beta test for {display_name}'.format(display_name=self.course.display_name,)
        )
        self.assertEqual(
            mail.outbox[0].body,
            "Dear {full_name}\n\nYou have been removed as a beta tester for "
            "{display_name} at edx.org by a member of the course staff. "
            "The course will remain on your dashboard, but you will no longer "
            "be part of the beta testing group.\n\n"
            "Your other courses have not been affected.\n\n----\n"
            "This email was automatically sent from edx.org to {email_address}".format(
                display_name=self.course.display_name,
                full_name=self.beta_tester.profile.name,
                email_address=self.beta_tester.email
            )
        )


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPILevelsAccess(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints whereby instructors can change permissions
    of other users.

    This test does NOT test whether the actions had an effect on the
    database, that is the job of test_access.
    This tests the response and action switch.
    Actually, modify_access does not have a very meaningful
    response yet, so only the status code is tested.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        self.other_instructor = InstructorFactory(course_key=self.course.id)
        self.other_staff = StaffFactory(course_key=self.course.id)
        self.other_user = UserFactory()

    def test_modify_access_noparams(self):
        """ Test missing all query parameters. """
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_modify_access_bad_action(self):
        """ Test with an invalid action parameter. """
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'staff',
            'action': 'robot-not-an-action',
        })
        self.assertEqual(response.status_code, 400)

    def test_modify_access_bad_role(self):
        """ Test with an invalid action parameter. """
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'robot-not-a-roll',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 400)

    def test_modify_access_allow(self):
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_user.email,
            'rolename': 'staff',
            'action': 'allow',
        })
        self.assertEqual(response.status_code, 200)

    def test_modify_access_allow_with_uname(self):
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_instructor.username,
            'rolename': 'staff',
            'action': 'allow',
        })
        self.assertEqual(response.status_code, 200)

    def test_modify_access_revoke(self):
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'staff',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 200)

    def test_modify_access_revoke_with_username(self):
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_staff.username,
            'rolename': 'staff',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 200)

    def test_modify_access_with_fake_user(self):
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': 'GandalfTheGrey',
            'rolename': 'staff',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 200)
        expected = {
            'unique_student_identifier': 'GandalfTheGrey',
            'userDoesNotExist': True,
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_modify_access_with_inactive_user(self):
        self.other_user.is_active = False
        self.other_user.save()  # pylint: disable=no-member
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_user.username,
            'rolename': 'beta',
            'action': 'allow',
        })
        self.assertEqual(response.status_code, 200)
        expected = {
            'unique_student_identifier': self.other_user.username,
            'inactiveUser': True,
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_modify_access_revoke_not_allowed(self):
        """ Test revoking access that a user does not have. """
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'instructor',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 200)

    def test_modify_access_revoke_self(self):
        """
        Test that an instructor cannot remove instructor privelages from themself.
        """
        url = reverse('modify_access', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'unique_student_identifier': self.instructor.email,
            'rolename': 'instructor',
            'action': 'revoke',
        })
        self.assertEqual(response.status_code, 200)
        # check response content
        expected = {
            'unique_student_identifier': self.instructor.username,
            'rolename': 'instructor',
            'action': 'revoke',
            'removingSelfAsInstructor': True,
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_list_course_role_members_noparams(self):
        """ Test missing all query parameters. """
        url = reverse('list_course_role_members', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_list_course_role_members_bad_rolename(self):
        """ Test with an invalid rolename parameter. """
        url = reverse('list_course_role_members', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'rolename': 'robot-not-a-rolename',
        })
        self.assertEqual(response.status_code, 400)

    def test_list_course_role_members_staff(self):
        url = reverse('list_course_role_members', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'rolename': 'staff',
        })
        self.assertEqual(response.status_code, 200)

        # check response content
        expected = {
            'course_id': self.course.id.to_deprecated_string(),
            'staff': [
                {
                    'username': self.other_staff.username,
                    'email': self.other_staff.email,
                    'first_name': self.other_staff.first_name,
                    'last_name': self.other_staff.last_name,
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_list_course_role_members_beta(self):
        url = reverse('list_course_role_members', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'rolename': 'beta',
        })
        self.assertEqual(response.status_code, 200)

        # check response content
        expected = {
            'course_id': self.course.id.to_deprecated_string(),
            'beta': []
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected)

    def test_update_forum_role_membership(self):
        """
        Test update forum role membership with user's email and username.
        """

        # Seed forum roles for course.
        seed_permissions_roles(self.course.id)

        for user in [self.instructor, self.other_user]:
            for identifier_attr in [user.email, user.username]:
                for rolename in ["Administrator", "Moderator", "Community TA"]:
                    for action in ["allow", "revoke"]:
                        self.assert_update_forum_role_membership(user, identifier_attr, rolename, action)

    def assert_update_forum_role_membership(self, current_user, identifier, rolename, action):
        """
        Test update forum role membership.
        Get unique_student_identifier, rolename and action and update forum role.
        """
        url = reverse('update_forum_role_membership', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(
            url,
            {
                'unique_student_identifier': identifier,
                'rolename': rolename,
                'action': action,
            }
        )

        # Status code should be 200.
        self.assertEqual(response.status_code, 200)

        user_roles = current_user.roles.filter(course_id=self.course.id).values_list("name", flat=True)
        if action == 'allow':
            self.assertIn(rolename, user_roles)
        elif action == 'revoke':
            self.assertNotIn(rolename, user_roles)


@ddt.ddt
@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPILevelsDataDump(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints that show data without side effects.
    """

    def setUp(self):
        super(TestInstructorAPILevelsDataDump, self).setUp()
        self.course = CourseFactory.create()
        self.course_mode = CourseMode(course_id=self.course.id,
                                      mode_slug="honor",
                                      mode_display_name="honor cert",
                                      min_price=40)
        self.course_mode.save()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')
        self.cart = Order.get_cart_for_user(self.instructor)
        self.coupon_code = 'abcde'
        self.coupon = Coupon(code=self.coupon_code, description='testing code', course_id=self.course.id,
                             percentage_discount=10, created_by=self.instructor, is_active=True)
        self.coupon.save()

        #create testing invoice 1
        self.sale_invoice_1 = Invoice.objects.create(
            total_amount=1234.32, company_name='Test1', company_contact_name='TestName', company_contact_email='Test@company.com',
            recipient_name='Testw', recipient_email='test1@test.com', customer_reference_number='2Fwe23S',
            internal_reference="A", course_id=self.course.id, is_valid=True
        )

        self.students = [UserFactory() for _ in xrange(6)]
        for student in self.students:
            CourseEnrollment.enroll(student, self.course.id)

    def test_invalidate_sale_record(self):
        """
        Testing the sale invalidating scenario.
        """
        for i in range(2):
            course_registration_code = CourseRegistrationCode(
                code='sale_invoice{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=self.sale_invoice_1
            )
            course_registration_code.save()

        data = {'invoice_number': self.sale_invoice_1.id, 'event_type': "invalidate"}
        url = reverse('sale_validation', kwargs={'course_id': self.course.id.to_deprecated_string()})
        self.assert_request_status_code(200, url, method="POST", data=data)

        #Now try to fetch data against not existing invoice number
        test_data_1 = {'invoice_number': 100, 'event_type': "invalidate"}
        self.assert_request_status_code(404, url, method="POST", data=test_data_1)

        # Now invalidate the same invoice number and expect an Bad request
        response = self.assert_request_status_code(400, url, method="POST", data=data)
        self.assertIn("The sale associated with this invoice has already been invalidated.", response.content)

        # now re_validate the invoice number
        data['event_type'] = "re_validate"
        self.assert_request_status_code(200, url, method="POST", data=data)

        # Now re_validate the same actove invoice number and expect an Bad request
        response = self.assert_request_status_code(400, url, method="POST", data=data)
        self.assertIn("This invoice is already active.", response.content)

        test_data_2 = {'invoice_number': self.sale_invoice_1.id}
        response = self.assert_request_status_code(400, url, method="POST", data=test_data_2)
        self.assertIn("Missing required event_type parameter", response.content)

        test_data_3 = {'event_type': "re_validate"}
        response = self.assert_request_status_code(400, url, method="POST", data=test_data_3)
        self.assertIn("Missing required invoice_number parameter", response.content)

        # submitting invalid invoice number
        data['invoice_number'] = 'testing'
        response = self.assert_request_status_code(400, url, method="POST", data=data)
        self.assertIn("invoice_number must be an integer, {value} provided".format(value=data['invoice_number']), response.content)

    def test_get_ecommerce_purchase_features_csv(self):
        """
        Test that the response from get_purchase_transaction is in csv format.
        """
        PaidCourseRegistration.add_to_order(self.cart, self.course.id)
        self.cart.purchase(first='FirstNameTesting123', street1='StreetTesting123')
        url = reverse('get_purchase_transaction', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url + '/csv', {})
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_get_sale_order_records_features_csv(self):
        """
        Test that the response from get_sale_order_records is in csv format.
        """
        self.cart.order_type = 'business'
        self.cart.save()
        self.cart.add_billing_details(company_name='Test Company', company_contact_name='Test',
                                      company_contact_email='test@123', recipient_name='R1',
                                      recipient_email='', customer_reference_number='PO#23')
        PaidCourseRegistration.add_to_order(self.cart, self.course.id)
        self.cart.purchase()
        sale_order_url = reverse('get_sale_order_records', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(sale_order_url)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_get_sale_records_features_csv(self):
        """
        Test that the response from get_sale_records is in csv format.
        """
        for i in range(2):
            course_registration_code = CourseRegistrationCode(
                code='sale_invoice{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=self.sale_invoice_1
            )
            course_registration_code.save()

        url = reverse('get_sale_records', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url + '/csv', {})
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_get_sale_records_features_json(self):
        """
        Test that the response from get_sale_records is in json format.
        """
        for i in range(5):
            course_registration_code = CourseRegistrationCode(
                code='sale_invoice{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=self.sale_invoice_1
            )
            course_registration_code.save()

        url = reverse('get_sale_records', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('sale', res_json)

        for res in res_json['sale']:
            self.validate_sale_records_response(res, course_registration_code, self.sale_invoice_1, 0)

    def test_get_sale_records_features_with_used_code(self):
        """
        Test that the response from get_sale_records is in json format and using one of the registration codes.
        """
        for i in range(5):
            course_registration_code = CourseRegistrationCode(
                code='qwerty{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=self.sale_invoice_1
            )
            course_registration_code.save()

        PaidCourseRegistration.add_to_order(self.cart, self.course.id)

        # now using registration code
        self.client.post(reverse('shoppingcart.views.use_code'), {'code': 'qwerty0'})

        url = reverse('get_sale_records', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('sale', res_json)

        for res in res_json['sale']:
            self.validate_sale_records_response(res, course_registration_code, self.sale_invoice_1, 1)

    def test_get_sale_records_features_with_multiple_invoices(self):
        """
        Test that the response from get_sale_records is in json format for multiple invoices
        """
        for i in range(5):
            course_registration_code = CourseRegistrationCode(
                code='qwerty{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=self.sale_invoice_1
            )
            course_registration_code.save()

        #create test invoice 2
        sale_invoice_2 = Invoice.objects.create(
            total_amount=1234.32, company_name='Test1', company_contact_name='TestName', company_contact_email='Test@company.com',
            recipient_name='Testw_2', recipient_email='test2@test.com', customer_reference_number='2Fwe23S',
            internal_reference="B", course_id=self.course.id
        )

        for i in range(5):
            course_registration_code = CourseRegistrationCode(
                code='xyzmn{}'.format(i), course_id=self.course.id.to_deprecated_string(),
                created_by=self.instructor, invoice=sale_invoice_2
            )
            course_registration_code.save()

        url = reverse('get_sale_records', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('sale', res_json)

        self.validate_sale_records_response(res_json['sale'][0], course_registration_code, self.sale_invoice_1, 0)
        self.validate_sale_records_response(res_json['sale'][1], course_registration_code, sale_invoice_2, 0)

    def validate_sale_records_response(self, res, course_registration_code, invoice, used_codes):
        """
        validate sale records attribute values with the response object
        """
        self.assertEqual(res['total_amount'], invoice.total_amount)
        self.assertEqual(res['recipient_email'], invoice.recipient_email)
        self.assertEqual(res['recipient_name'], invoice.recipient_name)
        self.assertEqual(res['company_name'], invoice.company_name)
        self.assertEqual(res['company_contact_name'], invoice.company_contact_name)
        self.assertEqual(res['company_contact_email'], invoice.company_contact_email)
        self.assertEqual(res['internal_reference'], invoice.internal_reference)
        self.assertEqual(res['customer_reference_number'], invoice.customer_reference_number)
        self.assertEqual(res['invoice_number'], invoice.id)
        self.assertEqual(res['created_by'], course_registration_code.created_by.username)
        self.assertEqual(res['course_id'], invoice.course_id.to_deprecated_string())
        self.assertEqual(res['total_used_codes'], used_codes)
        self.assertEqual(res['total_codes'], 5)

    def test_get_ecommerce_purchase_features_with_coupon_info(self):
        """
        Test that some minimum of information is formatted
        correctly in the response to get_purchase_transaction.
        """
        PaidCourseRegistration.add_to_order(self.cart, self.course.id)
        url = reverse('get_purchase_transaction', kwargs={'course_id': self.course.id.to_deprecated_string()})

        # using coupon code
        resp = self.client.post(reverse('shoppingcart.views.use_code'), {'code': self.coupon_code})
        self.assertEqual(resp.status_code, 200)
        self.cart.purchase(first='FirstNameTesting123', street1='StreetTesting123')
        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('students', res_json)

        for res in res_json['students']:
            self.validate_purchased_transaction_response(res, self.cart, self.instructor, self.coupon_code)

    def test_get_ecommerce_purchases_features_without_coupon_info(self):
        """
        Test that some minimum of information is formatted
        correctly in the response to get_purchase_transaction.
        """
        url = reverse('get_purchase_transaction', kwargs={'course_id': self.course.id.to_deprecated_string()})

        carts, instructors = ([] for i in range(2))

        # purchasing the course by different users
        for _ in xrange(3):
            test_instructor = InstructorFactory(course_key=self.course.id)
            self.client.login(username=test_instructor.username, password='test')
            cart = Order.get_cart_for_user(test_instructor)
            carts.append(cart)
            instructors.append(test_instructor)
            PaidCourseRegistration.add_to_order(cart, self.course.id)
            cart.purchase(first='FirstNameTesting123', street1='StreetTesting123')

        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('students', res_json)
        for res, i in zip(res_json['students'], xrange(3)):
            self.validate_purchased_transaction_response(res, carts[i], instructors[i], 'None')

    def validate_purchased_transaction_response(self, res, cart, user, code):
        """
        validate purchased transactions attribute values with the response object
        """
        item = cart.orderitem_set.all().select_subclasses()[0]

        self.assertEqual(res['coupon_code'], code)
        self.assertEqual(res['username'], user.username)
        self.assertEqual(res['email'], user.email)
        self.assertEqual(res['list_price'], item.list_price)
        self.assertEqual(res['unit_cost'], item.unit_cost)
        self.assertEqual(res['order_id'], cart.id)
        self.assertEqual(res['orderitem_id'], item.id)

    def test_get_students_features(self):
        """
        Test that some minimum of information is formatted
        correctly in the response to get_students_features.
        """
        url = reverse('get_students_features', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        res_json = json.loads(response.content)
        self.assertIn('students', res_json)
        for student in self.students:
            student_json = [
                x for x in res_json['students']
                if x['username'] == student.username
            ][0]
            self.assertEqual(student_json['username'], student.username)
            self.assertEqual(student_json['email'], student.email)

    @ddt.data(True, False)
    def test_get_students_features_cohorted(self, is_cohorted):
        """
        Test that get_students_features includes cohort info when the course is
        cohorted, and does not when the course is not cohorted.
        """
        url = reverse('get_students_features', kwargs={'course_id': unicode(self.course.id)})
        self.course.cohort_config = {'cohorted': is_cohorted}
        self.store.update_item(self.course, self.instructor.id)

        response = self.client.get(url, {})
        res_json = json.loads(response.content)

        self.assertEqual('cohort' in res_json['feature_names'], is_cohorted)

    @patch.object(instructor.views.api, 'anonymous_id_for_user', Mock(return_value='42'))
    @patch.object(instructor.views.api, 'unique_id_for_user', Mock(return_value='41'))
    def test_get_anon_ids(self):
        """
        Test the CSV output for the anonymized user ids.
        """
        url = reverse('get_anon_ids', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(
            '"User ID","Anonymized User ID","Course Specific Anonymized User ID"'
            '\n"3","41","42"\n'
        ))
        self.assertTrue(body.endswith('"8","41","42"\n'))

    def test_list_report_downloads(self):
        url = reverse('list_report_downloads', kwargs={'course_id': self.course.id.to_deprecated_string()})
        with patch('instructor_task.models.LocalFSReportStore.links_for') as mock_links_for:
            mock_links_for.return_value = [
                ('mock_file_name_1', 'https://1.mock.url'),
                ('mock_file_name_2', 'https://2.mock.url'),
            ]
            response = self.client.get(url, {})

        expected_response = {
            "downloads": [
                {
                    "url": "https://1.mock.url",
                    "link": "<a href=\"https://1.mock.url\">mock_file_name_1</a>",
                    "name": "mock_file_name_1"
                },
                {
                    "url": "https://2.mock.url",
                    "link": "<a href=\"https://2.mock.url\">mock_file_name_2</a>",
                    "name": "mock_file_name_2"
                }
            ]
        }
        res_json = json.loads(response.content)
        self.assertEqual(res_json, expected_response)

    @ddt.data(*REPORTS_DATA)
    @ddt.unpack
    def test_calculate_report_csv_success(self, report_type, instructor_api_endpoint, task_api_endpoint, extra_instructor_api_kwargs):
        kwargs = {'course_id': unicode(self.course.id)}
        kwargs.update(extra_instructor_api_kwargs)
        url = reverse(instructor_api_endpoint, kwargs=kwargs)

        with patch(task_api_endpoint):
            response = self.client.get(url, {})
        success_status = "Your {report_type} report is being generated! You can view the status of the generation task in the 'Pending Instructor Tasks' section.".format(report_type=report_type)
        self.assertIn(success_status, response.content)

    @ddt.data(*REPORTS_DATA)
    @ddt.unpack
    def test_calculate_report_csv_already_running(self, report_type, instructor_api_endpoint, task_api_endpoint, extra_instructor_api_kwargs):
        kwargs = {'course_id': unicode(self.course.id)}
        kwargs.update(extra_instructor_api_kwargs)
        url = reverse(instructor_api_endpoint, kwargs=kwargs)

        with patch(task_api_endpoint) as mock:
            mock.side_effect = AlreadyRunningError()
            response = self.client.get(url, {})
        already_running_status = "{report_type} report generation task is already in progress. Check the 'Pending Instructor Tasks' table for the status of the task. When completed, the report will be available for download in the table below.".format(report_type=report_type)
        self.assertIn(already_running_status, response.content)

    def test_get_distribution_no_feature(self):
        """
        Test that get_distribution lists available features
        when supplied no feature parameter.
        """
        url = reverse('get_distribution', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content)
        self.assertEqual(type(res_json['available_features']), list)

        url = reverse('get_distribution', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url + u'?feature=')
        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content)
        self.assertEqual(type(res_json['available_features']), list)

    def test_get_distribution_unavailable_feature(self):
        """
        Test that get_distribution fails gracefully with
            an unavailable feature.
        """
        url = reverse('get_distribution', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {'feature': 'robot-not-a-real-feature'})
        self.assertEqual(response.status_code, 400)

    def test_get_distribution_gender(self):
        """
        Test that get_distribution fails gracefully with
            an unavailable feature.
        """
        url = reverse('get_distribution', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {'feature': 'gender'})
        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content)
        self.assertEqual(res_json['feature_results']['data']['m'], 6)
        self.assertEqual(res_json['feature_results']['choices_display_names']['m'], 'Male')
        self.assertEqual(res_json['feature_results']['data']['no_data'], 0)
        self.assertEqual(res_json['feature_results']['choices_display_names']['no_data'], 'No Data')

    def test_get_student_progress_url(self):
        """ Test that progress_url is in the successful response. """
        url = reverse('get_student_progress_url', kwargs={'course_id': self.course.id.to_deprecated_string()})
        url += "?unique_student_identifier={}".format(
            quote(self.students[0].email.encode("utf-8"))
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content)
        self.assertIn('progress_url', res_json)

    def test_get_student_progress_url_from_uname(self):
        """ Test that progress_url is in the successful response. """
        url = reverse('get_student_progress_url', kwargs={'course_id': self.course.id.to_deprecated_string()})
        url += "?unique_student_identifier={}".format(
            quote(self.students[0].username.encode("utf-8"))
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content)
        self.assertIn('progress_url', res_json)

    def test_get_student_progress_url_noparams(self):
        """ Test that the endpoint 404's without the required query params. """
        url = reverse('get_student_progress_url', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_get_student_progress_url_nostudent(self):
        """ Test that the endpoint 400's when requesting an unknown email. """
        url = reverse('get_student_progress_url', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPIRegradeTask(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints whereby instructors can change student grades.
    This includes resetting attempts and starting rescore tasks.

    This test does NOT test whether the actions had an effect on the
    database, that is the job of task tests and test_enrollment.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.problem_location = msk_from_problem_urlname(
            self.course.id,
            'robot-some-problem-urlname'
        )
        self.problem_urlname = self.problem_location.to_deprecated_string()

        self.module_to_reset = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )

    def test_reset_student_attempts_deletall(self):
        """ Make sure no one can delete all students state on a problem. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
            'delete_module': True,
        })
        self.assertEqual(response.status_code, 400)

    def test_reset_student_attempts_single(self):
        """ Test reset single student attempts. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
        })
        self.assertEqual(response.status_code, 200)
        # make sure problem attempts have been reset.
        changed_module = StudentModule.objects.get(pk=self.module_to_reset.pk)
        self.assertEqual(
            json.loads(changed_module.state)['attempts'],
            0
        )

    # mock out the function which should be called to execute the action.
    @patch.object(instructor_task.api, 'submit_reset_problem_attempts_for_all_students')
    def test_reset_student_attempts_all(self, act):
        """ Test reset all student attempts. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(act.called)

    def test_reset_student_attempts_missingmodule(self):
        """ Test reset for non-existant problem. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': 'robot-not-a-real-module',
            'unique_student_identifier': self.student.email,
        })
        self.assertEqual(response.status_code, 400)

    def test_reset_student_attempts_delete(self):
        """ Test delete single student state. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
            'delete_module': True,
        })
        self.assertEqual(response.status_code, 200)
        # make sure the module has been deleted
        self.assertEqual(
            StudentModule.objects.filter(
                student=self.module_to_reset.student,
                course_id=self.module_to_reset.course_id,
                # module_id=self.module_to_reset.module_id,
            ).count(),
            0
        )

    def test_reset_student_attempts_nonsense(self):
        """ Test failure with both unique_student_identifier and all_students. """
        url = reverse('reset_student_attempts', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
            'all_students': True,
        })
        self.assertEqual(response.status_code, 400)

    @patch.object(instructor_task.api, 'submit_rescore_problem_for_student')
    def test_rescore_problem_single(self, act):
        """ Test rescoring of a single student. """
        url = reverse('rescore_problem', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(act.called)

    @patch.object(instructor_task.api, 'submit_rescore_problem_for_student')
    def test_rescore_problem_single_from_uname(self, act):
        """ Test rescoring of a single student. """
        url = reverse('rescore_problem', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.username,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(act.called)

    @patch.object(instructor_task.api, 'submit_rescore_problem_for_all_students')
    def test_rescore_problem_all(self, act):
        """ Test rescoring for all students. """
        url = reverse('rescore_problem', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(act.called)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@patch.dict(settings.FEATURES, {'ENABLE_INSTRUCTOR_EMAIL': True, 'REQUIRE_COURSE_EMAIL_AUTH': False})
class TestInstructorSendEmail(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Checks that only instructors have access to email endpoints, and that
    these endpoints are only accessible with courses that actually exist,
    only with valid email messages.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')
        test_subject = u'\u1234 test subject'
        test_message = u'\u6824 test message'
        self.full_test_message = {
            'send_to': 'staff',
            'subject': test_subject,
            'message': test_message,
        }

    def test_send_email_as_logged_in_instructor(self):
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, self.full_test_message)
        self.assertEqual(response.status_code, 200)

    def test_send_email_but_not_logged_in(self):
        self.client.logout()
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, self.full_test_message)
        self.assertEqual(response.status_code, 403)

    def test_send_email_but_not_staff(self):
        self.client.logout()
        student = UserFactory()
        self.client.login(username=student.username, password='test')
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, self.full_test_message)
        self.assertEqual(response.status_code, 403)

    def test_send_email_but_course_not_exist(self):
        url = reverse('send_email', kwargs={'course_id': 'GarbageCourse/DNE/NoTerm'})
        response = self.client.post(url, self.full_test_message)
        self.assertNotEqual(response.status_code, 200)

    def test_send_email_no_sendto(self):
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {
            'subject': 'test subject',
            'message': 'test message',
        })
        self.assertEqual(response.status_code, 400)

    def test_send_email_no_subject(self):
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {
            'send_to': 'staff',
            'message': 'test message',
        })
        self.assertEqual(response.status_code, 400)

    def test_send_email_no_message(self):
        url = reverse('send_email', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.post(url, {
            'send_to': 'staff',
            'subject': 'test subject',
        })
        self.assertEqual(response.status_code, 400)


class MockCompletionInfo(object):
    """Mock for get_task_completion_info"""
    times_called = 0

    def mock_get_task_completion_info(self, *args):  # pylint: disable=unused-argument
        """Mock for get_task_completion_info"""
        self.times_called += 1
        if self.times_called % 2 == 0:
            return True, 'Task Completed'
        return False, 'Task Errored In Some Way'


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestInstructorAPITaskLists(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test instructor task list endpoint.
    """

    class FakeTask(object):
        """ Fake task object """
        FEATURES = [
            'task_type',
            'task_input',
            'task_id',
            'requester',
            'task_state',
            'created',
            'status',
            'task_message',
            'duration_sec'
        ]

        def __init__(self, completion):
            for feature in self.FEATURES:
                setattr(self, feature, 'expected')
            # created needs to be a datetime
            self.created = datetime.datetime(2013, 10, 25, 11, 42, 35)
            # set 'status' and 'task_message' attrs
            success, task_message = completion()
            if success:
                self.status = "Complete"
            else:
                self.status = "Incomplete"
            self.task_message = task_message
            # Set 'task_output' attr, which will be parsed to the 'duration_sec' attr.
            self.task_output = '{"duration_ms": 1035000}'
            self.duration_sec = 1035000 / 1000.0

        def make_invalid_output(self):
            """Munge task_output to be invalid json"""
            self.task_output = 'HI MY NAME IS INVALID JSON'
            # This should be given the value of 'unknown' if the task output
            # can't be properly parsed
            self.duration_sec = 'unknown'

        def to_dict(self):
            """ Convert fake task to dictionary representation. """
            attr_dict = {key: getattr(self, key) for key in self.FEATURES}
            attr_dict['created'] = attr_dict['created'].isoformat()
            return attr_dict

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.problem_location = msk_from_problem_urlname(
            self.course.id,
            'robot-some-problem-urlname'
        )
        self.problem_urlname = self.problem_location.to_deprecated_string()

        self.module = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )
        mock_factory = MockCompletionInfo()
        self.tasks = [self.FakeTask(mock_factory.mock_get_task_completion_info) for _ in xrange(7)]
        self.tasks[-1].make_invalid_output()

    def tearDown(self):
        """
        Undo all patches.
        """
        patch.stopall()

    @patch.object(instructor_task.api, 'get_running_instructor_tasks')
    def test_list_instructor_tasks_running(self, act):
        """ Test list of all running tasks. """
        act.return_value = self.tasks
        url = reverse('list_instructor_tasks', kwargs={'course_id': self.course.id.to_deprecated_string()})
        mock_factory = MockCompletionInfo()
        with patch('instructor.views.instructor_task_helpers.get_task_completion_info') as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            response = self.client.get(url, {})
        self.assertEqual(response.status_code, 200)

        # check response
        self.assertTrue(act.called)
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content)['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        self.assertEqual(actual_tasks, expected_tasks)

    @patch.object(instructor_task.api, 'get_instructor_task_history')
    def test_list_background_email_tasks(self, act):
        """Test list of background email tasks."""
        act.return_value = self.tasks
        url = reverse('list_background_email_tasks', kwargs={'course_id': self.course.id.to_deprecated_string()})
        mock_factory = MockCompletionInfo()
        with patch('instructor.views.instructor_task_helpers.get_task_completion_info') as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            response = self.client.get(url, {})
        self.assertEqual(response.status_code, 200)

        # check response
        self.assertTrue(act.called)
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content)['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        self.assertEqual(actual_tasks, expected_tasks)

    @patch.object(instructor_task.api, 'get_instructor_task_history')
    def test_list_instructor_tasks_problem(self, act):
        """ Test list task history for problem. """
        act.return_value = self.tasks
        url = reverse('list_instructor_tasks', kwargs={'course_id': self.course.id.to_deprecated_string()})
        mock_factory = MockCompletionInfo()
        with patch('instructor.views.instructor_task_helpers.get_task_completion_info') as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            response = self.client.get(url, {
                'problem_location_str': self.problem_urlname,
            })
        self.assertEqual(response.status_code, 200)

        # check response
        self.assertTrue(act.called)
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content)['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        self.assertEqual(actual_tasks, expected_tasks)

    @patch.object(instructor_task.api, 'get_instructor_task_history')
    def test_list_instructor_tasks_problem_student(self, act):
        """ Test list task history for problem AND student. """
        act.return_value = self.tasks
        url = reverse('list_instructor_tasks', kwargs={'course_id': self.course.id.to_deprecated_string()})
        mock_factory = MockCompletionInfo()
        with patch('instructor.views.instructor_task_helpers.get_task_completion_info') as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            response = self.client.get(url, {
                'problem_location_str': self.problem_urlname,
                'unique_student_identifier': self.student.email,
            })
        self.assertEqual(response.status_code, 200)

        # check response
        self.assertTrue(act.called)
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content)['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)

        self.assertEqual(actual_tasks, expected_tasks)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@patch.object(instructor_task.api, 'get_instructor_task_history')
class TestInstructorEmailContentList(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the instructor email content history endpoint.
    """

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')
        self.tasks = {}
        self.emails = {}
        self.emails_info = {}

    def tearDown(self):
        """
        Undo all patches.
        """
        patch.stopall()

    def setup_fake_email_info(self, num_emails, with_failures=False):
        """ Initialize the specified number of fake emails """
        for email_id in range(num_emails):
            num_sent = random.randint(1, 15401)
            if with_failures:
                failed = random.randint(1, 15401)
            else:
                failed = 0

            self.tasks[email_id] = FakeContentTask(email_id, num_sent, failed, 'expected')
            self.emails[email_id] = FakeEmail(email_id)
            self.emails_info[email_id] = FakeEmailInfo(self.emails[email_id], num_sent, failed)

    def get_matching_mock_email(self, **kwargs):
        """ Returns the matching mock emails for the given id """
        email_id = kwargs.get('id', 0)
        return self.emails[email_id]

    def get_email_content_response(self, num_emails, task_history_request, with_failures=False):
        """ Calls the list_email_content endpoint and returns the repsonse """
        self.setup_fake_email_info(num_emails, with_failures)
        task_history_request.return_value = self.tasks.values()
        url = reverse('list_email_content', kwargs={'course_id': self.course.id.to_deprecated_string()})
        with patch('instructor.views.api.CourseEmail.objects.get') as mock_email_info:
            mock_email_info.side_effect = self.get_matching_mock_email
            response = self.client.get(url, {})
        self.assertEqual(response.status_code, 200)
        return response

    def check_emails_sent(self, num_emails, task_history_request, with_failures=False):
        """ Tests sending emails with or without failures """
        response = self.get_email_content_response(num_emails, task_history_request, with_failures)
        self.assertTrue(task_history_request.called)
        expected_email_info = [email_info.to_dict() for email_info in self.emails_info.values()]
        actual_email_info = json.loads(response.content)['emails']

        self.assertEqual(len(actual_email_info), num_emails)
        for exp_email, act_email in zip(expected_email_info, actual_email_info):
            self.assertDictEqual(exp_email, act_email)

        self.assertEqual(expected_email_info, actual_email_info)

    def test_content_list_one_email(self, task_history_request):
        """ Test listing of bulk emails when email list has one email """
        response = self.get_email_content_response(1, task_history_request)
        self.assertTrue(task_history_request.called)
        email_info = json.loads(response.content)['emails']

        # Emails list should have one email
        self.assertEqual(len(email_info), 1)

        # Email content should be what's expected
        expected_message = self.emails[0].html_message
        returned_email_info = email_info[0]
        received_message = returned_email_info[u'email'][u'html_message']
        self.assertEqual(expected_message, received_message)

    def test_content_list_no_emails(self, task_history_request):
        """ Test listing of bulk emails when email list empty """
        response = self.get_email_content_response(0, task_history_request)
        self.assertTrue(task_history_request.called)
        email_info = json.loads(response.content)['emails']

        # Emails list should be empty
        self.assertEqual(len(email_info), 0)

    def test_content_list_email_content_many(self, task_history_request):
        """ Test listing of bulk emails sent large amount of emails """
        self.check_emails_sent(50, task_history_request)

    def test_list_email_content_error(self, task_history_request):
        """ Test handling of error retrieving email """
        invalid_task = FakeContentTask(0, 0, 0, 'test')
        invalid_task.make_invalid_input()
        task_history_request.return_value = [invalid_task]
        url = reverse('list_email_content', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        self.assertEqual(response.status_code, 200)

        self.assertTrue(task_history_request.called)
        returned_email_info = json.loads(response.content)['emails']
        self.assertEqual(len(returned_email_info), 1)
        returned_info = returned_email_info[0]
        for info in ['created', 'sent_to', 'email', 'number_sent', 'requester']:
            self.assertEqual(returned_info[info], None)

    def test_list_email_with_failure(self, task_history_request):
        """ Test the handling of email task that had failures """
        self.check_emails_sent(1, task_history_request, True)

    def test_list_many_emails_with_failures(self, task_history_request):
        """ Test the handling of many emails with failures """
        self.check_emails_sent(50, task_history_request, True)

    def test_list_email_with_no_successes(self, task_history_request):
        task_info = FakeContentTask(0, 0, 10, 'expected')
        email = FakeEmail(0)
        email_info = FakeEmailInfo(email, 0, 10)
        task_history_request.return_value = [task_info]
        url = reverse('list_email_content', kwargs={'course_id': self.course.id.to_deprecated_string()})
        with patch('instructor.views.api.CourseEmail.objects.get') as mock_email_info:
            mock_email_info.return_value = email
            response = self.client.get(url, {})
        self.assertEqual(response.status_code, 200)

        self.assertTrue(task_history_request.called)
        returned_info_list = json.loads(response.content)['emails']

        self.assertEqual(len(returned_info_list), 1)
        returned_info = returned_info_list[0]
        expected_info = email_info.to_dict()
        self.assertDictEqual(expected_info, returned_info)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@override_settings(ANALYTICS_SERVER_URL="http://robotanalyticsserver.netbot:900/")
@override_settings(ANALYTICS_API_KEY="robot_api_key")
class TestInstructorAPIAnalyticsProxy(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test instructor analytics proxy endpoint.
    """

    class FakeProxyResponse(object):
        """ Fake successful requests response object. """

        def __init__(self):
            self.status_code = requests.status_codes.codes.OK
            self.content = '{"test_content": "robot test content"}'

    class FakeBadProxyResponse(object):
        """ Fake strange-failed requests response object. """

        def __init__(self):
            self.status_code = 'notok.'
            self.content = '{"test_content": "robot test content"}'

    def setUp(self):
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

    @patch.object(instructor.views.api.requests, 'get')
    def test_analytics_proxy_url(self, act):
        """ Test legacy analytics proxy url generation. """
        act.return_value = self.FakeProxyResponse()

        url = reverse('proxy_legacy_analytics', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'aname': 'ProblemGradeDistribution'
        })
        self.assertEqual(response.status_code, 200)

        # check request url
        expected_url = "{url}get?aname={aname}&course_id={course_id!s}&apikey={api_key}".format(
            url="http://robotanalyticsserver.netbot:900/",
            aname="ProblemGradeDistribution",
            course_id=self.course.id.to_deprecated_string(),
            api_key="robot_api_key",
        )
        act.assert_called_once_with(expected_url)

    @patch.object(instructor.views.api.requests, 'get')
    def test_analytics_proxy(self, act):
        """
        Test legacy analytics content proxyin, actg.
        """
        act.return_value = self.FakeProxyResponse()

        url = reverse('proxy_legacy_analytics', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'aname': 'ProblemGradeDistribution'
        })
        self.assertEqual(response.status_code, 200)

        # check response
        self.assertTrue(act.called)
        expected_res = {'test_content': "robot test content"}
        self.assertEqual(json.loads(response.content), expected_res)

    @patch.object(instructor.views.api.requests, 'get')
    def test_analytics_proxy_reqfailed(self, act):
        """ Test proxy when server reponds with failure. """
        act.return_value = self.FakeBadProxyResponse()

        url = reverse('proxy_legacy_analytics', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'aname': 'ProblemGradeDistribution'
        })
        self.assertEqual(response.status_code, 500)

    @patch.object(instructor.views.api.requests, 'get')
    def test_analytics_proxy_missing_param(self, act):
        """ Test proxy when missing the aname query parameter. """
        act.return_value = self.FakeProxyResponse()

        url = reverse('proxy_legacy_analytics', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(act.called)


class TestInstructorAPIHelpers(TestCase):
    """ Test helpers for instructor.api """

    def test_split_input_list(self):
        strings = []
        lists = []
        strings.append(
            "Lorem@ipsum.dolor, sit@amet.consectetur\nadipiscing@elit.Aenean\r convallis@at.lacus\r, ut@lacinia.Sed")
        lists.append(['Lorem@ipsum.dolor', 'sit@amet.consectetur', 'adipiscing@elit.Aenean', 'convallis@at.lacus',
                      'ut@lacinia.Sed'])

        for (stng, lst) in zip(strings, lists):
            self.assertEqual(_split_input_list(stng), lst)

    def test_split_input_list_unicode(self):
        self.assertEqual(_split_input_list('robot@robot.edu, robot2@robot.edu'),
                         ['robot@robot.edu', 'robot2@robot.edu'])
        self.assertEqual(_split_input_list(u'robot@robot.edu, robot2@robot.edu'),
                         ['robot@robot.edu', 'robot2@robot.edu'])
        self.assertEqual(_split_input_list(u'robot@robot.edu, robot2@robot.edu'),
                         [u'robot@robot.edu', 'robot2@robot.edu'])
        scary_unistuff = unichr(40960) + u'abcd' + unichr(1972)
        self.assertEqual(_split_input_list(scary_unistuff), [scary_unistuff])

    def test_msk_from_problem_urlname(self):
        course_id = SlashSeparatedCourseKey('MITx', '6.002x', '2013_Spring')
        name = 'L2Node1'
        output = 'i4x://MITx/6.002x/problem/L2Node1'
        self.assertEqual(msk_from_problem_urlname(course_id, name).to_deprecated_string(), output)

    @raises(ValueError)
    def test_msk_from_problem_urlname_error(self):
        args = ('notagoodcourse', 'L2Node1')
        msk_from_problem_urlname(*args)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestDueDateExtensions(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test data dumps for reporting.
    """

    def setUp(self):
        """
        Fixtures.
        """
        super(TestDueDateExtensions, self).setUp()

        due = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=utc)
        course = CourseFactory.create()
        week1 = ItemFactory.create(due=due)
        week2 = ItemFactory.create(due=due)
        week3 = ItemFactory.create()  # No due date
        course.children = [week1.location.to_deprecated_string(), week2.location.to_deprecated_string(),
                           week3.location.to_deprecated_string()]

        homework = ItemFactory.create(
            parent_location=week1.location,
            due=due
        )
        week1.children = [homework.location.to_deprecated_string()]

        user1 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=course.id,
            module_state_key=week1.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=course.id,
            module_state_key=week2.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=course.id,
            module_state_key=week3.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=course.id,
            module_state_key=homework.location).save()

        user2 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=course.id,
            module_state_key=week1.location).save()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=course.id,
            module_state_key=homework.location).save()

        user3 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=course.id,
            module_state_key=week1.location).save()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=course.id,
            module_state_key=homework.location).save()

        self.course = course
        self.week1 = week1
        self.homework = homework
        self.week2 = week2
        self.week3 = week3
        self.user1 = user1
        self.user2 = user2

        self.instructor = InstructorFactory(course_key=course.id)
        self.client.login(username=self.instructor.username, password='test')

    def test_change_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week1.location.to_deprecated_string(),
            'due_datetime': '12/30/2013 00:00'
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(datetime.datetime(2013, 12, 30, 0, 0, tzinfo=utc),
                         get_extended_due(self.course, self.week1, self.user1))

    def test_change_to_invalid_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week1.location.to_deprecated_string(),
            'due_datetime': '01/01/2009 00:00'
        })
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            None,
            get_extended_due(self.course, self.week1, self.user1)
        )

    def test_change_nonexistent_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week3.location.to_deprecated_string(),
            'due_datetime': '12/30/2013 00:00'
        })
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            None,
            get_extended_due(self.course, self.week3, self.user1)
        )

    def test_reset_date(self):
        self.test_change_due_date()
        url = reverse('reset_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week1.location.to_deprecated_string(),
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            None,
            get_extended_due(self.course, self.week1, self.user1)
        )

    def test_reset_nonexistent_extension(self):
        url = reverse('reset_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week1.location.to_deprecated_string(),
        })
        self.assertEqual(response.status_code, 400, response.content)

    def test_reset_extension_to_deleted_date(self):
        """
        Test that we can delete a due date extension after deleting the normal
        due date, without causing an error.
        """
        self.test_change_due_date()
        self.week1.due = None
        self.week1 = self.store.update_item(self.week1, self.user1.id)
        # Now, week1's normal due date is deleted but the extension still exists.
        url = reverse('reset_due_date', kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {
            'student': self.user1.username,
            'url': self.week1.location.to_deprecated_string(),
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            None,
            get_extended_due(self.course, self.week1, self.user1)
        )

    def test_show_unit_extensions(self):
        self.test_change_due_date()
        url = reverse('show_unit_extensions',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {'url': self.week1.location.to_deprecated_string()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content), {
            u'data': [{u'Extended Due Date': u'2013-12-30 00:00',
                       u'Full Name': self.user1.profile.name,
                       u'Username': self.user1.username}],
            u'header': [u'Username', u'Full Name', u'Extended Due Date'],
            u'title': u'Users with due date extensions for %s' %
                      self.week1.display_name})

    def test_show_student_extensions(self):
        self.test_change_due_date()
        url = reverse('show_student_extensions',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})
        response = self.client.get(url, {'student': self.user1.username})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content), {
            u'data': [{u'Extended Due Date': u'2013-12-30 00:00',
                       u'Unit': self.week1.display_name}],
            u'header': [u'Unit', u'Extended Due Date'],
            u'title': u'Due date extensions for %s (%s)' % (
                self.user1.profile.name, self.user1.username)})


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
@override_settings(REGISTRATION_CODE_LENGTH=8)
class TestCourseRegistrationCodes(ModuleStoreTestCase):
    """
    Test data dumps for E-commerce Course Registration Codes.
    """

    def setUp(self):
        """
        Fixtures.
        """
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password='test')

        url = reverse('generate_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {
            'total_registration_codes': 12, 'company_name': 'Test Group', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street',
            'address_line_2': '', 'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        for i in range(5):
            order = Order(user=self.instructor, status='purchased')
            order.save()

        # Spent(used) Registration Codes
        for i in range(5):
            i += 1
            registration_code_redemption = RegistrationCodeRedemption(
                order_id=i, registration_code_id=i, redeemed_by=self.instructor
            )
            registration_code_redemption.save()

    def test_user_invoice_copy_preference(self):
        """
        Test to remember user invoice copy preference
        """
        url_reg_code = reverse('generate_registration_codes',
                               kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {
            'total_registration_codes': 5, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 121.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': 'True'
        }

        # user invoice copy preference will be saved in api user preference; model
        response = self.client.post(url_reg_code, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')

        # get user invoice copy preference.
        url_user_invoice_preference = reverse('get_user_invoice_preference',
                                              kwargs={'course_id': self.course.id.to_deprecated_string()})

        response = self.client.post(url_user_invoice_preference, data)
        result = json.loads(response.content)
        self.assertEqual(result['invoice_copy'], True)

        # updating the user invoice copy preference during code generation flow
        data['invoice'] = ''
        response = self.client.post(url_reg_code, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')

        # get user invoice copy preference.
        url_user_invoice_preference = reverse('get_user_invoice_preference',
                                              kwargs={'course_id': self.course.id.to_deprecated_string()})

        response = self.client.post(url_user_invoice_preference, data)
        result = json.loads(response.content)
        self.assertEqual(result['invoice_copy'], False)

    def test_generate_course_registration_codes_csv(self):
        """
        Test to generate a response of all the generated course registration codes
        """
        url = reverse('generate_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {
            'total_registration_codes': 15, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 17)

    @patch.object(instructor.views.api, 'random_code_generator',
                  Mock(side_effect=['first', 'second', 'third', 'fourth']))
    def test_generate_course_registration_codes_matching_existing_coupon_code(self):
        """
        Test the generated course registration code is already in the Coupon Table
        """
        url = reverse('generate_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        coupon = Coupon(code='first', course_id=self.course.id.to_deprecated_string(), created_by=self.instructor)
        coupon.save()
        data = {
            'total_registration_codes': 3, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 5)  # 1 for headers, 1 for new line at the end and 3 for the actual data

    @patch.object(instructor.views.api, 'random_code_generator',
                  Mock(side_effect=['first', 'first', 'second', 'third']))
    def test_generate_course_registration_codes_integrity_error(self):
        """
       Test for the Integrity error against the generated code
        """
        url = reverse('generate_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {
            'total_registration_codes': 2, 'company_name': 'Test Group', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 4)

    def test_spent_course_registration_codes_csv(self):
        """
        Test to generate a response of all the spent course registration codes
        """
        url = reverse('spent_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {'spent_company_name': ''}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')

        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))

        self.assertEqual(len(body.split('\n')), 7)

        generate_code_url = reverse(
            'generate_registration_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )

        data = {
            'total_registration_codes': 9, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'sale_price': 122.45, 'company_contact_email': 'Test@company.com', 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(generate_code_url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)

        for i in range(9):
            order = Order(user=self.instructor, status='purchased')
            order.save()

        # Spent(used) Registration Codes
        for i in range(9):
            i += 13
            registration_code_redemption = RegistrationCodeRedemption(
                order_id=i, registration_code_id=i, redeemed_by=self.instructor
            )
            registration_code_redemption.save()

        data = {'spent_company_name': 'Group Alpha'}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 11)

    def test_active_course_registration_codes_csv(self):
        """
        Test to generate a response of all the active course registration codes
        """
        url = reverse('active_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})

        data = {'active_company_name': ''}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 9)

        generate_code_url = reverse(
            'generate_registration_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )

        data = {
            'total_registration_codes': 9, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(generate_code_url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)

        data = {'active_company_name': 'Group Alpha'}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 11)

    def test_get_all_course_registration_codes_csv(self):
        """
        Test to generate a response of all the course registration codes
        """
        url = reverse(
            'get_registration_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )

        data = {'download_company_name': ''}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 14)

        generate_code_url = reverse(
            'generate_registration_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )

        data = {
            'total_registration_codes': 9, 'company_name': 'Group Alpha', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': ''
        }

        response = self.client.post(generate_code_url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)

        data = {'download_company_name': 'Group Alpha'}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))
        self.assertEqual(len(body.split('\n')), 11)

    def test_get_codes_with_sale_invoice(self):
        """
        Test to generate a response of all the course registration codes
        """
        generate_code_url = reverse(
            'generate_registration_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )

        data = {
            'total_registration_codes': 5.5, 'company_name': 'Group Invoice', 'company_contact_name': 'Test@company.com',
            'company_contact_email': 'Test@company.com', 'sale_price': 122.45, 'recipient_name': 'Test123',
            'recipient_email': 'test@123.com', 'address_line_1': 'Portland Street', 'address_line_2': '',
            'address_line_3': '', 'city': '', 'state': '', 'zip': '', 'country': '',
            'customer_reference_number': '123A23F', 'internal_reference': '', 'invoice': True
        }

        response = self.client.post(generate_code_url, data, **{'HTTP_HOST': 'localhost'})
        self.assertEqual(response.status_code, 200, response.content)

        url = reverse('get_registration_codes',
                      kwargs={'course_id': self.course.id.to_deprecated_string()})
        data = {'download_company_name': 'Group Invoice'}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_CSV_HEADER))

    def test_get_historical_coupon_codes(self):
        """
        Test to download a response of all the active coupon codes
        """
        get_coupon_code_url = reverse(
            'get_coupon_codes', kwargs={'course_id': self.course.id.to_deprecated_string()}
        )
        for i in range(10):
            coupon = Coupon(
                code='test_code{0}'.format(i), description='test_description', course_id=self.course.id,
                percentage_discount='{0}'.format(i), created_by=self.instructor, is_active=True
            )
            coupon.save()

        response = self.client.get(get_coupon_code_url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.replace('\r', '')
        self.assertTrue(body.startswith(EXPECTED_COUPON_CSV_HEADER))
