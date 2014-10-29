import courseware.access as access
import datetime

import mock
from mock import Mock

from django.test import TestCase
from django.test.utils import override_settings

from courseware.tests.factories import UserFactory, StaffFactory, InstructorFactory
from student.tests.factories import AnonymousUserFactory, CourseEnrollmentAllowedFactory
from courseware.tests.tests import TEST_DATA_MIXED_MODULESTORE
import pytz
from opaque_keys.edx.locations import SlashSeparatedCourseKey


# pylint: disable=protected-access
@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class AccessTestCase(TestCase):
    """
    Tests for the various access controls on the student dashboard
    """

    def setUp(self):
        course_key = SlashSeparatedCourseKey('edX', 'toy', '2012_Fall')
        self.course = course_key.make_usage_key('course', course_key.run)
        self.anonymous_user = AnonymousUserFactory()
        self.student = UserFactory()
        self.global_staff = UserFactory(is_staff=True)
        self.course_staff = StaffFactory(course_key=self.course.course_key)
        self.course_instructor = InstructorFactory(course_key=self.course.course_key)

    def test_has_access_to_course(self):
        self.assertFalse(access._has_access_to_course(
            None, 'staff', self.course.course_key
        ))

        self.assertFalse(access._has_access_to_course(
            self.anonymous_user, 'staff', self.course.course_key
        ))
        self.assertFalse(access._has_access_to_course(
            self.anonymous_user, 'instructor', self.course.course_key
        ))

        self.assertTrue(access._has_access_to_course(
            self.global_staff, 'staff', self.course.course_key
        ))
        self.assertTrue(access._has_access_to_course(
            self.global_staff, 'instructor', self.course.course_key
        ))

        # A user has staff access if they are in the staff group
        self.assertTrue(access._has_access_to_course(
            self.course_staff, 'staff', self.course.course_key
        ))
        self.assertFalse(access._has_access_to_course(
            self.course_staff, 'instructor', self.course.course_key
        ))

        # A user has staff and instructor access if they are in the instructor group
        self.assertTrue(access._has_access_to_course(
            self.course_instructor, 'staff', self.course.course_key
        ))
        self.assertTrue(access._has_access_to_course(
            self.course_instructor, 'instructor', self.course.course_key
        ))

        # A user does not have staff or instructor access if they are
        # not in either the staff or the the instructor group
        self.assertFalse(access._has_access_to_course(
            self.student, 'staff', self.course.course_key
        ))
        self.assertFalse(access._has_access_to_course(
            self.student, 'instructor', self.course.course_key
        ))

    def test__has_access_string(self):
        user = Mock(is_staff=True)
        self.assertFalse(access._has_access_string(user, 'staff', 'not_global', self.course.course_key))

        user._has_global_staff_access.return_value = True
        self.assertTrue(access._has_access_string(user, 'staff', 'global', self.course.course_key))

        self.assertRaises(ValueError, access._has_access_string, user, 'not_staff', 'global', self.course.course_key)

    def test__has_access_error_desc(self):
        descriptor = Mock()

        self.assertFalse(access._has_access_error_desc(self.student, 'load', descriptor, self.course.course_key))
        self.assertTrue(access._has_access_error_desc(self.course_staff, 'load', descriptor, self.course.course_key))
        self.assertTrue(access._has_access_error_desc(self.course_instructor, 'load', descriptor, self.course.course_key))

        self.assertFalse(access._has_access_error_desc(self.student, 'staff', descriptor, self.course.course_key))
        self.assertTrue(access._has_access_error_desc(self.course_staff, 'staff', descriptor, self.course.course_key))
        self.assertTrue(access._has_access_error_desc(self.course_instructor, 'staff', descriptor, self.course.course_key))

        self.assertFalse(access._has_access_error_desc(self.student, 'instructor', descriptor, self.course.course_key))
        self.assertFalse(access._has_access_error_desc(self.course_staff, 'instructor', descriptor, self.course.course_key))
        self.assertTrue(access._has_access_error_desc(self.course_instructor, 'instructor', descriptor, self.course.course_key))

        with self.assertRaises(ValueError):
            access._has_access_error_desc(self.course_instructor, 'not_load_or_staff', descriptor, self.course.course_key)

    def test__has_access_descriptor(self):
        # TODO: override DISABLE_START_DATES and test the start date branch of the method
        user = Mock()
        descriptor = Mock()

        # Always returns true because DISABLE_START_DATES is set in test.py
        self.assertTrue(access._has_access_descriptor(user, 'load', descriptor))
        self.assertTrue(access._has_access_descriptor(user, 'instructor', descriptor))
        with self.assertRaises(ValueError):
            access._has_access_descriptor(user, 'not_load_or_staff', descriptor)

    @mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test__has_access_descriptor_staff_lock(self):
        """
        Tests that "visible_to_staff_only" overrides start date.
        """
        mock_unit = Mock()
        mock_unit._class_tags = {}  # Needed for detached check in _has_access_descriptor

        def verify_access(student_should_have_access):
            """ Verify the expected result from _has_access_descriptor """
            self.assertEqual(student_should_have_access, access._has_access_descriptor(
                self.anonymous_user, 'load', mock_unit, course_key=self.course.course_key)
            )
            # staff always has access
            self.assertTrue(access._has_access_descriptor(
                self.course_staff, 'load', mock_unit, course_key=self.course.course_key)
            )

        # No start date, staff lock on
        mock_unit.visible_to_staff_only = True
        verify_access(False)

        # No start date, staff lock off.
        mock_unit.visible_to_staff_only = False
        verify_access(True)

        # Start date in the past, staff lock on.
        mock_unit.start = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1)
        mock_unit.visible_to_staff_only = True
        verify_access(False)

        # Start date in the past, staff lock off.
        mock_unit.visible_to_staff_only = False
        verify_access(True)

        # Start date in the future, staff lock on.
        mock_unit.start = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1)  # release date in the future
        mock_unit.visible_to_staff_only = True
        verify_access(False)

        # Start date in the future, staff lock off.
        mock_unit.visible_to_staff_only = False
        verify_access(False)

    def test__has_access_course_desc_can_enroll(self):
        yesterday = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1)
        tomorrow = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1)

        # Non-staff can enroll if authenticated and specifically allowed for that course
        # even outside the open enrollment period
        user = UserFactory.create()
        course = Mock(
            enrollment_start=tomorrow, enrollment_end=tomorrow,
            id=SlashSeparatedCourseKey('edX', 'test', '2012_Fall'), enrollment_domain=''
        )
        CourseEnrollmentAllowedFactory(email=user.email, course_id=course.id)
        self.assertTrue(access._has_access_course_desc(user, 'enroll', course))

        # Staff can always enroll even outside the open enrollment period
        user = StaffFactory.create(course_key=course.id)
        self.assertTrue(access._has_access_course_desc(user, 'enroll', course))

        # Non-staff cannot enroll if it is between the start and end dates and invitation only
        # and not specifically allowed
        course = Mock(
            enrollment_start=yesterday, enrollment_end=tomorrow,
            id=SlashSeparatedCourseKey('edX', 'test', '2012_Fall'), enrollment_domain='',
            invitation_only=True
        )
        user = UserFactory.create()
        self.assertFalse(access._has_access_course_desc(user, 'enroll', course))

        # Non-staff can enroll if it is between the start and end dates and not invitation only
        course = Mock(
            enrollment_start=yesterday, enrollment_end=tomorrow,
            id=SlashSeparatedCourseKey('edX', 'test', '2012_Fall'), enrollment_domain='',
            invitation_only=False
        )
        self.assertTrue(access._has_access_course_desc(user, 'enroll', course))

        # Non-staff cannot enroll outside the open enrollment period if not specifically allowed
        course = Mock(
            enrollment_start=tomorrow, enrollment_end=tomorrow,
            id=SlashSeparatedCourseKey('edX', 'test', '2012_Fall'), enrollment_domain='',
            invitation_only=False
        )
        self.assertFalse(access._has_access_course_desc(user, 'enroll', course))

    def test__user_passed_as_none(self):
        """Ensure has_access handles a user being passed as null"""
        access.has_access(None, 'staff', 'global', None)


