from django.conf.urls import patterns, url

urlpatterns = patterns(
    'student_account.views',
    url(r'^$', 'index', name='account_index'),
    url(r'^email$', 'email_change_request_handler', name='email_change_request'),
    url(r'^email/confirmation/(?P<key>[^/]*)$', 'email_change_confirmation_handler', name='email_change_confirm'),
    url(r'^password$', 'password_change_request_handler', name='password_change_request'),
)
