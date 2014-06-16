"""
Modulestore backed by Mongodb.

Stores individual XModules as single documents with the following
structure:

{
    '_id': <location.as_dict>,
    'metadata': <dict containing all Scope.settings fields>
    'definition': <dict containing all Scope.content fields>
    'definition.children': <list of all child location.to_deprecated_string()s>
}
"""

import pymongo
import sys
import logging
import copy
import re

from bson.son import SON
from fs.osfs import OSFS
from path import path
from datetime import datetime
from pytz import UTC

from importlib import import_module
from xmodule.errortracker import null_error_tracker, exc_info_to_str
from xmodule.mako_module import MakoDescriptorSystem
from xmodule.error_module import ErrorDescriptor
from xmodule.html_module import AboutDescriptor
from xblock.runtime import KvsFieldData
from xblock.exceptions import InvalidScopeError
from xblock.fields import Scope, ScopeIds, Reference, ReferenceList, ReferenceValueDict

from xmodule.modulestore import ModuleStoreWriteBase, MONGO_MODULESTORE_TYPE
from opaque_keys.edx.locations import Location
from xmodule.modulestore.exceptions import ItemNotFoundError, InvalidLocationError
from xmodule.modulestore.inheritance import own_metadata, InheritanceMixin, inherit_metadata, InheritanceKeyValueStore
from xmodule.tabs import StaticTab, CourseTabList
from xblock.core import XBlock
from opaque_keys.edx.locations import SlashSeparatedCourseKey

log = logging.getLogger(__name__)


class InvalidWriteError(Exception):
    """
    Raised to indicate that writing to a particular key
    in the KeyValueStore is disabled
    """
    pass


class MongoKeyValueStore(InheritanceKeyValueStore):
    """
    A KeyValueStore that maps keyed data access to one of the 3 data areas
    known to the MongoModuleStore (data, children, and metadata)
    """
    def __init__(self, data, children, metadata):
        super(MongoKeyValueStore, self).__init__()
        if not isinstance(data, dict):
            self._data = {'data': data}
        else:
            self._data = data
        self._children = children
        self._metadata = metadata

    def get(self, key):
        if key.scope == Scope.children:
            return self._children
        elif key.scope == Scope.parent:
            return None
        elif key.scope == Scope.settings:
            return self._metadata[key.field_name]
        elif key.scope == Scope.content:
            return self._data[key.field_name]
        else:
            raise InvalidScopeError(key)

    def set(self, key, value):
        if key.scope == Scope.children:
            self._children = value
        elif key.scope == Scope.settings:
            self._metadata[key.field_name] = value
        elif key.scope == Scope.content:
            self._data[key.field_name] = value
        else:
            raise InvalidScopeError(key)

    def delete(self, key):
        if key.scope == Scope.children:
            self._children = []
        elif key.scope == Scope.settings:
            if key.field_name in self._metadata:
                del self._metadata[key.field_name]
        elif key.scope == Scope.content:
            if key.field_name in self._data:
                del self._data[key.field_name]
        else:
            raise InvalidScopeError(key)

    def has(self, key):
        if key.scope in (Scope.children, Scope.parent):
            return True
        elif key.scope == Scope.settings:
            return key.field_name in self._metadata
        elif key.scope == Scope.content:
            return key.field_name in self._data
        else:
            return False


