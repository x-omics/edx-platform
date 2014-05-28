"""
Views for support dashboard
"""
import logging

from django.contrib.auth.models import User
from django.views.generic.edit import FormView
from django.views.generic.base import TemplateView
from django.utils.translation import ugettext as _
from django.http import HttpResponseRedirect
from django.contrib import messages
from django import forms
from student.models import CourseEnrollment
from shoppingcart.models import CertificateItem
from opaque_keys.edx.keys import CourseKey
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locations import SlashSeparatedCourseKey

log = logging.getLogger(__name__)


class RefundForm(forms.Form):
    user = forms.EmailField(label=_("Email Address"), required=True)
    course_id = forms.CharField(label=_("Course ID"), required=True)
    confirmed = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_user(self):
        user_email = self.cleaned_data['user']
        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            raise forms.ValidationError(_("User not found"))
        return user

    def clean_course_id(self):
        course_id = self.cleaned_data['course_id']
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            try:
                course_key = SlashSeparatedCourseKey.from_deprecated_string(course_id)
            except InvalidKeyError:
                raise forms.ValidationError(_("Invalid course id"))
        return course_key

    def clean(self):
        user, course_id = self.cleaned_data.get('user'), self.cleaned_data.get('course_id')
        if user and course_id:
            self.cleaned_data['enrollment'] = enrollment = CourseEnrollment.get_or_create_enrollment(user, course_id)
            if enrollment.refundable():
                raise forms.ValidationError(_("Course {course_id} not past the refund window.").format(course_id=course_id))
            try:
                self.cleaned_data['cert'] = enrollment.certificateitem_set.filter(mode='verified', status='purchased')[0]
            except IndexError:
                raise forms.ValidationError(_("No order found for {user} in course {course_id}").format(user=user, course_id=course_id))
        return self.cleaned_data


class SupportDash(TemplateView):
    template_name = 'dashboard/support.html'


class Refund(FormView):
    template_name = 'dashboard/_dashboard_refund.html'
    form_class = RefundForm
    success_url = '/support/'

    def get_context_data(self, **kwargs):
        form = getattr(kwargs['form'], 'cleaned_data', {})
        if form.get('confirmed') == 'true':
            kwargs['cert'] = form.get('cert')
            kwargs['enrollment'] = form.get('enrollment')
        return kwargs

    def form_valid(self, form):
        if form.cleaned_data['confirmed'] == 'true':
            user = form.cleaned_data['user']
            course_id = form.cleaned_data['course_id']
            enrollment = form.cleaned_data['enrollment']
            cert = form.cleaned_data['cert']
            enrollment.can_refund = True
            enrollment.update_enrollment(is_active=False)

            log.info(u"%s manually refunded %s %s", self.request.user, user, course_id)
            messages.success(self.request, _("Unenrolled {user} from {course_id}").format(user=user, course_id=course_id))
            messages.success(self.request, _("Refunded {cost} for order id {order_id}").format(cost=cert.unit_cost, order_id=cert.order.id))
            return HttpResponseRedirect('/support/refund/')
        else:
            form.data = {'user': form.data['user'], 'course_id': form.data['course_id'], 'confirmed': 'true'}
            form.cleaned_data['confirmed'] = 'true'
            log.info(u"%s wants to refund %s %s", self.request.user, form.data['user'], form.data['course_id'])
            return self.form_invalid(form)