class UserRoleTestCase(TestCase):
    """
    Tests for user roles.
    """
    def setUp(self):
        self.course_key = SlashSeparatedCourseKey('edX', 'toy', '2012_Fall')
        self.anonymous_user = AnonymousUserFactory()
        self.student = UserFactory()
        self.global_staff = UserFactory(is_staff=True)
        self.course_staff = StaffFactory(course_key=self.course_key)
        self.course_instructor = InstructorFactory(course_key=self.course_key)

    def test_user_role_staff(self):
        """Ensure that user role is student for staff masqueraded as student."""
        self.assertEqual(
            'staff',
            access.get_user_role(self.course_staff, self.course_key)
        )
        # Masquerade staff
        self.course_staff.masquerade_as_student = True
        self.assertEqual(
            'student',
            access.get_user_role(self.course_staff, self.course_key)
        )

    def test_user_role_instructor(self):
        """Ensure that user role is student for instructor masqueraded as student."""
        self.assertEqual(
            'instructor',
            access.get_user_role(self.course_instructor, self.course_key)
        )
        # Masquerade instructor
        self.course_instructor.masquerade_as_student = True
        self.assertEqual(
            'student',
            access.get_user_role(self.course_instructor, self.course_key)
        )

    def test_user_role_anonymous(self):
        """Ensure that user role is student for anonymous user."""
        self.assertEqual(
            'student',
            access.get_user_role(self.anonymous_user, self.course_key)
        )