class CachingDescriptorSystem(MakoDescriptorSystem):
    """
    A system that has a cache of module json that it will use to load modules
    from, with a backup of calling to the underlying modulestore for more data
    TODO (cdodge) when the 'split module store' work has been completed we can remove all
    references to metadata_inheritance_tree
    """
    def __init__(self, modulestore, course_key, module_data, default_class, cached_metadata, **kwargs):
        """
        modulestore: the module store that can be used to retrieve additional modules

        course_key: the course for which everything in this runtime will be relative

        module_data: a dict mapping Location -> json that was cached from the
            underlying modulestore

        default_class: The default_class to use when loading an
            XModuleDescriptor from the module_data

        cached_metadata: the cache for handling inheritance computation. internal use only

        resources_fs: a filesystem, as per MakoDescriptorSystem

        error_tracker: a function that logs errors for later display to users

        render_template: a function for rendering templates, as per
            MakoDescriptorSystem
        """
        super(CachingDescriptorSystem, self).__init__(
            field_data=None,
            load_item=self.load_item,
            **kwargs
        )

        self.modulestore = modulestore
        self.module_data = module_data
        self.default_class = default_class
        # cdodge: other Systems have a course_id attribute defined. To keep things consistent, let's
        # define an attribute here as well, even though it's None
        self.course_id = course_key
        self.cached_metadata = cached_metadata

    def load_item(self, location):
        """
        Return an XModule instance for the specified location
        """
        assert isinstance(location, Location)
        json_data = self.module_data.get(location)
        if json_data is None:
            module = self.modulestore.get_item(location)
            if module is not None:
                # update our own cache after going to the DB to get cache miss
                self.module_data.update(module.runtime.module_data)
            return module
        else:
            # load the module and apply the inherited metadata
            try:
                category = json_data['location']['category']
                class_ = self.load_block_type(category)


                definition = json_data.get('definition', {})
                metadata = json_data.get('metadata', {})
                for old_name, new_name in getattr(class_, 'metadata_translations', {}).items():
                    if old_name in metadata:
                        metadata[new_name] = metadata[old_name]
                        del metadata[old_name]

                children = [
                    location.course_key.make_usage_key_from_deprecated_string(childloc)
                    for childloc in definition.get('children', [])
                ]
                data = definition.get('data', {})
                if isinstance(data, basestring):
                    data = {'data': data}
                mixed_class = self.mixologist.mix(class_)
                if data is not None:
                    data = self._convert_reference_fields_to_keys(mixed_class, location.course_key, data)
                metadata = self._convert_reference_fields_to_keys(mixed_class, location.course_key, metadata)
                kvs = MongoKeyValueStore(
                    data,
                    children,
                    metadata,
                )

                field_data = KvsFieldData(kvs)
                scope_ids = ScopeIds(None, category, location, location)
                module = self.construct_xblock_from_class(class_, scope_ids, field_data)
                if self.cached_metadata is not None:
                    # parent container pointers don't differentiate between draft and non-draft
                    # so when we do the lookup, we should do so with a non-draft location
                    non_draft_loc = location.replace(revision=None)

                    # Convert the serialized fields values in self.cached_metadata
                    # to python values
                    metadata_to_inherit = self.cached_metadata.get(non_draft_loc.to_deprecated_string(), {})
                    inherit_metadata(module, metadata_to_inherit)

                # restore editing information
                edit_info = json_data.get('edit_info', {})
                module.edited_by = edit_info.get('edited_by')
                module.edited_on = edit_info.get('edited_on')

                # decache any computed pending field settings
                module.save()
                return module
            except:
                log.warning("Failed to load descriptor from %s", json_data, exc_info=True)
                return ErrorDescriptor.from_json(
                    json_data,
                    self,
                    location,
                    error_msg=exc_info_to_str(sys.exc_info())
                )

    def _convert_reference_fields_to_keys(self, class_, course_key, jsonfields):
        """
        Find all fields of type reference and convert the payload into UsageKeys
        :param class_: the XBlock class
        :param course_key: a CourseKey object for the given course
        :param jsonfields: a dict of the jsonified version of the fields
        """
        for field_name, value in jsonfields.iteritems():
            if value:
                field = class_.fields.get(field_name)
                if field is None:
                    continue
                elif isinstance(field, Reference):
                    jsonfields[field_name] = course_key.make_usage_key_from_deprecated_string(value)
                elif isinstance(field, ReferenceList):
                    jsonfields[field_name] = [
                        course_key.make_usage_key_from_deprecated_string(ele) for ele in value
                    ]
                elif isinstance(field, ReferenceValueDict):
                    for key, subvalue in value.iteritems():
                        assert isinstance(subvalue, basestring)
                        value[key] = course_key.make_usage_key_from_deprecated_string(subvalue)
        return jsonfields


# The only thing using this w/ wildcards is contentstore.mongo for asset retrieval
def location_to_query(location, wildcard=True, tag='i4x'):
    """
    Takes a Location and returns a SON object that will query for that location by subfields
    rather than subdoc.
    Fields in location that are None are ignored in the query.

    If `wildcard` is True, then a None in a location is treated as a wildcard
    query. Otherwise, it is searched for literally
    """
    query = location.to_deprecated_son(prefix='_id.', tag=tag)

    if wildcard:
        for key, value in query.items():
            # don't allow wildcards on revision, since public is set as None, so
            # its ambiguous between None as a real value versus None=wildcard
            if value is None and key != '_id.revision':
                del query[key]

    return query


