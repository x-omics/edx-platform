"""
Tests for the Shopping Cart Models
"""
from decimal import Decimal
import datetime

import smtplib
from boto.exception import BotoServerError  # this is a super-class of SESError and catches connection errors

from mock import patch, MagicMock
import pytz
from django.core import mail
from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase
from django.test.utils import override_settings
from django.contrib.auth.models import AnonymousUser
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase, mixed_store_config
)
from xmodule.modulestore.tests.factories import CourseFactory

from shoppingcart.models import (
    Order, OrderItem, CertificateItem,
    InvalidCartItem, CourseRegistrationCode, PaidCourseRegistration, CourseRegCodeItem,
    Donation, OrderItemSubclassPK
)
from student.tests.factories import UserFactory
from student.models import CourseEnrollment
from course_modes.models import CourseMode
from shoppingcart.exceptions import (PurchasedCallbackException, CourseDoesNotExistException,
                                     ItemAlreadyInCartException, AlreadyEnrolledInCourseException)

from opaque_keys.edx.locator import CourseLocator

# Since we don't need any XML course fixtures, use a modulestore configuration
# that disables the XML modulestore.
MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {}, include_xml=False)

@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class OrderTest(ModuleStoreTestCase):
    def setUp(self):
        self.user = UserFactory.create()
        course = CourseFactory.create()
        self.course_key = course.id
        self.other_course_keys = []
        for __ in xrange(1, 5):
            self.other_course_keys.append(CourseFactory.create().id)
        self.cost = 40

    def test_get_cart_for_user(self):
        # create a cart
        cart = Order.get_cart_for_user(user=self.user)
        # add something to it
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        # should return the same cart
        cart2 = Order.get_cart_for_user(user=self.user)
        self.assertEquals(cart2.orderitem_set.count(), 1)

    def test_user_cart_has_items(self):
        anon = AnonymousUser()
        self.assertFalse(Order.user_cart_has_items(anon))
        self.assertFalse(Order.user_cart_has_items(self.user))
        cart = Order.get_cart_for_user(self.user)
        item = OrderItem(order=cart, user=self.user)
        item.save()
        self.assertTrue(Order.user_cart_has_items(self.user))
        self.assertFalse(Order.user_cart_has_items(self.user, [CertificateItem]))
        self.assertFalse(Order.user_cart_has_items(self.user, [PaidCourseRegistration]))

    def test_user_cart_has_paid_course_registration_items(self):
        cart = Order.get_cart_for_user(self.user)
        item = PaidCourseRegistration(order=cart, user=self.user)
        item.save()
        self.assertTrue(Order.user_cart_has_items(self.user, [PaidCourseRegistration]))
        self.assertFalse(Order.user_cart_has_items(self.user, [CertificateItem]))

    def test_user_cart_has_certificate_items(self):
        cart = Order.get_cart_for_user(self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        self.assertTrue(Order.user_cart_has_items(self.user, [CertificateItem]))
        self.assertFalse(Order.user_cart_has_items(self.user, [PaidCourseRegistration]))

    def test_cart_clear(self):
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        CertificateItem.add_to_order(cart, self.other_course_keys[0], self.cost, 'honor')
        self.assertEquals(cart.orderitem_set.count(), 2)
        self.assertTrue(cart.has_items())
        cart.clear()
        self.assertEquals(cart.orderitem_set.count(), 0)
        self.assertFalse(cart.has_items())

    def test_add_item_to_cart_currency_match(self):
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor', currency='eur')
        # verify that a new item has been added
        self.assertEquals(cart.orderitem_set.count(), 1)
        # verify that the cart's currency was updated
        self.assertEquals(cart.currency, 'eur')
        with self.assertRaises(InvalidCartItem):
            CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor', currency='usd')
        # assert that this item did not get added to the cart
        self.assertEquals(cart.orderitem_set.count(), 1)

    def test_total_cost(self):
        cart = Order.get_cart_for_user(user=self.user)
        # add items to the order
        course_costs = [(self.other_course_keys[0], 30),
                        (self.other_course_keys[1], 40),
                        (self.other_course_keys[2], 10),
                        (self.other_course_keys[3], 20)]
        for course, cost in course_costs:
            CertificateItem.add_to_order(cart, course, cost, 'honor')
        self.assertEquals(cart.orderitem_set.count(), len(course_costs))
        self.assertEquals(cart.total_cost, sum(cost for _course, cost in course_costs))

    def test_start_purchase(self):
        # Start the purchase, which will mark the cart as "paying"
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor', currency='usd')
        cart.start_purchase()
        self.assertEqual(cart.status, 'paying')
        for item in cart.orderitem_set.all():
            self.assertEqual(item.status, 'paying')

        # Starting the purchase should be idempotent
        cart.start_purchase()
        self.assertEqual(cart.status, 'paying')
        for item in cart.orderitem_set.all():
            self.assertEqual(item.status, 'paying')

        # If we retrieve the cart for the user, we should get a different order
        next_cart = Order.get_cart_for_user(user=self.user)
        self.assertNotEqual(cart, next_cart)
        self.assertEqual(next_cart.status, 'cart')

        # Complete the first purchase
        cart.purchase()
        self.assertEqual(cart.status, 'purchased')
        for item in cart.orderitem_set.all():
            self.assertEqual(item.status, 'purchased')

        # Starting the purchase again should be a no-op
        cart.start_purchase()
        self.assertEqual(cart.status, 'purchased')
        for item in cart.orderitem_set.all():
            self.assertEqual(item.status, 'purchased')

    def test_purchase(self):
        # This test is for testing the subclassing functionality of OrderItem, but in
        # order to do this, we end up testing the specific functionality of
        # CertificateItem, which is not quite good unit test form. Sorry.
        cart = Order.get_cart_for_user(user=self.user)
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))
        item = CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        # course enrollment object should be created but still inactive
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))
        cart.purchase()
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))

        # test e-mail sending
        self.assertEquals(len(mail.outbox), 1)
        self.assertEquals('Order Payment Confirmation', mail.outbox[0].subject)
        self.assertIn(settings.PAYMENT_SUPPORT_EMAIL, mail.outbox[0].body)
        self.assertIn(unicode(cart.total_cost), mail.outbox[0].body)
        self.assertIn(item.additional_instruction_text, mail.outbox[0].body)

    def test_purchase_item_failure(self):
        # once again, we're testing against the specific implementation of
        # CertificateItem
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        with patch('shoppingcart.models.CertificateItem.save', side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                cart.purchase()
                # verify that we rolled back the entire transaction
                self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))
                # verify that e-mail wasn't sent
                self.assertEquals(len(mail.outbox), 0)

    def test_purchase_twice(self):
        cart = Order.get_cart_for_user(self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        # purchase the cart more than once
        cart.purchase()
        cart.purchase()
        self.assertEquals(len(mail.outbox), 1)

    @patch('shoppingcart.models.log.error')
    def test_purchase_item_email_smtp_failure(self, error_logger):
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        with patch('shoppingcart.models.EmailMessage.send', side_effect=smtplib.SMTPException):
            cart.purchase()
            self.assertTrue(error_logger.called)

    @patch('shoppingcart.models.log.error')
    def test_purchase_item_email_boto_failure(self, error_logger):
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        with patch('shoppingcart.models.send_mail', side_effect=BotoServerError("status", "reason")):
            cart.purchase()
            self.assertTrue(error_logger.called)

    def purchase_with_data(self, cart):
        """ purchase a cart with billing information """
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        cart.purchase(
            first='John',
            last='Smith',
            street1='11 Cambridge Center',
            street2='Suite 101',
            city='Cambridge',
            state='MA',
            postalcode='02412',
            country='US',
            ccnum='1111',
            cardtype='001',
        )

    @patch('shoppingcart.models.render_to_string')
    @patch.dict(settings.FEATURES, {'STORE_BILLING_INFO': True})
    def test_billing_info_storage_on(self, render):
        cart = Order.get_cart_for_user(self.user)
        self.purchase_with_data(cart)
        self.assertNotEqual(cart.bill_to_first, '')
        self.assertNotEqual(cart.bill_to_last, '')
        self.assertNotEqual(cart.bill_to_street1, '')
        self.assertNotEqual(cart.bill_to_street2, '')
        self.assertNotEqual(cart.bill_to_postalcode, '')
        self.assertNotEqual(cart.bill_to_ccnum, '')
        self.assertNotEqual(cart.bill_to_cardtype, '')
        self.assertNotEqual(cart.bill_to_city, '')
        self.assertNotEqual(cart.bill_to_state, '')
        self.assertNotEqual(cart.bill_to_country, '')
        ((_, context), _) = render.call_args
        self.assertTrue(context['has_billing_info'])

    @patch('shoppingcart.models.render_to_string')
    @patch.dict(settings.FEATURES, {'STORE_BILLING_INFO': False})
    def test_billing_info_storage_off(self, render):
        cart = Order.get_cart_for_user(self.user)
        self.purchase_with_data(cart)
        self.assertNotEqual(cart.bill_to_first, '')
        self.assertNotEqual(cart.bill_to_last, '')
        self.assertNotEqual(cart.bill_to_city, '')
        self.assertNotEqual(cart.bill_to_state, '')
        self.assertNotEqual(cart.bill_to_country, '')
        self.assertNotEqual(cart.bill_to_postalcode, '')
        # things we expect to be missing when the feature is off
        self.assertEqual(cart.bill_to_street1, '')
        self.assertEqual(cart.bill_to_street2, '')
        self.assertEqual(cart.bill_to_ccnum, '')
        self.assertEqual(cart.bill_to_cardtype, '')
        ((_, context), _) = render.call_args
        self.assertFalse(context['has_billing_info'])

    mock_gen_inst = MagicMock(return_value=(OrderItemSubclassPK(OrderItem, 1), set([])))

    def test_generate_receipt_instructions_callchain(self):
        """
        This tests the generate_receipt_instructions call chain (ie calling the function on the
        cart also calls it on items in the cart
        """
        cart = Order.get_cart_for_user(self.user)
        item = OrderItem(user=self.user, order=cart)
        item.save()
        self.assertTrue(cart.has_items())
        with patch.object(OrderItem, 'generate_receipt_instructions', self.mock_gen_inst):
            cart.generate_receipt_instructions()
            self.mock_gen_inst.assert_called_with()


class OrderItemTest(TestCase):
    def setUp(self):
        self.user = UserFactory.create()

    def test_order_item_purchased_callback(self):
        """
        This tests that calling purchased_callback on the base OrderItem class raises NotImplementedError
        """
        item = OrderItem(user=self.user, order=Order.get_cart_for_user(self.user))
        with self.assertRaises(NotImplementedError):
            item.purchased_callback()

    def test_order_item_generate_receipt_instructions(self):
        """
        This tests that the generate_receipt_instructions call chain and also
        that calling it on the base OrderItem class returns an empty list
        """
        cart = Order.get_cart_for_user(self.user)
        item = OrderItem(user=self.user, order=cart)
        item.save()
        self.assertTrue(cart.has_items())
        (inst_dict, inst_set) = cart.generate_receipt_instructions()
        self.assertDictEqual({item.pk_with_subclass: set([])}, inst_dict)
        self.assertEquals(set([]), inst_set)


@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class PaidCourseRegistrationTest(ModuleStoreTestCase):
    def setUp(self):
        self.user = UserFactory.create()
        self.cost = 40
        self.course = CourseFactory.create()
        self.course_key = self.course.id
        self.course_mode = CourseMode(course_id=self.course_key,
                                      mode_slug="honor",
                                      mode_display_name="honor cert",
                                      min_price=self.cost)
        self.course_mode.save()
        self.cart = Order.get_cart_for_user(self.user)

    def test_add_to_order(self):
        reg1 = PaidCourseRegistration.add_to_order(self.cart, self.course_key)

        self.assertEqual(reg1.unit_cost, self.cost)
        self.assertEqual(reg1.line_cost, self.cost)
        self.assertEqual(reg1.unit_cost, self.course_mode.min_price)
        self.assertEqual(reg1.mode, "honor")
        self.assertEqual(reg1.user, self.user)
        self.assertEqual(reg1.status, "cart")
        self.assertTrue(PaidCourseRegistration.contained_in_order(self.cart, self.course_key))
        self.assertFalse(PaidCourseRegistration.contained_in_order(
            self.cart, CourseLocator(org="MITx", course="999", run="Robot_Super_Course_abcd"))
        )

        self.assertEqual(self.cart.total_cost, self.cost)

    def test_cart_type_business(self):
        self.cart.order_type = 'business'
        self.cart.save()
        item = CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2)
        self.cart.purchase()
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))
        # check that the registration codes are generated against the order
        self.assertEqual(len(CourseRegistrationCode.objects.filter(order=self.cart)), item.qty)

    def test_add_with_default_mode(self):
        """
        Tests add_to_cart where the mode specified in the argument is NOT in the database
        and NOT the default "honor".  In this case it just adds the user in the CourseMode.DEFAULT_MODE, 0 price
        """
        reg1 = PaidCourseRegistration.add_to_order(self.cart, self.course_key, mode_slug="DNE")

        self.assertEqual(reg1.unit_cost, 0)
        self.assertEqual(reg1.line_cost, 0)
        self.assertEqual(reg1.mode, "honor")
        self.assertEqual(reg1.user, self.user)
        self.assertEqual(reg1.status, "cart")
        self.assertEqual(self.cart.total_cost, 0)
        self.assertTrue(PaidCourseRegistration.contained_in_order(self.cart, self.course_key))

        course_reg_code_item = CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2, mode_slug="DNE")

        self.assertEqual(course_reg_code_item.unit_cost, 0)
        self.assertEqual(course_reg_code_item.line_cost, 0)
        self.assertEqual(course_reg_code_item.mode, "honor")
        self.assertEqual(course_reg_code_item.user, self.user)
        self.assertEqual(course_reg_code_item.status, "cart")
        self.assertEqual(self.cart.total_cost, 0)
        self.assertTrue(CourseRegCodeItem.contained_in_order(self.cart, self.course_key))

    def test_add_course_reg_item_with_no_course_item(self):
        fake_course_id = CourseLocator(org="edx", course="fake", run="course")
        with self.assertRaises(CourseDoesNotExistException):
            CourseRegCodeItem.add_to_order(self.cart, fake_course_id, 2)

    def test_course_reg_item_already_in_cart(self):
        CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2)
        with self.assertRaises(ItemAlreadyInCartException):
            CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2)

    def test_course_reg_item_already_enrolled_in_course(self):
        CourseEnrollment.enroll(self.user, self.course_key)
        with self.assertRaises(AlreadyEnrolledInCourseException):
            CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2)

    def test_purchased_callback(self):
        reg1 = PaidCourseRegistration.add_to_order(self.cart, self.course_key)
        self.cart.purchase()
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))
        reg1 = PaidCourseRegistration.objects.get(id=reg1.id)  # reload from DB to get side-effect
        self.assertEqual(reg1.status, "purchased")

    def test_generate_receipt_instructions(self):
        """
        Add 2 courses to the order and make sure the instruction_set only contains 1 element (no dups)
        """
        course2 = CourseFactory.create()
        course_mode2 = CourseMode(course_id=course2.id,
                                  mode_slug="honor",
                                  mode_display_name="honor cert",
                                  min_price=self.cost)
        course_mode2.save()
        pr1 = PaidCourseRegistration.add_to_order(self.cart, self.course_key)
        pr2 = PaidCourseRegistration.add_to_order(self.cart, course2.id)
        self.cart.purchase()
        inst_dict, inst_set = self.cart.generate_receipt_instructions()
        self.assertEqual(2, len(inst_dict))
        self.assertEqual(1, len(inst_set))
        self.assertIn("dashboard", inst_set.pop())
        self.assertIn(pr1.pk_with_subclass, inst_dict)
        self.assertIn(pr2.pk_with_subclass, inst_dict)

    def test_purchased_callback_exception(self):
        reg1 = PaidCourseRegistration.add_to_order(self.cart, self.course_key)
        reg1.course_id = CourseLocator(org="changed", course="forsome", run="reason")
        reg1.save()
        with self.assertRaises(PurchasedCallbackException):
            reg1.purchased_callback()
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))

        reg1.course_id = CourseLocator(org="abc", course="efg", run="hij")
        reg1.save()
        with self.assertRaises(PurchasedCallbackException):
            reg1.purchased_callback()
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course_key))

        course_reg_code_item = CourseRegCodeItem.add_to_order(self.cart, self.course_key, 2)
        course_reg_code_item.course_id = CourseLocator(org="changed1", course="forsome1", run="reason1")
        course_reg_code_item.save()
        with self.assertRaises(PurchasedCallbackException):
            course_reg_code_item.purchased_callback()

    def test_user_cart_has_both_items(self):
        """
        This test exists b/c having both CertificateItem and PaidCourseRegistration in an order used to break
        PaidCourseRegistration.contained_in_order
        """
        cart = Order.get_cart_for_user(self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        PaidCourseRegistration.add_to_order(self.cart, self.course_key)
        self.assertTrue(PaidCourseRegistration.contained_in_order(cart, self.course_key))


@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class CertificateItemTest(ModuleStoreTestCase):
    """
    Tests for verifying specific CertificateItem functionality
    """
    def setUp(self):
        self.user = UserFactory.create()
        self.cost = 40
        course = CourseFactory.create()
        self.course_key = course.id
        course_mode = CourseMode(course_id=self.course_key,
                                 mode_slug="honor",
                                 mode_display_name="honor cert",
                                 min_price=self.cost)
        course_mode.save()
        course_mode = CourseMode(course_id=self.course_key,
                                 mode_slug="verified",
                                 mode_display_name="verified cert",
                                 min_price=self.cost)
        course_mode.save()

        patcher = patch('student.models.tracker')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_enrollment(self):
        CourseEnrollment.enroll(self.user, self.course_key)
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'verified')
        # verify that we are still enrolled
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))
        self.mock_tracker.reset_mock()
        cart.purchase()
        enrollment = CourseEnrollment.objects.get(user=self.user, course_id=self.course_key)
        self.assertEquals(enrollment.mode, u'verified')

    def test_single_item_template(self):
        cart = Order.get_cart_for_user(user=self.user)
        cert_item = CertificateItem.add_to_order(cart, self.course_key, self.cost, 'verified')

        self.assertEquals(cert_item.single_item_receipt_template,
                          'shoppingcart/verified_cert_receipt.html')

        cert_item = CertificateItem.add_to_order(cart, self.course_key, self.cost, 'honor')
        self.assertEquals(cert_item.single_item_receipt_template,
                          'shoppingcart/receipt.html')

    def test_refund_cert_callback_no_expiration(self):
        # When there is no expiration date on a verified mode, the user can always get a refund
        CourseEnrollment.enroll(self.user, self.course_key, 'verified')
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, self.course_key, self.cost, 'verified')
        cart.purchase()

        CourseEnrollment.unenroll(self.user, self.course_key)
        target_certs = CertificateItem.objects.filter(course_id=self.course_key, user_id=self.user, status='refunded', mode='verified')
        self.assertTrue(target_certs[0])
        self.assertTrue(target_certs[0].refund_requested_time)
        self.assertEquals(target_certs[0].order.status, 'refunded')

    def test_refund_cert_callback_before_expiration(self):
        # If the expiration date has not yet passed on a verified mode, the user can be refunded
        many_days = datetime.timedelta(days=60)

        course = CourseFactory.create()
        course_key = course.id
        course_mode = CourseMode(course_id=course_key,
                                 mode_slug="verified",
                                 mode_display_name="verified cert",
                                 min_price=self.cost,
                                 expiration_datetime=(datetime.datetime.now(pytz.utc) + many_days))
        course_mode.save()

        CourseEnrollment.enroll(self.user, course_key, 'verified')
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, course_key, self.cost, 'verified')
        cart.purchase()

        CourseEnrollment.unenroll(self.user, course_key)
        target_certs = CertificateItem.objects.filter(course_id=course_key, user_id=self.user, status='refunded', mode='verified')
        self.assertTrue(target_certs[0])
        self.assertTrue(target_certs[0].refund_requested_time)
        self.assertEquals(target_certs[0].order.status, 'refunded')

    def test_refund_cert_callback_before_expiration_email(self):
        """ Test that refund emails are being sent correctly. """
        course = CourseFactory.create()
        course_key = course.id
        many_days = datetime.timedelta(days=60)

        course_mode = CourseMode(course_id=course_key,
                                 mode_slug="verified",
                                 mode_display_name="verified cert",
                                 min_price=self.cost,
                                 expiration_datetime=datetime.datetime.now(pytz.utc) + many_days)
        course_mode.save()

        CourseEnrollment.enroll(self.user, course_key, 'verified')
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, course_key, self.cost, 'verified')
        cart.purchase()

        mail.outbox = []
        with patch('shoppingcart.models.log.error') as mock_error_logger:
            CourseEnrollment.unenroll(self.user, course_key)
            self.assertFalse(mock_error_logger.called)
            self.assertEquals(len(mail.outbox), 1)
            self.assertEquals('[Refund] User-Requested Refund', mail.outbox[0].subject)
            self.assertEquals(settings.PAYMENT_SUPPORT_EMAIL, mail.outbox[0].from_email)
            self.assertIn('has requested a refund on Order', mail.outbox[0].body)

    @patch('shoppingcart.models.log.error')
    def test_refund_cert_callback_before_expiration_email_error(self, error_logger):
        # If there's an error sending an email to billing, we need to log this error
        many_days = datetime.timedelta(days=60)

        course = CourseFactory.create()
        course_key = course.id

        course_mode = CourseMode(course_id=course_key,
                                 mode_slug="verified",
                                 mode_display_name="verified cert",
                                 min_price=self.cost,
                                 expiration_datetime=datetime.datetime.now(pytz.utc) + many_days)
        course_mode.save()

        CourseEnrollment.enroll(self.user, course_key, 'verified')
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, course_key, self.cost, 'verified')
        cart.purchase()

        with patch('shoppingcart.models.send_mail', side_effect=smtplib.SMTPException):
            CourseEnrollment.unenroll(self.user, course_key)
            self.assertTrue(error_logger.call_args[0][0].startswith('Failed sending email'))

    def test_refund_cert_callback_after_expiration(self):
        # If the expiration date has passed, the user cannot get a refund
        many_days = datetime.timedelta(days=60)

        course = CourseFactory.create()
        course_key = course.id
        course_mode = CourseMode(course_id=course_key,
                                 mode_slug="verified",
                                 mode_display_name="verified cert",
                                 min_price=self.cost,)
        course_mode.save()

        CourseEnrollment.enroll(self.user, course_key, 'verified')
        cart = Order.get_cart_for_user(user=self.user)
        CertificateItem.add_to_order(cart, course_key, self.cost, 'verified')
        cart.purchase()

        course_mode.expiration_datetime = (datetime.datetime.now(pytz.utc) - many_days)
        course_mode.save()

        CourseEnrollment.unenroll(self.user, course_key)
        target_certs = CertificateItem.objects.filter(course_id=course_key, user_id=self.user, status='refunded', mode='verified')
        self.assertEqual(len(target_certs), 0)

    def test_refund_cert_no_cert_exists(self):
        # If there is no paid certificate, the refund callback should return nothing
        CourseEnrollment.enroll(self.user, self.course_key, 'verified')
        ret_val = CourseEnrollment.unenroll(self.user, self.course_key)
        self.assertFalse(ret_val)


