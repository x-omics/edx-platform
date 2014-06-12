from xblock.fields import Scope

from contentstore.utils import get_modulestore
from cms.lib.xblock.mixin import CmsBlockMixin


class CourseMetadata(object):
    '''
    For CRUD operations on metadata fields which do not have specific editors
    on the other pages including any user generated ones.
    The objects have no predefined attrs but instead are obj encodings of the
    editable metadata.
    '''
    FILTERED_LIST = ['xml_attributes',
                     'start',
                     'end',
                     'enrollment_start',
                     'enrollment_end',
                     'tabs',
                     'graceperiod',
                     'checklists',
                     'show_timezone',
                     'format',
                     'graded',
                     'hide_from_toc',
                     'pdf_textbooks',
                     'name', # from xblock
                     'tags', # from xblock
                     'due'
    ]

    @classmethod
    def fetch(cls, descriptor):
        """
        Fetch the key:value editable course details for the given course from
        persistence and return a CourseMetadata model.
        """
        result = {}

        for field in descriptor.fields.values():
            if field.name in CmsBlockMixin.fields:
                continue

            if field.scope != Scope.settings:
                continue

            if field.name in cls.FILTERED_LIST:
                continue

            result[field.name] = {
                'value': field.read_json(descriptor),
                'display_name': field.display_name,
                'help': field.help,
                'deprecated': field.deprecated
            }

        return result

    @classmethod
    def update_from_json(cls, descriptor, jsondict, filter_tabs=True, user=None):
        """
        Decode the json into CourseMetadata and save any changed attrs to the db.

        Ensures none of the fields are in the blacklist.
        """
        dirty = False

        # Copy the filtered list to avoid permanently changing the class attribute.
        filtered_list = list(cls.FILTERED_LIST)
        # Don't filter on the tab attribute if filter_tabs is False.
        if not filter_tabs:
            filtered_list.remove("tabs")

        for key, model in jsondict.iteritems():
            # should it be an error if one of the filtered list items is in the payload?
            if key in filtered_list:
                continue

            val = model['value']
            if hasattr(descriptor, key) and getattr(descriptor, key) != val:
                dirty = True
                value = descriptor.fields[key].from_json(val)
                setattr(descriptor, key, value)

        if dirty:
            get_modulestore(descriptor.location).update_item(descriptor, user.id if user else None)

        return cls.fetch(descriptor)
