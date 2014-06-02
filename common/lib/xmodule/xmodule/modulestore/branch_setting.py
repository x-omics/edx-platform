"""
This file contains functionality for determining the branch to use on a particular thread
"""

import re
import threading
from xmodule.util.django import get_current_request_hostname
from django import settings

class BranchSetting(object):
    """
    Class that encapsulates the logic for determining the thread-specific branch setting to be used by modulestores
    """
    TYPES = ['draft', 'published']
    local_thread_branch = threading.local()

    @classmethod
    def get_value(cls):
        # find value set on local thread
        branch = getattr(cls.local_thread_branch, 'branch_value', None)

        if branch is None:
            branch = cls._get_branch_from_request()
            if branch is None:
                branch = cls._get_branch_from_setting()
            assert branch in cls.TYPES
            cls.local_thread_branch.branch_value = branch

        return branch

    @classmethod
    def set_draft(cls):
        cls.local_thread_branch.branch_value = 'draft'

    @classmethod
    def set_published(cls):
        cls.local_thread_branch.branch_value = 'published'

    @classmethod
    def reset(cls):
        cls.local_thread_branch.branch_value = None

    @classmethod
    def is_draft(cls):
        return cls.get_value() == 'draft'

    @classmethod
    def is_published(cls):
        return cls.get_value() == 'published'

    @classmethod
    def _get_branch_from_request(cls, default_branch=None):
        """
        Returns the branch mapping for the current Django request if configured, else returns the given default
        """
        # see what request we are currently processing - if any at all - and get hostname for the request
        hostname = get_current_request_hostname()

        if hostname:
            # get mapping information which is defined in configurations
            mappings = getattr(settings, 'HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS', None)

            # compare hostname against the regex expressions set of mappings which will tell us which branch to use
            if mappings:
                for key in mappings.keys():
                    if re.match(key, hostname):
                        return mappings[key]

        return default_branch

    @classmethod
    def _get_branch_from_setting(cls):
        """
        Returns the branch value from the configuration settings
        """
        return settings.MODULESTORE_BRANCH
