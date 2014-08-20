"""
Integration tests of the payment flow, including course mode selection.
"""

from lxml.html import soupparser
from django.test.utils import override_settings
from django.core.urlresolvers import reverse
from django.conf import settings

from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, mixed_store_config
from student.tests.factories import UserFactory
from student.models import CourseEnrollment
from course_modes.tests.factories import CourseModeFactory
from verify_student.models import SoftwareSecurePhotoVerification


# Since we don't need any XML course fixtures, use a modulestore configuration
# that disables the XML modulestore.
MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {}, include_xml=False)


@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class TestProfEdVerification(ModuleStoreTestCase):
    """
    Integration test for professional ed verification, including course mode selection.
    """

    # Choose an uncommon number for the price so we can search for it on the page
    MIN_PRICE = 1438

    def setUp(self):
        self.user = UserFactory.create(username="rusty", password="test")
        self.client.login(username="rusty", password="test")
        course = CourseFactory.create(org='Robot', number='999', display_name='Test Course')
        self.course_key = course.id
        CourseModeFactory(
            mode_slug="professional",
            course_id=self.course_key,
            min_price=self.MIN_PRICE,
            suggested_prices=''
        )

        self.urls = {
            'course_modes_choose': reverse(
                'course_modes_choose',
                args=[unicode(self.course_key)]
            ),

            'verify_show_student_requirements': reverse(
                'verify_student_show_requirements',
                args=[unicode(self.course_key)]
            ),

            'verify_student_verify': reverse(
                'verify_student_verify',
                args=[unicode(self.course_key)]
            ),

            'verify_student_verified': reverse(
                'verify_student_verified',
                args=[unicode(self.course_key)]
            ) + "?upgrade=False",
        }

    def test_new_user_flow(self):
        # Go to the course mode page, expecting a redirect
        # to the show requirements page
        # because this is a professional ed course
        # (otherwise, the student would have the option to choose their track)
        resp = self.client.get(self.urls['course_modes_choose'], follow=True)
        self.assertRedirects(resp, self.urls['verify_show_student_requirements'])

        # On the show requirements page, verify that there's a link to the verify page
        # (this is the only action the user is allowed to take)
        self.assertContains(resp, self.urls['verify_student_verify'])

        # Simulate the user clicking the button by following the link
        # to the verified page.
        # Since there are no suggested prices for professional ed,
        # expect that only one price is displayed.
        resp = self.client.get(self.urls['verify_student_verify'])
        self.assertEqual(self._prices_on_page(resp.content), [self.MIN_PRICE])

    def test_already_verified_user_flow(self):
        # Simulate the user already being verified
        self._verify_student()

        # Go to the course mode page, expecting a redirect to the
        # verified (past tense!) page.
        resp = self.client.get(self.urls['course_modes_choose'], follow=True)
        self.assertRedirects(resp, self.urls['verify_student_verified'])

        # Since this is a professional ed course, expect that only
        # one price is shown.
        self.assertContains(resp, "Your Course Total is $")
        self.assertContains(resp, str(self.MIN_PRICE))

        # On the verified page, expect that there's a link to payment page
        self.assertContains(resp, '/shoppingcart/payment_fake')

    def test_do_not_auto_register(self):
        # TODO (ECOM-16): Remove once we complete the AB-test of auto-registration.
        session = self.client.session
        session['auto_register'] = True
        session.save()

        # Go to the course mode page, expecting a redirect
        # to the show requirements page.
        resp = self.client.get(self.urls['course_modes_choose'], follow=True)
        self.assertRedirects(resp, self.urls['verify_show_student_requirements'])

        # For professional ed courses, expect that the student is NOT enrolled
        # automatically in the course.
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))

        # Expect that the rendered page says that the student is "registering",
        # not that they've already been registered.
        self.assertIn("You are registering for", resp.content)
        self.assertNotIn("You are now registered", resp.content)

    def _prices_on_page(self, page_content):
        """ Retrieve the available prices on the verify page. """
        html = soupparser.fromstring(page_content)
        xpath_sel = '//li[@class="field contribution-option"]/span[@class="label-value"]/text()'
        return [int(price) for price in html.xpath(xpath_sel)]

    def _verify_student(self):
        """ Simulate that the student's identity has already been verified. """
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