class MongoModuleStore(ModuleStoreWriteBase):
    """
    A Mongodb backed ModuleStore
    """
    reference_type = Location

    # TODO (cpennington): Enable non-filesystem filestores
    # pylint: disable=C0103
    # pylint: disable=W0201
    def __init__(self, doc_store_config, fs_root, render_template,
                 default_class=None,
                 error_tracker=null_error_tracker,
                 i18n_service=None,
                 **kwargs):
        """
        :param doc_store_config: must have a host, db, and collection entries. Other common entries: port, tz_aware.
        """

        super(MongoModuleStore, self).__init__(**kwargs)

        def do_connection(
            db, collection, host, port=27017, tz_aware=True, user=None, password=None, **kwargs
        ):
            """
            Create & open the connection, authenticate, and provide pointers to the collection
            """
            self.database = pymongo.database.Database(
                pymongo.MongoClient(
                    host=host,
                    port=port,
                    tz_aware=tz_aware,
                    document_class=dict,
                    **kwargs
                ),
                db
            )
            self.collection = self.database[collection]

            if user is not None and password is not None:
                self.database.authenticate(user, password)

        do_connection(**doc_store_config)

        # Force mongo to report errors, at the expense of performance
        self.collection.write_concern = {'w': 1}

        if default_class is not None:
            module_path, _, class_name = default_class.rpartition('.')
            class_ = getattr(import_module(module_path), class_name)
            self.default_class = class_
        else:
            self.default_class = None
        self.fs_root = path(fs_root)
        self.error_tracker = error_tracker
        self.render_template = render_template
        self.i18n_service = i18n_service

        self.ignore_write_events_on_courses = set()

    def _compute_metadata_inheritance_tree(self, course_id):
        '''
        TODO (cdodge) This method can be deleted when the 'split module store' work has been completed
        '''
        # get all collections in the course, this query should not return any leaf nodes
        # note this is a bit ugly as when we add new categories of containers, we have to add it here

        block_types_with_children = set(
            name for name, class_ in XBlock.load_classes() if getattr(class_, 'has_children', False)
        )
        query = SON([
            ('_id.tag', 'i4x'),
            ('_id.org', course_id.org),
            ('_id.course', course_id.course),
            ('_id.category', {'$in': list(block_types_with_children)})
        ])
        # we just want the Location, children, and inheritable metadata
        record_filter = {'_id': 1, 'definition.children': 1}

        # just get the inheritable metadata since that is all we need for the computation
        # this minimizes both data pushed over the wire
        for field_name in InheritanceMixin.fields:
            record_filter['metadata.{0}'.format(field_name)] = 1

        # call out to the DB
        resultset = self.collection.find(query, record_filter)

        # it's ok to keep these as deprecated strings b/c the overall cache is indexed by course_key and this
        # is a dictionary relative to that course
        results_by_url = {}
        root = None

        # now go through the results and order them by the location url
        for result in resultset:
            # manually pick it apart b/c the db has tag and we want revision = None regardless
            location = Location._from_deprecated_son(result['_id'], course_id.run).replace(revision=None)

            location_url = location.to_deprecated_string()
            if location_url in results_by_url:
                # found either draft or live to complement the other revision
                existing_children = results_by_url[location_url].get('definition', {}).get('children', [])
                additional_children = result.get('definition', {}).get('children', [])
                total_children = existing_children + additional_children
                results_by_url[location_url].setdefault('definition', {})['children'] = total_children
            results_by_url[location_url] = result
            if location.category == 'course':
                root = location_url

        # now traverse the tree and compute down the inherited metadata
        metadata_to_inherit = {}

        def _compute_inherited_metadata(url):
            """
            Helper method for computing inherited metadata for a specific location url
            """
            my_metadata = results_by_url[url].get('metadata', {})

            # go through all the children and recurse, but only if we have
            # in the result set. Remember results will not contain leaf nodes
            for child in results_by_url[url].get('definition', {}).get('children', []):
                if child in results_by_url:
                    new_child_metadata = copy.deepcopy(my_metadata)
                    new_child_metadata.update(results_by_url[child].get('metadata', {}))
                    results_by_url[child]['metadata'] = new_child_metadata
                    metadata_to_inherit[child] = new_child_metadata
                    _compute_inherited_metadata(child)
                else:
                    # this is likely a leaf node, so let's record what metadata we need to inherit
                    metadata_to_inherit[child] = my_metadata

        if root is not None:
            _compute_inherited_metadata(root)

        return metadata_to_inherit

    def _get_cached_metadata_inheritance_tree(self, course_id, force_refresh=False):
        '''
        TODO (cdodge) This method can be deleted when the 'split module store' work has been completed
        '''
        tree = {}

        if not force_refresh:
            # see if we are first in the request cache (if present)
            if self.request_cache is not None and course_id in self.request_cache.data.get('metadata_inheritance', {}):
                return self.request_cache.data['metadata_inheritance'][course_id]

            # then look in any caching subsystem (e.g. memcached)
            if self.metadata_inheritance_cache_subsystem is not None:
                tree = self.metadata_inheritance_cache_subsystem.get(unicode(course_id), {})
            else:
                logging.warning('Running MongoModuleStore without a metadata_inheritance_cache_subsystem. This is OK in localdev and testing environment. Not OK in production.')

        if not tree:
            # if not in subsystem, or we are on force refresh, then we have to compute
            tree = self._compute_metadata_inheritance_tree(course_id)

            # now write out computed tree to caching subsystem (e.g. memcached), if available
            if self.metadata_inheritance_cache_subsystem is not None:
                self.metadata_inheritance_cache_subsystem.set(unicode(course_id), tree)

        # now populate a request_cache, if available. NOTE, we are outside of the
        # scope of the above if: statement so that after a memcache hit, it'll get
        # put into the request_cache
        if self.request_cache is not None:
            # we can't assume the 'metadatat_inheritance' part of the request cache dict has been
            # defined
            if 'metadata_inheritance' not in self.request_cache.data:
                self.request_cache.data['metadata_inheritance'] = {}
            self.request_cache.data['metadata_inheritance'][course_id] = tree

        return tree

    def refresh_cached_metadata_inheritance_tree(self, course_id, runtime=None):
        """
        Refresh the cached metadata inheritance tree for the org/course combination
        for location

        If given a runtime, it replaces the cached_metadata in that runtime. NOTE: failure to provide
        a runtime may mean that some objects report old values for inherited data.
        """
        if course_id not in self.ignore_write_events_on_courses:
            cached_metadata = self._get_cached_metadata_inheritance_tree(course_id, force_refresh=True)
            if runtime:
                runtime.cached_metadata = cached_metadata

    def _clean_item_data(self, item):
        """
        Renames the '_id' field in item to 'location'
        """
        item['location'] = item['_id']
        del item['_id']

    def _query_children_for_cache_children(self, course_key, items):
        """
        Generate a pymongo in query for finding the items and return the payloads
        """
        # first get non-draft in a round-trip
        query = {
            '_id': {'$in': [
                course_key.make_usage_key_from_deprecated_string(item).to_deprecated_son() for item in items
            ]}
        }
        return list(self.collection.find(query))

    def _cache_children(self, course_key, items, depth=0):
        """
        Returns a dictionary mapping Location -> item data, populated with json data
        for all descendents of items up to the specified depth.
        (0 = no descendents, 1 = children, 2 = grandchildren, etc)
        If depth is None, will load all the children.
        This will make a number of queries that is linear in the depth.
        """

        data = {}
        to_process = list(items)
        while to_process and depth is None or depth >= 0:
            children = []
            for item in to_process:
                self._clean_item_data(item)
                children.extend(item.get('definition', {}).get('children', []))
                data[Location._from_deprecated_son(item['location'], course_key.run)] = item

            if depth == 0:
                break

            # Load all children by id. See
            # http://www.mongodb.org/display/DOCS/Advanced+Queries#AdvancedQueries-%24or
            # for or-query syntax
            to_process = []
            if children:
                to_process = self._query_children_for_cache_children(course_key, children)

            # If depth is None, then we just recurse until we hit all the descendents
            if depth is not None:
                depth -= 1

        return data

    def _load_item(self, course_key, item, data_cache, apply_cached_metadata=True):
        """
        Load an XModuleDescriptor from item, using the children stored in data_cache
        """
        location = Location._from_deprecated_son(item['location'], course_key.run)
        data_dir = getattr(item, 'data_dir', location.course)
        root = self.fs_root / data_dir

        root.makedirs_p()  # create directory if it doesn't exist

        resource_fs = OSFS(root)

        cached_metadata = {}
        if apply_cached_metadata:
            cached_metadata = self._get_cached_metadata_inheritance_tree(course_key)

        services = {}
        if self.i18n_service:
            services["i18n"] = self.i18n_service

        system = CachingDescriptorSystem(
            modulestore=self,
            course_key=course_key,
            module_data=data_cache,
            default_class=self.default_class,
            resources_fs=resource_fs,
            error_tracker=self.error_tracker,
            render_template=self.render_template,
            cached_metadata=cached_metadata,
            mixins=self.xblock_mixins,
            select=self.xblock_select,
            services=services,
        )
        return system.load_item(location)

    def _load_items(self, course_key, items, depth=0):
        """
        Load a list of xmodules from the data in items, with children cached up
        to specified depth
        """
        data_cache = self._cache_children(course_key, items, depth)

        # if we are loading a course object, if we're not prefetching children (depth != 0) then don't
        # bother with the metadata inheritance
        return [
            self._load_item(
                course_key, item, data_cache,
                apply_cached_metadata=(item['location']['category'] != 'course' or depth != 0)
            )
            for item in items
        ]

    def get_courses(self):
        '''
        Returns a list of course descriptors.
        '''
        base_list = sum(
            [
                self._load_items(
                    SlashSeparatedCourseKey(course['_id']['org'], course['_id']['course'], course['_id']['name']),
                    [course]
                )
                for course
                # I tried to add '$and': [{'_id.org': {'$ne': 'edx'}}, {'_id.course': {'$ne': 'templates'}}]
                # but it didn't do the right thing (it filtered all edx and all templates out)
                in self.collection.find({'_id.category': 'course'})
                if not (  # TODO kill this
                    course['_id']['org'] == 'edx' and
                    course['_id']['course'] == 'templates'
                )
            ],
            []
        )
        return [course for course in base_list if not isinstance(course, ErrorDescriptor)]

    def _find_one(self, location):
        '''Look for a given location in the collection.  If revision is not
        specified, returns the latest.  If the item is not present, raise
        ItemNotFoundError.
        '''
        assert isinstance(location, Location)
        item = self.collection.find_one(
            {'_id': location.to_deprecated_son()},
            sort=[('revision', pymongo.ASCENDING)],
        )
        if item is None:
            raise ItemNotFoundError(location)
        return item

    def get_course(self, course_key, depth=0):
        """
        Get the course with the given courseid (org/course/run)
        """
        assert(isinstance(course_key, SlashSeparatedCourseKey))
        location = course_key.make_usage_key('course', course_key.run)
        try:
            return self.get_item(location, depth=depth)
        except ItemNotFoundError:
            return None

    def has_course(self, course_key, ignore_case=False):
        """
        Is the given course in this modulestore

        If ignore_case is True, do a case insensitive search,
        otherwise, do a case sensitive search
        """
        assert(isinstance(course_key, SlashSeparatedCourseKey))
        location = course_key.make_usage_key('course', course_key.run)
        if ignore_case:
            course_query = location.to_deprecated_son('_id.')
            for key in course_query.iterkeys():
                if isinstance(course_query[key], basestring):
                    course_query[key] = re.compile(r"(?i)^{}$".format(course_query[key]))
        else:
            course_query = {'_id': location.to_deprecated_son()}
        return self.collection.find_one(course_query, fields={'_id': True}) is not None

    def has_item(self, usage_key):
        """
        Returns True if location exists in this ModuleStore.
        """
        try:
            self._find_one(usage_key)
            return True
        except ItemNotFoundError:
            return False

    def get_item(self, usage_key, depth=0):
        """
        Returns an XModuleDescriptor instance for the item at location.

        If any segment of the location is None except revision, raises
            xmodule.modulestore.exceptions.InsufficientSpecificationError
        If no object is found at that location, raises
            xmodule.modulestore.exceptions.ItemNotFoundError

        usage_key: a :class:`.UsageKey` instance
        depth (int): An argument that some module stores may use to prefetch
            descendents of the queried modules for more efficient results later
            in the request. The depth is counted in the number of
            calls to get_children() to cache. None indicates to cache all descendents.
        """
        item = self._find_one(usage_key)
        module = self._load_items(usage_key.course_key, [item], depth)[0]
        return module

    @staticmethod
    def _course_key_to_son(course_id, tag='i4x'):
        """
        Generate the partial key to look up items relative to a given course
        """
        return SON([
            ('_id.tag', tag),
            ('_id.org', course_id.org),
            ('_id.course', course_id.course),
        ])

    def get_items(self, course_id, settings=None, content=None, revision=None, **kwargs):
        """
        Returns:
            list of XModuleDescriptor instances for the matching items within the course with
            the given course_id

        NOTE: don't use this to look for courses
        as the course_id is required. Use get_courses which is a lot faster anyway.

        If you don't provide a value for revision, this limits the result to only ones in the
        published course. Call this method on draft mongo store if you want to include drafts.

        Args:
            course_id (CourseKey): the course identifier
            settings (dict): fields to look for which have settings scope. Follows same syntax
                and rules as kwargs below
            content (dict): fields to look for which have content scope. Follows same syntax and
                rules as kwargs below.
            revision (str): the revision of the items you're looking for. (only 'draft' makes sense for
                this modulestore. If you don't provide a revision, it won't retrieve any drafts. If you
                say 'draft', it will only return drafts. If you want one of each matching xblock but
                preferring draft to published, call this same method on the draft modulestore w/o a
                revision qualifier.)
            kwargs (key=value): what to look for within the course.
                Common qualifiers are ``category`` or any field name. if the target field is a list,
                then it searches for the given value in the list not list equivalence.
                Substring matching pass a regex object.
                For this modulestore, ``name`` is a commonly provided key (Location based stores)
                This modulestore does not allow searching dates by comparison or edited_by, previous_version,
                update_version info.
        """
        query = self._course_key_to_son(course_id)
        query['_id.revision'] = revision
        for field in ['category', 'name']:
            if field in kwargs:
                query['_id.' + field] = kwargs.pop(field)

        for key, value in (settings or {}).iteritems():
            query['metadata.' + key] = value
        for key, value in (content or {}).iteritems():
            query['definition.data.' + key] = value
        if 'children' in kwargs:
            query['definition.children'] = kwargs.pop('children')

        query.update(kwargs)
        items = self.collection.find(
            query,
            sort=[('_id.revision', pymongo.ASCENDING)],
        )

        modules = self._load_items(course_id, list(items))
        return modules

    def create_course(self, org, offering, user_id=None, fields=None, **kwargs):
        """
        Creates and returns the course.

        Args:
            org (str): the organization that owns the course
            offering (str): the name of the course offering
            user_id: id of the user creating the course
            fields (dict): Fields to set on the course at initialization
            kwargs: Any optional arguments understood by a subset of modulestores to customize instantiation

        Returns: a CourseDescriptor

        Raises:
            InvalidLocationError: If a course with the same org and offering already exists
        """

        course, _, run = offering.partition('/')
        course_id = SlashSeparatedCourseKey(org, course, run)

        # Check if a course with this org/course has been defined before (case-insensitive)
        course_search_location = SON([
            ('_id.tag', 'i4x'),
            ('_id.org', re.compile(u'^{}$'.format(course_id.org), re.IGNORECASE)),
            ('_id.course', re.compile(u'^{}$'.format(course_id.course), re.IGNORECASE)),
            ('_id.category', 'course'),
        ])
        courses = self.collection.find(course_search_location, fields=('_id'))
        if courses.count() > 0:
            raise InvalidLocationError(
                "There are already courses with the given org and course id: {}".format([
                    course['_id'] for course in courses
                ]))

        location = course_id.make_usage_key('course', course_id.run)
        course = self.create_and_save_xmodule(location, fields=fields, **kwargs)

        # clone a default 'about' overview module as well
        about_location = location.replace(
            category='about',
            name='overview'
        )
        overview_template = AboutDescriptor.get_template('overview.yaml')
        self.create_and_save_xmodule(
            about_location,
            system=course.system,
            definition_data=overview_template.get('data')
        )

        return course

    def delete_course(self, course_key, user_id=None):
        """
        The impl removes all of the db records for the course.
        :param course_key:
        :param user_id:
        """
        course_query = self._course_key_to_son(course_key)
        self.collection.remove(course_query, multi=True)

    def create_xmodule(self, location, definition_data=None, metadata=None, system=None, fields={}):
        """
        Create the new xmodule but don't save it. Returns the new module.

        :param location: a Location--must have a category
        :param definition_data: can be empty. The initial definition_data for the kvs
        :param metadata: can be empty, the initial metadata for the kvs
        :param system: if you already have an xblock from the course, the xblock.runtime value
        """
        # differs from split mongo in that I believe most of this logic should be above the persistence
        # layer but added it here to enable quick conversion. I'll need to reconcile these.
        if metadata is None:
            metadata = {}

        if definition_data is None:
            definition_data = {}

        if system is None:
            services = {}
            if self.i18n_service:
                services["i18n"] = self.i18n_service

            system = CachingDescriptorSystem(
                modulestore=self,
                module_data={},
                course_key=location.course_key,
                default_class=self.default_class,
                resources_fs=None,
                error_tracker=self.error_tracker,
                render_template=self.render_template,
                cached_metadata={},
                mixins=self.xblock_mixins,
                select=self.xblock_select,
                services=services,
            )
        xblock_class = system.load_block_type(location.category)
        dbmodel = self._create_new_field_data(location.category, location, definition_data, metadata)
        xmodule = system.construct_xblock_from_class(
            xblock_class,
            # We're loading a descriptor, so student_id is meaningless
            # We also don't have separate notions of definition and usage ids yet,
            # so we use the location for both.
            ScopeIds(None, location.category, location, location),
            dbmodel,
        )
        if fields is not None:
            for key, value in fields.iteritems():
                setattr(xmodule, key, value)
        # decache any pending field settings from init
        xmodule.save()
        return xmodule

    def create_and_save_xmodule(self, location, definition_data=None, metadata=None, system=None,
                                fields={}, user_id=None):
        """
        Create the new xmodule and save it. Does not return the new module because if the caller
        will insert it as a child, it's inherited metadata will completely change. The difference
        between this and just doing create_xmodule and update_item is this ensures static_tabs get
        pointed to by the course.

        :param location: a Location--must have a category
        :param definition_data: can be empty. The initial definition_data for the kvs
        :param metadata: can be empty, the initial metadata for the kvs
        :param system: if you already have an xblock from the course, the xblock.runtime value
        :param user_id: the user that created the xblock
        """
        # differs from split mongo in that I believe most of this logic should be above the persistence
        # layer but added it here to enable quick conversion. I'll need to reconcile these.
        new_object = self.create_xmodule(location, definition_data, metadata, system, fields)
        location = new_object.scope_ids.usage_id
        self.update_item(new_object, allow_not_found=True)

        # VS[compat] cdodge: This is a hack because static_tabs also have references from the course module, so
        # if we add one then we need to also add it to the policy information (i.e. metadata)
        # we should remove this once we can break this reference from the course to static tabs
        # TODO move this special casing to app tier (similar to attaching new element to parent)
        if location.category == 'static_tab':
            course = self._get_course_for_item(location)
            course.tabs.append(
                StaticTab(
                    name=new_object.display_name,
                    url_slug=new_object.scope_ids.usage_id.name,
                )
            )
            self.update_item(course, user_id=user_id)

        return new_object

    def _get_course_for_item(self, location, depth=0):
        '''
        for a given Xmodule, return the course that it belongs to
        Also we have to assert that this module maps to only one course item - it'll throw an
        assert if not
        '''
        return self.get_course(location.course_key, depth)

    def _update_single_item(self, location, update, user_id):
        """
        Set update on the specified item, and raises ItemNotFoundError
        if the location doesn't exist
        """

        update['edit_info'] = {
            'edited_on': datetime.now(UTC),
            'edited_by': user_id,
        }

        # See http://www.mongodb.org/display/DOCS/Updating for
        # atomic update syntax
        result = self.collection.update(
            {'_id': location.to_deprecated_son()},
            {'$set': update},
            multi=False,
            upsert=True,
            # Must include this to avoid the django debug toolbar (which defines the deprecated "safe=False")
            # from overriding our default value set in the init method.
            safe=self.collection.safe
        )
        if result['n'] == 0:
            raise ItemNotFoundError(location)

    def update_item(self, xblock, user_id=None, allow_not_found=False, force=False):
        """
        Update the persisted version of xblock to reflect its current values.

        xblock: which xblock to persist
        user_id: who made the change (ignored for now by this modulestore)
        allow_not_found: whether to create a new object if one didn't already exist or give an error
        force: force is meaningless for this modulestore
        """
        print "has published_date " + str(hasattr(xblock, 'published_date'))
        try:
            definition_data = self._convert_reference_fields_to_strings(xblock, xblock.get_explicitly_set_fields_by_scope())
            payload = {
                'definition.data': definition_data,
                'metadata': self._convert_reference_fields_to_strings(xblock, own_metadata(xblock)),
            }
            if xblock.has_children:
                children = self._convert_reference_fields_to_strings(xblock, {'children': xblock.children})
                payload.update({'definition.children': children['children']})
            self._update_single_item(xblock.scope_ids.usage_id, payload, user_id)
            # for static tabs, their containing course also records their display name
            if xblock.scope_ids.block_type == 'static_tab':
                course = self._get_course_for_item(xblock.scope_ids.usage_id)
                # find the course's reference to this tab and update the name.
                static_tab = CourseTabList.get_tab_by_slug(course.tabs, xblock.scope_ids.usage_id.name)
                # only update if changed
                if static_tab and static_tab['name'] != xblock.display_name:
                    static_tab['name'] = xblock.display_name
                    self.update_item(course, user_id)

            # recompute (and update) the metadata inheritance tree which is cached
            self.refresh_cached_metadata_inheritance_tree(xblock.scope_ids.usage_id.course_key, xblock.runtime)
            # fire signal that we've written to DB
        except ItemNotFoundError:
            if not allow_not_found:
                raise

    def _convert_reference_fields_to_strings(self, xblock, jsonfields):
        """
        Find all fields of type reference and convert the payload from UsageKeys to deprecated strings
        :param xblock: the XBlock class
        :param jsonfields: a dict of the jsonified version of the fields
        """
        assert isinstance(jsonfields, dict)
        for field_name, value in jsonfields.iteritems():
            if value:
                if isinstance(xblock.fields[field_name], Reference):
                    jsonfields[field_name] = value.to_deprecated_string()
                elif isinstance(xblock.fields[field_name], ReferenceList):
                    jsonfields[field_name] = [
                        ele.to_deprecated_string() for ele in value
                    ]
                elif isinstance(xblock.fields[field_name], ReferenceValueDict):
                    for key, subvalue in value.iteritems():
                        assert isinstance(subvalue, Location)
                        value[key] = subvalue.to_deprecated_string()
        return jsonfields

    # pylint: disable=unused-argument
    def delete_item(self, location, **kwargs):
        """
        Delete an item from this modulestore.

        Args:
            location (UsageKey)
        """
        # pylint: enable=unused-argument
        # VS[compat] cdodge: This is a hack because static_tabs also have references from the course module, so
        # if we add one then we need to also add it to the policy information (i.e. metadata)
        # we should remove this once we can break this reference from the course to static tabs
        if location.category == 'static_tab':
            item = self.get_item(location)
            course = self._get_course_for_item(item.scope_ids.usage_id)
            existing_tabs = course.tabs or []
            course.tabs = [tab for tab in existing_tabs if tab.get('url_slug') != location.name]
            self.update_item(course, '**replace_user**')

        # Must include this to avoid the django debug toolbar (which defines the deprecated "safe=False")
        # from overriding our default value set in the init method.
        self.collection.remove({'_id': location.to_deprecated_son()}, safe=self.collection.safe)
        # recompute (and update) the metadata inheritance tree which is cached
        self.refresh_cached_metadata_inheritance_tree(location.course_key)

    def get_parent_locations(self, location):
        '''Find all locations that are the parents of this location in this
        course.  Needed for path_to_location().
        '''
        query = self._course_key_to_son(location.course_key)
        query['definition.children'] = location.to_deprecated_string()
        items = self.collection.find(query, {'_id': True})
        return [
            location.course_key.make_usage_key(i['_id']['category'], i['_id']['name'])
            for i in items
        ]

    def get_modulestore_type(self, course_id):
        """
        Returns an enumeration-like type reflecting the type of this modulestore
        The return can be one of:
        "xml" (for XML based courses),
        "mongo" for old-style MongoDB backed courses,
        "split" for new-style split MongoDB backed courses.
        """
        return MONGO_MODULESTORE_TYPE

    def get_orphans(self, course_key):
        """
        Return an array all of the locations (deprecated string format) for orphans in the course.
        """
        detached_categories = [name for name, __ in XBlock.load_tagged_classes("detached")]
        query = self._course_key_to_son(course_key)
        query['_id.category'] = {'$nin': detached_categories}
        all_items = self.collection.find(query)
        all_reachable = set()
        item_locs = set()
        for item in all_items:
            if item['_id']['category'] != 'course':
                # It would be nice to change this method to return UsageKeys instead of the deprecated string.
                item_locs.add(
                    Location._from_deprecated_son(item['_id'], course_key.run).replace(revision=None).to_deprecated_string()
                )
            all_reachable = all_reachable.union(item.get('definition', {}).get('children', []))
        item_locs -= all_reachable
        return list(item_locs)

    def get_courses_for_wiki(self, wiki_slug):
        """
        Return the list of courses which use this wiki_slug
        :param wiki_slug: the course wiki root slug
        :return: list of course locations
        """
        courses = self.collection.find({'_id.category': 'course', 'definition.data.wiki_slug': wiki_slug})
        # the course's run == its name. It's the only xblock for which that's necessarily true.
        return [Location._from_deprecated_son(course['_id'], course['_id']['name']) for course in courses]

    def _create_new_field_data(self, _category, _location, definition_data, metadata):
        """
        To instantiate a new xmodule which will be saved latter, set up the dbModel and kvs
        """
        kvs = MongoKeyValueStore(
            definition_data,
            [],
            metadata,
        )

        field_data = KvsFieldData(kvs)
        return field_data
