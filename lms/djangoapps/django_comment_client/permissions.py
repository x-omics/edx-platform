"""
Module for checking permissions with the comment_client backend
"""

import logging
from types import NoneType
from django.core import cache
from lms.lib.comment_client import Thread
from opaque_keys.edx.keys import CourseKey

CACHE = cache.get_cache('default')
CACHE_LIFESPAN = 60


def cached_has_permission(user, permission, course_id=None):
    """
    Call has_permission if it's not cached. A change in a user's role or
    a role's permissions will only become effective after CACHE_LIFESPAN seconds.
    """
    assert isinstance(course_id, (NoneType, CourseKey))
    key = u"permission_{user_id:d}_{course_id}_{permission}".format(
        user_id=user.id, course_id=course_id, permission=permission)
    val = CACHE.get(key, None)
    if val not in [True, False]:
        val = has_permission(user, permission, course_id=course_id)
        CACHE.set(key, val, CACHE_LIFESPAN)
    return val


def has_permission(user, permission, course_id=None):
    assert isinstance(course_id, (NoneType, CourseKey))
    for role in user.roles.filter(course_id=course_id):
        if role.has_permission(permission):
            return True
    return False


CONDITIONS = ['is_open', 'is_author', 'is_question_author']


def _check_condition(user, condition, content):
    def check_open(user, content):
        try:
            return content and not content['closed']
        except KeyError:
            return False

    def check_author(user, content):
        try:
            return content and content['user_id'] == str(user.id)
        except KeyError:
            return False

    def check_question_author(user, content):
        if not content:
            return False
        try:
            if content["type"] == "thread":
                return content["thread_type"] == "question" and content["user_id"] == str(user.id)
            else:
                # N.B. This will trigger a comments service query
                return check_question_author(user, Thread(id=content["thread_id"]).to_dict())
        except KeyError:
            return False

    handlers = {
        'is_open': check_open,
        'is_author': check_author,
        'is_question_author': check_question_author,
    }

    return handlers[condition](user, content)


def _check_conditions_permissions(user, permissions, course_id, content):
    """
    Accepts a list of permissions and proceed if any of the permission is valid.
    Note that ["can_view", "can_edit"] will proceed if the user has either
    "can_view" or "can_edit" permission. To use AND operator in between, wrap them in
    a list.
    """

    def test(user, per, operator="or"):
        if isinstance(per, basestring):
            if per in CONDITIONS:
                return _check_condition(user, per, content)
            return cached_has_permission(user, per, course_id=course_id)
        elif isinstance(per, list) and operator in ["and", "or"]:
            results = [test(user, x, operator="and") for x in per]
            if operator == "or":
                return True in results
            elif operator == "and":
                return not False in results
    return test(user, permissions, operator="or")


VIEW_PERMISSIONS = {
    'update_thread': ['edit_content', ['update_thread', 'is_open', 'is_author']],
    'create_comment': [["create_comment", "is_open"]],
    'delete_thread': ['delete_thread', ['update_thread', 'is_author']],
    'update_comment': ['edit_content', ['update_comment', 'is_open', 'is_author']],
    'endorse_comment': ['endorse_comment', 'is_question_author'],
    'openclose_thread': ['openclose_thread'],
    'create_sub_comment': [['create_sub_comment', 'is_open']],
    'delete_comment': ['delete_comment', ['update_comment', 'is_open', 'is_author']],
    'vote_for_comment': [['vote', 'is_open']],
    'undo_vote_for_comment': [['unvote', 'is_open']],
    'vote_for_thread': [['vote', 'is_open']],
    'flag_abuse_for_thread': ['vote'],
    'un_flag_abuse_for_thread': ['vote'],
    'flag_abuse_for_comment': ['vote'],
    'un_flag_abuse_for_comment': ['vote'],
    'undo_vote_for_thread': [['unvote', 'is_open']],
    'pin_thread': ['openclose_thread'],
    'un_pin_thread': ['openclose_thread'],
    'follow_thread': ['follow_thread'],
    'follow_commentable': ['follow_commentable'],
    'follow_user': ['follow_user'],
    'unfollow_thread': ['unfollow_thread'],
    'unfollow_commentable': ['unfollow_commentable'],
    'unfollow_user': ['unfollow_user'],
    'create_thread': ['create_thread'],
}


def check_permissions_by_view(user, course_id, content, name):
    assert isinstance(course_id, CourseKey)
    try:
        p = VIEW_PERMISSIONS[name]
    except KeyError:
        logging.warning("Permission for view named %s does not exist in permissions.py" % name)
    return _check_conditions_permissions(user, p, course_id, content)