@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class DonationTest(ModuleStoreTestCase):
    """Tests for the donation order item type. """

    COST = Decimal('23.45')

    def setUp(self):
        """Create a test user and order. """
        super(DonationTest, self).setUp()
        self.user = UserFactory.create()
        self.cart = Order.get_cart_for_user(self.user)

    def test_donate_to_org(self):
        # No course ID provided, so this is a donation to the entire organization
        donation = Donation.add_to_order(self.cart, self.COST)
        self._assert_donation(
            donation,
            donation_type="general",
            unit_cost=self.COST,
            line_desc="Donation for edX"
        )

    def test_donate_to_course(self):
        # Create a test course
        course = CourseFactory.create(display_name="Test Course")

        # Donate to the course
        donation = Donation.add_to_order(self.cart, self.COST, course_id=course.id)
        self._assert_donation(
            donation,
            donation_type="course",
            course_id=course.id,
            unit_cost=self.COST,
            line_desc=u"Donation for Test Course"
        )

    def test_confirmation_email(self):
        # Pay for a donation
        Donation.add_to_order(self.cart, self.COST)
        self.cart.start_purchase()
        self.cart.purchase()

        # Check that the tax-deduction information appears in the confirmation email
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEquals('Order Payment Confirmation', email.subject)
        self.assertIn("tax purposes", email.body)

    def test_donate_no_such_course(self):
        fake_course_id = CourseLocator(org="edx", course="fake", run="course")
        with self.assertRaises(CourseDoesNotExistException):
            Donation.add_to_order(self.cart, self.COST, course_id=fake_course_id)

    def _assert_donation(self, donation, donation_type=None, course_id=None, unit_cost=None, line_desc=None):
        """Verify the donation fields and that the donation can be purchased. """
        self.assertEqual(donation.order, self.cart)
        self.assertEqual(donation.user, self.user)
        self.assertEqual(donation.donation_type, donation_type)
        self.assertEqual(donation.course_id, course_id)
        self.assertEqual(donation.qty, 1)
        self.assertEqual(donation.unit_cost, unit_cost)
        self.assertEqual(donation.currency, "usd")
        self.assertEqual(donation.line_desc, line_desc)

        # Verify that the donation is in the cart
        self.assertTrue(self.cart.has_items(item_type=Donation))
        self.assertEqual(self.cart.total_cost, unit_cost)

        # Purchase the item
        self.cart.start_purchase()
        self.cart.purchase()

        # Verify that the donation is marked as purchased
        donation = Donation.objects.get(pk=donation.id)
        self.assertEqual(donation.status, "purchased")
