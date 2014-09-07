"""
Instructor API endpoint urls.
"""

from django.conf.urls import patterns, url

urlpatterns = patterns('',  # nopep8
    url(r'^students_update_enrollment$',
        'instructor.views.api.students_update_enrollment', name="students_update_enrollment"),
    url(r'^list_course_role_members$',
        'instructor.views.api.list_course_role_members', name="list_course_role_members"),
    url(r'^modify_access$',
        'instructor.views.api.modify_access', name="modify_access"),
    url(r'^bulk_beta_modify_access$',
        'instructor.views.api.bulk_beta_modify_access', name="bulk_beta_modify_access"),
    url(r'^get_grading_config$',
        'instructor.views.api.get_grading_config', name="get_grading_config"),
    url(r'^get_students_features(?P<csv>/csv)?$',
        'instructor.views.api.get_students_features', name="get_students_features"),
    url(r'^get_purchase_transaction(?P<csv>/csv)?$',
        'instructor.views.api.get_purchase_transaction', name="get_purchase_transaction"),
    url(r'^get_user_invoice_preference$',
        'instructor.views.api.get_user_invoice_preference', name="get_user_invoice_preference"),
    url(r'^get_sale_records(?P<csv>/csv)?$',
        'instructor.views.api.get_sale_records', name="get_sale_records"),
    url(r'^sale_validation_url$',
        'instructor.views.api.sale_validation', name="sale_validation"),
    url(r'^get_anon_ids$',
        'instructor.views.api.get_anon_ids', name="get_anon_ids"),
    url(r'^get_distribution$',
        'instructor.views.api.get_distribution', name="get_distribution"),
    url(r'^get_student_progress_url$',
        'instructor.views.api.get_student_progress_url', name="get_student_progress_url"),
    url(r'^reset_student_attempts$',
        'instructor.views.api.reset_student_attempts', name="reset_student_attempts"),
    url(r'^rescore_problem$',
        'instructor.views.api.rescore_problem', name="rescore_problem"),
    url(r'^list_instructor_tasks$',
        'instructor.views.api.list_instructor_tasks', name="list_instructor_tasks"),
    url(r'^list_background_email_tasks$',
        'instructor.views.api.list_background_email_tasks', name="list_background_email_tasks"),
    url(r'^list_email_content$',
        'instructor.views.api.list_email_content', name="list_email_content"),
    url(r'^list_forum_members$',
        'instructor.views.api.list_forum_members', name="list_forum_members"),
    url(r'^update_forum_role_membership$',
        'instructor.views.api.update_forum_role_membership', name="update_forum_role_membership"),
    url(r'^proxy_legacy_analytics$',
        'instructor.views.api.proxy_legacy_analytics', name="proxy_legacy_analytics"),
    url(r'^send_email$',
        'instructor.views.api.send_email', name="send_email"),
    url(r'^change_due_date$', 'instructor.views.api.change_due_date',
        name='change_due_date'),
    url(r'^reset_due_date$', 'instructor.views.api.reset_due_date',
        name='reset_due_date'),
    url(r'^show_unit_extensions$', 'instructor.views.api.show_unit_extensions',
        name='show_unit_extensions'),
    url(r'^show_student_extensions$', 'instructor.views.api.show_student_extensions',
        name='show_student_extensions'),

    # Grade downloads...
    url(r'^list_report_downloads$',
        'instructor.views.api.list_report_downloads', name="list_report_downloads"),
    url(r'calculate_grades_csv$',
        'instructor.views.api.calculate_grades_csv', name="calculate_grades_csv"),

    # Registration Codes..
    url(r'get_registration_codes$',
        'instructor.views.api.get_registration_codes', name="get_registration_codes"),
    url(r'generate_registration_codes$',
        'instructor.views.api.generate_registration_codes', name="generate_registration_codes"),
    url(r'active_registration_codes$',
        'instructor.views.api.active_registration_codes', name="active_registration_codes"),
    url(r'spent_registration_codes$',
        'instructor.views.api.spent_registration_codes', name="spent_registration_codes"),

    # Coupon Codes..
    url(r'get_coupon_codes',
        'instructor.views.api.get_coupon_codes', name="get_coupon_codes"),

    # spoc gradebook
    url(r'^gradebook$',
        'instructor.views.api.spoc_gradebook', name='spoc_gradebook'),
)
