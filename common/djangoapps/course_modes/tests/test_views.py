import unittest
import decimal
import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.core.urlresolvers import reverse

from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase, mixed_store_config
)

from xmodule.modulestore.tests.factories import CourseFactory
from course_modes.tests.factories import CourseModeFactory
from student.tests.factories import CourseEnrollmentFactory, UserFactory
from student.models import CourseEnrollment


# Since we don't need any XML course fixtures, use a modulestore configuration
# that disables the XML modulestore.
MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {}, include_xml=False)


@ddt.ddt
@override_settings(MODULESTORE=MODULESTORE_CONFIG)
@unittest.skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
class CourseModeViewTest(ModuleStoreTestCase):

    def setUp(self):
        super(CourseModeViewTest, self).setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username="Bob", email="bob@example.com", password="edx")
        self.client.login(username=self.user.username, password="edx")

    @ddt.data(
        # is_active?, enrollment_mode, upgrade?, redirect?
        (True, 'verified', True, False),   # User has an active verified enrollment and is trying to upgrade
        (True, 'verified', False, True),   # User has an active verified enrollment and is not trying to upgrade
        (True, 'honor', True, False),      # User has an active honor enrollment and is trying to upgrade
        (True, 'honor', False, False),     # User has an active honor enrollment and is not trying to upgrade
        (True, 'audit', True, False),      # User has an active audit enrollment and is trying to upgrade
        (True, 'audit', False, False),     # User has an active audit enrollment and is not trying to upgrade
        (False, 'verified', True, True),   # User has an inactive verified enrollment and is trying to upgrade
        (False, 'verified', False, True),  # User has an inactive verified enrollment and is not trying to upgrade
        (False, 'honor', True, True),      # User has an inactive honor enrollment and is trying to upgrade
        (False, 'honor', False, True),     # User has an inactive honor enrollment and is not trying to upgrade
        (False, 'audit', True, True),      # User has an inactive audit enrollment and is trying to upgrade
        (False, 'audit', False, True),     # User has an inactive audit enrollment and is not trying to upgrade
    )
    @ddt.unpack
    def test_redirect_to_dashboard(self, is_active, enrollment_mode, upgrade, redirect):
        # Create the course modes
        for mode in ('audit', 'honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # Enroll the user in the test course
        CourseEnrollmentFactory(
            is_active=is_active,
            mode=enrollment_mode,
            course_id=self.course.id,
            user=self.user
        )

        # Configure whether we're upgrading or not
        get_params = {}
        if upgrade:
            get_params = {'upgrade': True}

        url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        response = self.client.get(url, get_params)

        # Check whether we were correctly redirected
        if redirect:
            self.assertRedirects(response, reverse('dashboard'))
        else:
            self.assertEquals(response.status_code, 200)

    def test_redirect_to_dashboard_no_enrollment(self):
        # Create the course modes
        for mode in ('audit', 'honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # User visits the track selection page directly without ever enrolling
        url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        response = self.client.get(url)

        self.assertRedirects(response, reverse('dashboard'))

    @ddt.data(
        '',
        '1,,2',
        '1, ,2',
        '1, 2, 3'
    )
    def test_suggested_prices(self, price_list):

        # Create the course modes
        for mode in ('audit', 'honor'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        CourseModeFactory(
            mode_slug='verified',
            course_id=self.course.id,
            suggested_prices=price_list
        )

        # Enroll the user in the test course to emulate
        # automatic enrollment
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        # Verify that the prices render correctly
        response = self.client.get(
            reverse('course_modes_choose', args=[unicode(self.course.id)]),
            follow=False,
        )

        self.assertEquals(response.status_code, 200)
        # TODO: Fix it so that response.templates works w/ mako templates, and then assert
        # that the right template rendered

    def test_professional_registration(self):
        # The only course mode is professional ed
        CourseModeFactory(mode_slug='professional', course_id=self.course.id)

        # Go to the "choose your track" page
        choose_track_url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        response = self.client.get(choose_track_url)

        # Expect that we're redirected immediately to the "show requirements" page
        # (since the only available track is professional ed)
        show_reqs_url = reverse('verify_student_show_requirements', args=[unicode(self.course.id)])
        self.assertRedirects(response, show_reqs_url)

        # Now enroll in the course
        CourseEnrollmentFactory(
            user=self.user,
            is_active=True,
            mode="professional",
            course_id=unicode(self.course.id),
        )

        # Expect that this time we're redirected to the dashboard (since we're already registered)
        response = self.client.get(choose_track_url)
        self.assertRedirects(response, reverse('dashboard'))


    # Mapping of course modes to the POST parameters sent
    # when the user chooses that mode.
    POST_PARAMS_FOR_COURSE_MODE = {
        'honor': {'honor_mode': True},
        'verified': {'verified_mode': True, 'contribution': '1.23'},
        'unsupported': {'unsupported_mode': True},
    }

    @ddt.data(
        ('honor', 'dashboard'),
        ('verified', 'show_requirements'),
    )
    @ddt.unpack
    def test_choose_mode_redirect(self, course_mode, expected_redirect):
        # Create the course modes
        for mode in ('audit', 'honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # Choose the mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        response = self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE[course_mode])

        # Verify the redirect
        if expected_redirect == 'dashboard':
            redirect_url = reverse('dashboard')
        elif expected_redirect == 'show_requirements':
            redirect_url = reverse(
                'verify_student_show_requirements',
                kwargs={'course_id': unicode(self.course.id)}
            ) + "?upgrade=False"
        else:
            self.fail("Must provide a valid redirect URL name")

        self.assertRedirects(response, redirect_url)

    def test_remember_donation_for_course(self):
        # Create the course modes
        for mode in ('honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # Choose the mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE['verified'])

        # Expect that the contribution amount is stored in the user's session
        self.assertIn('donation_for_course', self.client.session)
        self.assertIn(unicode(self.course.id), self.client.session['donation_for_course'])

        actual_amount = self.client.session['donation_for_course'][unicode(self.course.id)]
        expected_amount = decimal.Decimal(self.POST_PARAMS_FOR_COURSE_MODE['verified']['contribution'])
        self.assertEqual(actual_amount, expected_amount)

    def test_successful_honor_enrollment(self):
        # Create the course modes
        for mode in ('honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # Enroll the user in the default mode (honor) to emulate
        # automatic enrollment
        params = {
            'enrollment_action': 'enroll',
            'course_id': unicode(self.course.id)
        }
        self.client.post(reverse('change_enrollment'), params)

        # Explicitly select the honor mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE['honor'])

        # Verify that the user's enrollment remains unchanged
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        self.assertEqual(mode, 'honor')
        self.assertEqual(is_active, True)

    def test_unsupported_enrollment_mode_failure(self):
        # Create the supported course modes
        for mode in ('honor', 'verified'):
            CourseModeFactory(mode_slug=mode, course_id=self.course.id)

        # Choose an unsupported mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[unicode(self.course.id)])
        response = self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE['unsupported'])

        self.assertEqual(400, response.status_code)
