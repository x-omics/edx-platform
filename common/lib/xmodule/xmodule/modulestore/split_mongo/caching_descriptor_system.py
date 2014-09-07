import sys
import logging
from xblock.runtime import KvsFieldData
from xblock.fields import ScopeIds
from opaque_keys.edx.locator import BlockUsageLocator, LocalId, CourseLocator, DefinitionLocator
from xmodule.mako_module import MakoDescriptorSystem
from xmodule.error_module import ErrorDescriptor
from xmodule.errortracker import exc_info_to_str
from xmodule.modulestore.split_mongo import encode_key_for_mongo
from ..exceptions import ItemNotFoundError
from .split_mongo_kvs import SplitMongoKVS
from fs.osfs import OSFS
from .definition_lazy_loader import DefinitionLazyLoader

log = logging.getLogger(__name__)


class CachingDescriptorSystem(MakoDescriptorSystem):
    """
    A system that has a cache of a course version's json that it will use to load modules
    from, with a backup of calling to the underlying modulestore for more data.

    Computes the settings (nee 'metadata') inheritance upon creation.
    """
    def __init__(self, modulestore, course_entry, default_class, module_data, lazy, **kwargs):
        """
        Computes the settings inheritance and sets up the cache.

        modulestore: the module store that can be used to retrieve additional
        modules

        course_entry: the originally fetched enveloped course_structure w/ branch and course id info.
        Callers to _load_item provide an override but that function ignores the provided structure and
        only looks at the branch and course id

        module_data: a dict mapping Location -> json that was cached from the
            underlying modulestore
        """
        # needed by capa_problem (as runtime.filestore via this.resources_fs)
        if 'course' in course_entry:
            root = modulestore.fs_root / course_entry['org'] / course_entry['course'] / course_entry['run']
        else:
            root = modulestore.fs_root / course_entry['structure']['_id']
        root.makedirs_p()  # create directory if it doesn't exist

        super(CachingDescriptorSystem, self).__init__(
            field_data=None,
            load_item=self._load_item,
            resources_fs = OSFS(root),
            **kwargs
        )
        self.modulestore = modulestore
        self.course_entry = course_entry
        self.lazy = lazy
        self.module_data = module_data
        # Compute inheritance
        modulestore.inherit_settings(
            course_entry['structure'].get('blocks', {}),
            course_entry['structure'].get('blocks', {}).get(
                encode_key_for_mongo(course_entry['structure'].get('root'))
            )
        )
        self.default_class = default_class
        self.local_modules = {}

    def _load_item(self, usage_key, course_entry_override=None, **kwargs):
        # usage_key is either a UsageKey or just the block_id. if a usage_key,
        if isinstance(usage_key, BlockUsageLocator):
            if isinstance(usage_key.block_id, LocalId):
                try:
                    return self.local_modules[usage_key]
                except KeyError:
                    raise ItemNotFoundError
            else:
                block_id = usage_key.block_id
        else:
            block_id = usage_key

        if isinstance(usage_key, BlockUsageLocator):
            # trust the passed in key to know the caller's expectations of which fields are filled in.
            # particularly useful for strip_keys so may go away when we're version aware
            course_key = usage_key.course_key
        else:
            course_info = course_entry_override or self.course_entry
            course_key = CourseLocator(
                version_guid=course_info['structure']['_id'],
                org=course_info.get('org'),
                course=course_info.get('course'),
                run=course_info.get('run'),
                branch=course_info.get('branch'),
            )
        json_data = self.module_data.get(block_id)
        if json_data is None:
            # deeper than initial descendant fetch or doesn't exist
            self.modulestore.cache_items(self, [block_id], course_key, lazy=self.lazy)
            json_data = self.module_data.get(block_id)
            if json_data is None:
                raise ItemNotFoundError(block_id)

        class_ = self.load_block_type(json_data.get('category'))
        new_item = self.xblock_from_json(class_, course_key, block_id, json_data, course_entry_override, **kwargs)
        return new_item

    # xblock's runtime does not always pass enough contextual information to figure out
    # which named container (course x branch) or which parent is requesting an item. Because split allows
    # a many:1 mapping from named containers to structures and because item's identities encode
    # context as well as unique identity, this function must sometimes infer whether the access is
    # within an unspecified named container. In most cases, course_entry_override will give the
    # explicit context; however, runtime.get_block(), e.g., does not. HOWEVER, there are simple heuristics
    # which will work 99.999% of the time: a runtime is thread & even context specific. The likelihood that
    # the thread is working with more than one named container pointing to the same specific structure is
    # low; thus, the course_entry is most likely correct. If the thread is looking at > 1 named container
    # pointing to the same structure, the access is likely to be chunky enough that the last known container
    # is the intended one when not given a course_entry_override; thus, the caching of the last branch/course id.
    def xblock_from_json(self, class_, course_key, block_id, json_data, course_entry_override=None, **kwargs):
        if course_entry_override is None:
            course_entry_override = self.course_entry
        else:
            # most recent retrieval is most likely the right one for next caller (see comment above fn)
            self.course_entry['branch'] = course_entry_override['branch']
            self.course_entry['org'] = course_entry_override['org']
            self.course_entry['course'] = course_entry_override['course']
            self.course_entry['run'] = course_entry_override['run']

        definition_id = json_data.get('definition')
        block_type = json_data['category']

        if definition_id is not None and not json_data.get('definition_loaded', False):
            definition_loader = DefinitionLazyLoader(
                self.modulestore, block_type, definition_id,
                lambda fields: self.modulestore.convert_references_to_keys(
                    course_key, self.load_block_type(block_type),
                    fields, self.course_entry['structure']['blocks'],
                )
            )
        else:
            definition_loader = None

        # If no definition id is provide, generate an in-memory id
        if definition_id is None:
            definition_id = LocalId()

        # If no usage id is provided, generate an in-memory id
        if block_id is None:
            block_id = LocalId()

        block_locator = BlockUsageLocator(
            course_key,
            block_type=block_type,
            block_id=block_id,
        )

        converted_fields = self.modulestore.convert_references_to_keys(
            block_locator.course_key, class_, json_data.get('fields', {}), self.course_entry['structure']['blocks'],
        )
        kvs = SplitMongoKVS(
            definition_loader,
            converted_fields,
            json_data.get('_inherited_settings'),
            **kwargs
        )
        field_data = KvsFieldData(kvs)

        try:
            module = self.construct_xblock_from_class(
                class_,
                ScopeIds(None, block_type, definition_id, block_locator),
                field_data,
            )
        except Exception:
            log.warning("Failed to load descriptor", exc_info=True)
            return ErrorDescriptor.from_json(
                json_data,
                self,
                BlockUsageLocator(
                    CourseLocator(version_guid=course_entry_override['structure']['_id']),
                    block_type='error',
                    block_id=block_id
                ),
                error_msg=exc_info_to_str(sys.exc_info())
            )

        edit_info = json_data.get('edit_info', {})
        module.edited_by = edit_info.get('edited_by')
        module.edited_on = edit_info.get('edited_on')
        module.subtree_edited_by = None  # TODO - addressed with LMS-11183
        module.subtree_edited_on = None  # TODO - addressed with LMS-11183
        module.published_by = None  # TODO - addressed with LMS-11184
        module.published_date = None  # TODO - addressed with LMS-11184
        module.previous_version = edit_info.get('previous_version')
        module.update_version = edit_info.get('update_version')
        module.source_version = edit_info.get('source_version', None)
        module.definition_locator = DefinitionLocator(block_type, definition_id)
        # decache any pending field settings
        module.save()

        # If this is an in-memory block, store it in this system
        if isinstance(block_locator.block_id, LocalId):
            self.local_modules[block_locator] = module

        return module
