import pymongo
from uuid import uuid4
import ddt
import itertools
from importlib import import_module
from collections import namedtuple
import unittest
import datetime
from pytz import UTC

from xmodule.tests import DATA_DIR
from xmodule.modulestore import ModuleStoreEnum, PublishState
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.exceptions import InvalidVersionError

from opaque_keys.edx.locations import SlashSeparatedCourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

# Mixed modulestore depends on django, so we'll manually configure some django settings
# before importing the module
# TODO remove this import and the configuration -- xmodule should not depend on django!
from django.conf import settings
from xmodule.modulestore.tests.factories import check_mongo_calls
from xmodule.modulestore.search import path_to_location
from xmodule.modulestore.exceptions import DuplicateCourseError
if not settings.configured:
    settings.configure()
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.draft_and_published import UnsupportedRevisionError
from xmodule.modulestore.tests.mongo_connection import MONGO_PORT_NUM, MONGO_HOST


@ddt.ddt
class TestMixedModuleStore(unittest.TestCase):
    """
    Quasi-superclass which tests Location based apps against both split and mongo dbs (Locator and
    Location-based dbs)
    """
    HOST = MONGO_HOST
    PORT = MONGO_PORT_NUM
    DB = 'test_mongo_%s' % uuid4().hex[:5]
    COLLECTION = 'modulestore'
    FS_ROOT = DATA_DIR
    DEFAULT_CLASS = 'xmodule.raw_module.RawDescriptor'
    RENDER_TEMPLATE = lambda t_n, d, ctx = None, nsp = 'main': ''

    MONGO_COURSEID = 'MITx/999/2013_Spring'
    XML_COURSEID1 = 'edX/toy/2012_Fall'
    XML_COURSEID2 = 'edX/simple/2012_Fall'
    BAD_COURSE_ID = 'edX/simple'

    modulestore_options = {
        'default_class': DEFAULT_CLASS,
        'fs_root': DATA_DIR,
        'render_template': RENDER_TEMPLATE,
    }
    DOC_STORE_CONFIG = {
        'host': HOST,
        'port': PORT,
        'db': DB,
        'collection': COLLECTION,
    }
    OPTIONS = {
        'mappings': {
            XML_COURSEID1: 'xml',
            XML_COURSEID2: 'xml',
            BAD_COURSE_ID: 'xml',
        },
        'stores': [
            {
                'NAME': 'draft',
                'ENGINE': 'xmodule.modulestore.mongo.draft.DraftModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
            {
                'NAME': 'split',
                'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
            {
                'NAME': 'xml',
                'ENGINE': 'xmodule.modulestore.xml.XMLModuleStore',
                'OPTIONS': {
                    'data_dir': DATA_DIR,
                    'default_class': 'xmodule.hidden_module.HiddenDescriptor',
                }
            },
        ]
    }

    def _compareIgnoreVersion(self, loc1, loc2, msg=None):
        """
        AssertEqual replacement for CourseLocator
        """
        if loc1.for_branch(None) != loc2.for_branch(None):
            self.fail(self._formatMessage(msg, u"{} != {}".format(unicode(loc1), unicode(loc2))))

    def setUp(self):
        """
        Set up the database for testing
        """
        self.options = getattr(self, 'options', self.OPTIONS)
        self.connection = pymongo.MongoClient(
            host=self.HOST,
            port=self.PORT,
            tz_aware=True,
        )
        self.connection.drop_database(self.DB)
        self.addCleanup(self.connection.drop_database, self.DB)
        self.addCleanup(self.connection.close)
        super(TestMixedModuleStore, self).setUp()

        self.addTypeEqualityFunc(BlockUsageLocator, '_compareIgnoreVersion')
        self.addTypeEqualityFunc(CourseLocator, '_compareIgnoreVersion')
        # define attrs which get set in initdb to quell pylint
        self.writable_chapter_location = self.store = self.fake_location = self.xml_chapter_location = None
        self.course_locations = []

        self.user_id = ModuleStoreEnum.UserID.test

    # pylint: disable=invalid-name
    def _create_course(self, default, course_key):
        """
        Create a course w/ one item in the persistence store using the given course & item location.
        """
        # create course
        self.course = self.store.create_course(course_key.org, course_key.course, course_key.run, self.user_id)
        if isinstance(self.course.id, CourseLocator):
            self.course_locations[self.MONGO_COURSEID] = self.course.location
        else:
            self.assertEqual(self.course.id, course_key)

        # create chapter
        chapter = self.store.create_child(self.user_id, self.course.location, 'chapter', block_id='Overview')
        self.writable_chapter_location = chapter.location

    def _create_block_hierarchy(self):
        """
        Creates a hierarchy of blocks for testing
        Each block's (version_agnostic) location is assigned as a field of the class and can be easily accessed
        """
        BlockInfo = namedtuple('BlockInfo', 'field_name, category, display_name, sub_tree')

        trees = [
            BlockInfo(
                'chapter_x', 'chapter', 'Chapter_x', [
                    BlockInfo(
                        'sequential_x1', 'sequential', 'Sequential_x1', [
                            BlockInfo(
                                'vertical_x1a', 'vertical', 'Vertical_x1a', [
                                    BlockInfo('problem_x1a_1', 'problem', 'Problem_x1a_1', []),
                                    BlockInfo('problem_x1a_2', 'problem', 'Problem_x1a_2', []),
                                    BlockInfo('problem_x1a_3', 'problem', 'Problem_x1a_3', []),
                                    BlockInfo('html_x1a_1', 'html', 'HTML_x1a_1', []),
                                ]
                            )
                        ]
                    )
                ]
            ),
            BlockInfo(
                'chapter_y', 'chapter', 'Chapter_y', [
                    BlockInfo(
                        'sequential_y1', 'sequential', 'Sequential_y1', [
                            BlockInfo(
                                'vertical_y1a', 'vertical', 'Vertical_y1a', [
                                    BlockInfo('problem_y1a_1', 'problem', 'Problem_y1a_1', []),
                                    BlockInfo('problem_y1a_2', 'problem', 'Problem_y1a_2', []),
                                    BlockInfo('problem_y1a_3', 'problem', 'Problem_y1a_3', []),
                                ]
                            )
                        ]
                    )
                ]
            )
        ]

        def create_sub_tree(parent, block_info):
            block = self.store.create_child(
                self.user_id, parent.location,
                block_info.category, block_id=block_info.display_name,
                fields={'display_name': block_info.display_name},
            )
            for tree in block_info.sub_tree:
                create_sub_tree(block, tree)
            setattr(self, block_info.field_name, block.location)

        for tree in trees:
            create_sub_tree(self.course, tree)

    def _course_key_from_string(self, string):
        """
        Get the course key for the given course string
        """
        return self.course_locations[string].course_key

    def _initialize_mixed(self):
        self.store = MixedModuleStore(None, create_modulestore_instance=create_modulestore_instance, **self.options)
        self.addCleanup(self.store.close_all_connections)

    def initdb(self, default):
        """
        Initialize the database and create one test course in it
        """
        # set the default modulestore
        store_configs = self.options['stores']
        for index in range(len(store_configs)):
            if store_configs[index]['NAME'] == default:
                if index > 0:
                    store_configs[index], store_configs[0] = store_configs[0], store_configs[index]
                break
        self._initialize_mixed()

        # convert to CourseKeys
        self.course_locations = {
            course_id: CourseLocator.from_string(course_id)
            for course_id in [self.MONGO_COURSEID, self.XML_COURSEID1, self.XML_COURSEID2]
        }
        # and then to the root UsageKey
        self.course_locations = {
            course_id: course_key.make_usage_key('course', course_key.run)
            for course_id, course_key in self.course_locations.iteritems()  # pylint: disable=maybe-no-member
        }

        self.fake_location = self.course_locations[self.MONGO_COURSEID].course_key.make_usage_key('vertical', 'fake')

        self.xml_chapter_location = self.course_locations[self.XML_COURSEID1].replace(
            category='chapter', name='Overview'
        )
        self._create_course(default, self.course_locations[self.MONGO_COURSEID].course_key)

    @ddt.data('draft', 'split')
    def test_get_modulestore_type(self, default_ms):
        """
        Make sure we get back the store type we expect for given mappings
        """
        self.initdb(default_ms)
        self.assertEqual(self.store.get_modulestore_type(
            self._course_key_from_string(self.XML_COURSEID1)), ModuleStoreEnum.Type.xml
        )
        self.assertEqual(self.store.get_modulestore_type(
            self._course_key_from_string(self.XML_COURSEID2)), ModuleStoreEnum.Type.xml
        )
        mongo_ms_type = ModuleStoreEnum.Type.mongo if default_ms == 'draft' else ModuleStoreEnum.Type.split
        self.assertEqual(self.store.get_modulestore_type(
            self._course_key_from_string(self.MONGO_COURSEID)), mongo_ms_type
        )
        # try an unknown mapping, it should be the 'default' store
        self.assertEqual(self.store.get_modulestore_type(
            SlashSeparatedCourseKey('foo', 'bar', '2012_Fall')), mongo_ms_type
        )

    @ddt.data(*itertools.product(
        (ModuleStoreEnum.Type.mongo, ModuleStoreEnum.Type.split),
        (True, False)
    ))
    @ddt.unpack
    def test_duplicate_course_error(self, default_ms, reset_mixed_mappings):
        """
        Make sure we get back the store type we expect for given mappings
        """
        self._initialize_mixed()
        with self.store.default_store(default_ms):
            self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
            if reset_mixed_mappings:
                self.store.mappings = {}
            with self.assertRaises(DuplicateCourseError):
                self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)

    # split has one lookup for the course and then one for the course items
    @ddt.data(('draft', 1, 0), ('split', 2, 0))
    @ddt.unpack
    def test_has_item(self, default_ms, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        self.assertTrue(self.store.has_item(self.course_locations[self.XML_COURSEID1]))

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.assertTrue(self.store.has_item(self.problem_x1a_1))

        # try negative cases
        self.assertFalse(self.store.has_item(
            self.course_locations[self.XML_COURSEID1].replace(name='not_findable', category='problem')
        ))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.assertFalse(self.store.has_item(self.fake_location))

        # verify that an error is raised when the revision is not valid
        with self.assertRaises(UnsupportedRevisionError):
            self.store.has_item(self.fake_location, revision=ModuleStoreEnum.RevisionOption.draft_preferred)

    # draft is 2 to compute inheritance
    # split is 2 (would be 3 on course b/c it looks up the wiki_slug in definitions)
    @ddt.data(('draft', 2, 0), ('split', 2, 0))
    @ddt.unpack
    def test_get_item(self, default_ms, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        self.assertIsNotNone(self.store.get_item(self.course_locations[self.XML_COURSEID1]))

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.assertIsNotNone(self.store.get_item(self.problem_x1a_1))

        # try negative cases
        with self.assertRaises(ItemNotFoundError):
            self.store.get_item(
                self.course_locations[self.XML_COURSEID1].replace(name='not_findable', category='problem')
            )
        with check_mongo_calls(mongo_store, max_find, max_send):
            with self.assertRaises(ItemNotFoundError):
                self.store.get_item(self.fake_location)

        # verify that an error is raised when the revision is not valid
        with self.assertRaises(UnsupportedRevisionError):
            self.store.get_item(self.fake_location, revision=ModuleStoreEnum.RevisionOption.draft_preferred)

    # compared to get_item for the course, draft asks for both draft and published
    @ddt.data(('draft', 8, 0), ('split', 2, 0))
    @ddt.unpack
    def test_get_items(self, default_ms, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        course_locn = self.course_locations[self.XML_COURSEID1]
        # NOTE: use get_course if you just want the course. get_items is expensive
        modules = self.store.get_items(course_locn.course_key, qualifiers={'category': 'course'})
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0].location, course_locn)

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        course_locn = self.course_locations[self.MONGO_COURSEID]
        with check_mongo_calls(mongo_store, max_find, max_send):
            # NOTE: use get_course if you just want the course. get_items is expensive
            modules = self.store.get_items(course_locn.course_key, qualifiers={'category': 'problem'})
        self.assertEqual(len(modules), 6)

        # verify that an error is raised when the revision is not valid
        with self.assertRaises(UnsupportedRevisionError):
            self.store.get_items(
                self.course_locations[self.MONGO_COURSEID].course_key,
                revision=ModuleStoreEnum.RevisionOption.draft_preferred
            )

    # draft: 2 to look in draft and then published and then 5 for updating ancestors.
    # split: 3 to get the course structure & the course definition (show_calculator is scope content)
    #  before the change. 1 during change to refetch the definition. 3 afterward (b/c it calls get_item to return the "new" object).
    #  2 sends to update index & structure (calculator is a setting field)
    @ddt.data(('draft', 7, 5), ('split', 6, 2))
    @ddt.unpack
    def test_update_item(self, default_ms, max_find, max_send):
        """
        Update should fail for r/o dbs and succeed for r/w ones
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        course = self.store.get_course(self.course_locations[self.XML_COURSEID1].course_key)
        # if following raised, then the test is really a noop, change it
        self.assertFalse(course.show_calculator, "Default changed making test meaningless")
        course.show_calculator = True
        with self.assertRaises(NotImplementedError):  # ensure it doesn't allow writing
            self.store.update_item(course, self.user_id)

        # now do it for a r/w db
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        problem = self.store.get_item(self.problem_x1a_1)
        # if following raised, then the test is really a noop, change it
        self.assertNotEqual(problem.max_attempts, 2, "Default changed making test meaningless")
        problem.max_attempts = 2
        with check_mongo_calls(mongo_store, max_find, max_send):
            problem = self.store.update_item(problem, self.user_id)

        self.assertEqual(problem.max_attempts, 2, "Update didn't persist")

    @ddt.data('draft', 'split')
    def test_has_changes_direct_only(self, default_ms):
        """
        Tests that has_changes() returns false when a new xblock in a direct only category is checked
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create dummy direct only xblocks
        chapter = self.store.create_item(
            self.user_id,
            test_course.id,
            'chapter',
            block_id='vertical_container'
        )

        # Check that neither xblock has changes
        self.assertFalse(self.store.has_changes(test_course))
        self.assertFalse(self.store.has_changes(chapter))

    @ddt.data('draft', 'split')
    def test_has_changes(self, default_ms):
        """
        Tests that has_changes() only returns true when changes are present
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy component to test against
        xblock = self.store.create_item(
            self.user_id,
            test_course.id,
            'vertical',
            block_id='test_vertical'
        )

        # Not yet published, so changes are present
        self.assertTrue(self.store.has_changes(xblock))

        # Publish and verify that there are no unpublished changes
        newXBlock = self.store.publish(xblock.location, self.user_id)
        self.assertFalse(self.store.has_changes(newXBlock))

        # Change the component, then check that there now are changes
        component = self.store.get_item(xblock.location)
        component.display_name = 'Changed Display Name'

        component = self.store.update_item(component, self.user_id)
        self.assertTrue(self.store.has_changes(component))

        # Publish and verify again
        component = self.store.publish(component.location, self.user_id)
        self.assertFalse(self.store.has_changes(component))

    @ddt.data(('draft', 7, 2), ('split', 13, 4))
    @ddt.unpack
    def test_delete_item(self, default_ms, max_find, max_send):
        """
        Delete should reject on r/o db and work on r/w one
        """
        self.initdb(default_ms)

        # r/o try deleting the chapter (is here to ensure it can't be deleted)
        with self.assertRaises(NotImplementedError):
            self.store.delete_item(self.xml_chapter_location, self.user_id)

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.store.delete_item(self.writable_chapter_location, self.user_id)
        # verify it's gone
        with self.assertRaises(ItemNotFoundError):
            self.store.get_item(self.writable_chapter_location)

    @ddt.data(('draft', 8, 2), ('split', 13, 4))
    @ddt.unpack
    def test_delete_private_vertical(self, default_ms, max_find, max_send):
        """
        Because old mongo treated verticals as the first layer which could be draft, it has some interesting
        behavioral properties which this deletion test gets at.
        """
        self.initdb(default_ms)
        # create and delete a private vertical with private children
        private_vert = self.store.create_child(
            # don't use course_location as it may not be the repr
            self.user_id, self.course_locations[self.MONGO_COURSEID],
            'vertical', block_id='private'
        )
        private_leaf = self.store.create_child(
            # don't use course_location as it may not be the repr
            self.user_id, private_vert.location, 'html', block_id='private_leaf'
        )

        # verify pre delete state (just to verify that the test is valid)
        if hasattr(private_vert.location, 'version_guid'):
            # change to the HEAD version
            vert_loc = private_vert.location.for_version(private_leaf.location.version_guid)
        else:
            vert_loc = private_vert.location
        self.assertTrue(self.store.has_item(vert_loc))
        self.assertTrue(self.store.has_item(private_leaf.location))
        course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key, 0)
        self.assertIn(vert_loc, course.children)

        # delete the vertical and ensure the course no longer points to it
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.store.delete_item(vert_loc, self.user_id)
        course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key, 0)
        if hasattr(private_vert.location, 'version_guid'):
            # change to the HEAD version
            vert_loc = private_vert.location.for_version(course.location.version_guid)
            leaf_loc = private_leaf.location.for_version(course.location.version_guid)
        else:
            vert_loc = private_vert.location
            leaf_loc = private_leaf.location
        self.assertFalse(self.store.has_item(vert_loc))
        self.assertFalse(self.store.has_item(leaf_loc))
        self.assertNotIn(vert_loc, course.children)

    @ddt.data(('draft', 4, 1), ('split', 5, 2))
    @ddt.unpack
    def test_delete_draft_vertical(self, default_ms, max_find, max_send):
        """
        Test deleting a draft vertical which has a published version.
        """
        self.initdb(default_ms)

        # reproduce bug STUD-1965
        # create and delete a private vertical with private children
        private_vert = self.store.create_child(
            # don't use course_location as it may not be the repr
             self.user_id, self.course_locations[self.MONGO_COURSEID], 'vertical', block_id='publish'
        )
        private_leaf = self.store.create_child(
            self.user_id, private_vert.location, 'html', block_id='bug_leaf'
        )

        # verify that an error is raised when the revision is not valid
        with self.assertRaises(UnsupportedRevisionError):
            self.store.delete_item(
                private_leaf.location,
                self.user_id,
                revision=ModuleStoreEnum.RevisionOption.draft_preferred
            )

        self.store.publish(private_vert.location, self.user_id)
        private_leaf.display_name = 'change me'
        private_leaf = self.store.update_item(private_leaf, self.user_id)
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        # test succeeds if delete succeeds w/o error
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.store.delete_item(private_leaf.location, self.user_id)

    @ddt.data(('draft', 2, 0), ('split', 3, 0))
    @ddt.unpack
    def test_get_courses(self, default_ms, max_find, max_send):
        self.initdb(default_ms)
        # we should have 3 total courses across all stores
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            courses = self.store.get_courses()
        course_ids = [course.location for course in courses]
        self.assertEqual(len(courses), 3, "Not 3 courses: {}".format(course_ids))
        self.assertIn(self.course_locations[self.MONGO_COURSEID], course_ids)
        self.assertIn(self.course_locations[self.XML_COURSEID1], course_ids)
        self.assertIn(self.course_locations[self.XML_COURSEID2], course_ids)

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            draft_courses = self.store.get_courses(remove_branch=True)
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            published_courses = self.store.get_courses(remove_branch=True)
        self.assertEquals([c.id for c in draft_courses], [c.id for c in published_courses])


    def test_xml_get_courses(self):
        """
        Test that the xml modulestore only loaded the courses from the maps.
        """
        self.initdb('draft')
        xml_store = self.store._get_modulestore_by_type(ModuleStoreEnum.Type.xml)
        courses = xml_store.get_courses()
        self.assertEqual(len(courses), 2)
        course_ids = [course.id for course in courses]
        self.assertIn(self.course_locations[self.XML_COURSEID1].course_key, course_ids)
        self.assertIn(self.course_locations[self.XML_COURSEID2].course_key, course_ids)
        # this course is in the directory from which we loaded courses but not in the map
        self.assertNotIn("edX/toy/TT_2012_Fall", course_ids)

    def test_xml_no_write(self):
        """
        Test that the xml modulestore doesn't allow write ops.
        """
        self.initdb('draft')
        xml_store = self.store._get_modulestore_by_type(ModuleStoreEnum.Type.xml)
        # the important thing is not which exception it raises but that it raises an exception
        with self.assertRaises(AttributeError):
            xml_store.create_course("org", "course", "run", self.user_id)

    # draft is 2 to compute inheritance
    # split is 3 b/c it gets the definition to check whether wiki is set
    @ddt.data(('draft', 2, 0), ('split', 3, 0))
    @ddt.unpack
    def test_get_course(self, default_ms, max_find, max_send):
        """
        This test is here for the performance comparison not functionality. It tests the performance
        of getting an item whose scope.content fields are looked at.
        """
        self.initdb(default_ms)
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            course = self.store.get_item(self.course_locations[self.MONGO_COURSEID])
            self.assertEqual(course.id, self.course_locations[self.MONGO_COURSEID].course_key)

        course = self.store.get_item(self.course_locations[self.XML_COURSEID1])
        self.assertEqual(course.id, self.course_locations[self.XML_COURSEID1].course_key)

    # notice this doesn't test getting a public item via draft_preferred which draft would have 2 hits (split
    # still only 2)
    @ddt.data(('draft', 1, 0), ('split', 2, 0))
    @ddt.unpack
    def test_get_parent_locations(self, default_ms, max_find, max_send):
        """
        Test a simple get parent for a direct only category (i.e, always published)
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            parent = self.store.get_parent_location(self.problem_x1a_1)
        self.assertEqual(parent, self.vertical_x1a)

        parent = self.store.get_parent_location(self.xml_chapter_location)
        self.assertEqual(parent, self.course_locations[self.XML_COURSEID1])

    def verify_get_parent_locations_results(self, expected_results):
        for child_location, parent_location, revision in expected_results:
            self.assertEqual(
                parent_location,
                self.store.get_parent_location(child_location, revision=revision)
            )

    @ddt.data('draft', 'split')
    def test_get_parent_locations_moved_child(self, default_ms):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # publish the course
        self.course = self.store.publish(self.course.location, self.user_id)

        # make drafts of verticals
        self.store.convert_to_draft(self.vertical_x1a, self.user_id)
        self.store.convert_to_draft(self.vertical_y1a, self.user_id)

        # move child problem_x1a_1 to vertical_y1a
        child_to_move_location = self.problem_x1a_1
        new_parent_location = self.vertical_y1a
        old_parent_location = self.vertical_x1a

        old_parent = self.store.get_item(old_parent_location)
        old_parent.children.remove(child_to_move_location.replace(version_guid=old_parent.location.version_guid))
        self.store.update_item(old_parent, self.user_id)

        new_parent = self.store.get_item(new_parent_location)
        new_parent.children.append(child_to_move_location.replace(version_guid=new_parent.location.version_guid))
        self.store.update_item(new_parent, self.user_id)

        self.verify_get_parent_locations_results([
            (child_to_move_location, new_parent_location, None),
            (child_to_move_location, new_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
            (child_to_move_location, old_parent_location.for_branch(ModuleStoreEnum.BranchName.published), ModuleStoreEnum.RevisionOption.published_only),
        ])

        # publish the course again
        self.store.publish(self.course.location, self.user_id)
        self.verify_get_parent_locations_results([
            (child_to_move_location, new_parent_location, None),
            (child_to_move_location, new_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
            (child_to_move_location, new_parent_location.for_branch(ModuleStoreEnum.BranchName.published), ModuleStoreEnum.RevisionOption.published_only),
        ])

    @ddt.data('draft')
    def test_get_parent_locations_deleted_child(self, default_ms):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # publish the course
        self.store.publish(self.course.location, self.user_id)

        # make draft of vertical
        self.store.convert_to_draft(self.vertical_y1a, self.user_id)

        # delete child problem_y1a_1
        child_to_delete_location = self.problem_y1a_1
        old_parent_location = self.vertical_y1a
        self.store.delete_item(child_to_delete_location, self.user_id)

        self.verify_get_parent_locations_results([
            (child_to_delete_location, old_parent_location, None),
            # Note: The following could be an unexpected result, but we want to avoid an extra database call
            (child_to_delete_location, old_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
            (child_to_delete_location, old_parent_location, ModuleStoreEnum.RevisionOption.published_only),
        ])

        # publish the course again
        self.store.publish(self.course.location, self.user_id)
        self.verify_get_parent_locations_results([
            (child_to_delete_location, None, None),
            (child_to_delete_location, None, ModuleStoreEnum.RevisionOption.draft_preferred),
            (child_to_delete_location, None, ModuleStoreEnum.RevisionOption.published_only),
        ])

    @ddt.data(('draft', [10, 3], 0), ('split', [14, 6], 0))
    @ddt.unpack
    def test_path_to_location(self, default_ms, num_finds, num_sends):
        """
        Make sure that path_to_location works
        """
        self.initdb(default_ms)

        course_key = self.course_locations[self.MONGO_COURSEID].course_key
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            self._create_block_hierarchy()

            should_work = (
                (self.problem_x1a_2,
                 (course_key, u"Chapter_x", u"Sequential_x1", '1')),
                (self.chapter_x,
                 (course_key, "Chapter_x", None, None)),
            )

            mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
            for location, expected in should_work:
                with check_mongo_calls(mongo_store, num_finds.pop(0), num_sends):
                    self.assertEqual(path_to_location(self.store, location), expected)

        not_found = (
            course_key.make_usage_key('video', 'WelcomeX'),
            course_key.make_usage_key('course', 'NotHome'),
        )
        for location in not_found:
            with self.assertRaises(ItemNotFoundError):
                path_to_location(self.store, location)

    def test_xml_path_to_location(self):
        """
        Make sure that path_to_location works: should be passed a modulestore
        with the toy and simple courses loaded.
        """
        # only needs course_locations set
        self.initdb('draft')
        course_key = self.course_locations[self.XML_COURSEID1].course_key
        should_work = (
            (course_key.make_usage_key('video', 'Welcome'),
             (course_key, "Overview", "Welcome", None)),
            (course_key.make_usage_key('chapter', 'Overview'),
             (course_key, "Overview", None, None)),
        )

        for location, expected in should_work:
            self.assertEqual(path_to_location(self.store, location), expected)

        not_found = (
            course_key.make_usage_key('video', 'WelcomeX'),
            course_key.make_usage_key('course', 'NotHome'),
        )
        for location in not_found:
            with self.assertRaises(ItemNotFoundError):
                path_to_location(self.store, location)

    @ddt.data('draft')
    def test_revert_to_published_root_draft(self, default_ms):
        """
        Test calling revert_to_published on draft vertical.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        vertical = self.store.get_item(self.vertical_x1a)
        vertical_children_num = len(vertical.children)

        self.store.publish(self.course.location, self.user_id)

        # delete leaf problem (will make parent vertical a draft)
        self.store.delete_item(self.problem_x1a_1, self.user_id)

        draft_parent = self.store.get_item(self.vertical_x1a)
        self.assertEqual(vertical_children_num - 1, len(draft_parent.children))
        published_parent = self.store.get_item(
            self.vertical_x1a,
            revision=ModuleStoreEnum.RevisionOption.published_only
        )
        self.assertEqual(vertical_children_num, len(published_parent.children))

        self.store.revert_to_published(self.vertical_x1a, self.user_id)
        reverted_parent = self.store.get_item(self.vertical_x1a)
        self.assertEqual(vertical_children_num, len(published_parent.children))
        self.assertEqual(reverted_parent, published_parent)

    @ddt.data('draft')
    def test_revert_to_published_root_published(self, default_ms):
        """
        Test calling revert_to_published on a published vertical with a draft child.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        self.store.publish(self.course.location, self.user_id)

        problem = self.store.get_item(self.problem_x1a_1)
        orig_display_name = problem.display_name

        # Change display name of problem and update just it (so parent remains published)
        problem.display_name = "updated before calling revert"
        self.store.update_item(problem, self.user_id)
        self.store.revert_to_published(self.vertical_x1a, self.user_id)

        reverted_problem = self.store.get_item(self.problem_x1a_1)
        self.assertEqual(orig_display_name, reverted_problem.display_name)

    @ddt.data('draft')
    def test_revert_to_published_no_draft(self, default_ms):
        """
        Test calling revert_to_published on vertical with no draft content does nothing.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        self.store.publish(self.course.location, self.user_id)

        orig_vertical = self.store.get_item(self.vertical_x1a)
        self.store.revert_to_published(self.vertical_x1a, self.user_id)
        reverted_vertical = self.store.get_item(self.vertical_x1a)
        self.assertEqual(orig_vertical, reverted_vertical)

    @ddt.data('draft')
    def test_revert_to_published_no_published(self, default_ms):
        """
        Test calling revert_to_published on vertical with no published version errors.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        with self.assertRaises(InvalidVersionError):
            self.store.revert_to_published(self.vertical_x1a, self.user_id)

    @ddt.data('draft')
    def test_revert_to_published_direct_only(self, default_ms):
        """
        Test calling revert_to_published on a direct-only item is a no-op.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        self.store.revert_to_published(self.sequential_x1, self.user_id)
        reverted_parent = self.store.get_item(self.sequential_x1)
        # It does not discard the child vertical, even though that child is a draft (with no published version)
        self.assertEqual(1, len(reverted_parent.children))

    @ddt.data(('draft', 1, 0), ('split', 2, 0))
    @ddt.unpack
    def test_get_orphans(self, default_ms, max_find, max_send):
        """
        Test finding orphans.
        """
        self.initdb(default_ms)
        course_id = self.course_locations[self.MONGO_COURSEID].course_key

        # create parented children
        self._create_block_hierarchy()

        # orphans
        orphan_locations = [
            course_id.make_usage_key('chapter', 'OrphanChapter'),
            course_id.make_usage_key('vertical', 'OrphanVertical'),
            course_id.make_usage_key('problem', 'OrphanProblem'),
            course_id.make_usage_key('html', 'OrphanHTML'),
        ]

        # detached items (not considered as orphans)
        detached_locations = [
            course_id.make_usage_key('static_tab', 'StaticTab'),
            course_id.make_usage_key('course_info', 'updates'),
        ]

        for location in (orphan_locations + detached_locations):
            self.store.create_item(
                self.user_id,
                location.course_key,
                location.block_type,
                block_id=location.block_id
            )

        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            found_orphans = self.store.get_orphans(self.course_locations[self.MONGO_COURSEID].course_key)
        self.assertEqual(set(found_orphans), set(orphan_locations))

    @ddt.data('draft')
    def test_create_item_from_parent_location(self, default_ms):
        """
        Test a code path missed by the above: passing an old-style location as parent but no
        new location for the child
        """
        self.initdb(default_ms)
        self.store.create_child(
            self.user_id,
            self.course_locations[self.MONGO_COURSEID],
            'problem',
            block_id='orphan'
        )
        orphans = self.store.get_orphans(self.course_locations[self.MONGO_COURSEID].course_key)
        self.assertEqual(len(orphans), 0, "unexpected orphans: {}".format(orphans))

    @ddt.data('draft', 'split')
    def test_create_item_populates_edited_info(self, default_ms):
        self.initdb(default_ms)
        block = self.store.create_item(
            self.user_id,
            self.course.location.course_key,
            'problem'
        )
        self.assertEqual(self.user_id, block.edited_by)
        self.assertGreater(datetime.datetime.now(UTC), block.edited_on)

    @ddt.data('draft')
    def test_create_item_populates_subtree_edited_info(self, default_ms):
        self.initdb(default_ms)
        block = self.store.create_item(
            self.user_id,
            self.course.location.course_key,
            'problem'
        )
        self.assertEqual(self.user_id, block.subtree_edited_by)
        self.assertGreater(datetime.datetime.now(UTC), block.subtree_edited_on)

    @ddt.data(('draft', 1, 0), ('split', 1, 0))
    @ddt.unpack
    def test_get_courses_for_wiki(self, default_ms, max_find, max_send):
        """
        Test the get_courses_for_wiki method
        """
        self.initdb(default_ms)
        # Test XML wikis
        wiki_courses = self.store.get_courses_for_wiki('toy')
        self.assertEqual(len(wiki_courses), 1)
        self.assertIn(self.course_locations[self.XML_COURSEID1].course_key, wiki_courses)

        wiki_courses = self.store.get_courses_for_wiki('simple')
        self.assertEqual(len(wiki_courses), 1)
        self.assertIn(self.course_locations[self.XML_COURSEID2].course_key, wiki_courses)

        # Test Mongo wiki
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            wiki_courses = self.store.get_courses_for_wiki('999')
        self.assertEqual(len(wiki_courses), 1)
        self.assertIn(
            self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None),  # Branch agnostic
            wiki_courses
        )

        self.assertEqual(len(self.store.get_courses_for_wiki('edX.simple.2012_Fall')), 0)
        self.assertEqual(len(self.store.get_courses_for_wiki('no_such_wiki')), 0)

    @ddt.data(('draft', 2, 6), ('split', 7, 2))
    @ddt.unpack
    def test_unpublish(self, default_ms, max_find, max_send):
        """
        Test calling unpublish
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # publish
        self.store.publish(self.course.location, self.user_id)
        published_xblock = self.store.get_item(
            self.vertical_x1a,
            revision=ModuleStoreEnum.RevisionOption.published_only
        )
        self.assertIsNotNone(published_xblock)

        # unpublish
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.store.unpublish(self.vertical_x1a, self.user_id)

        with self.assertRaises(ItemNotFoundError):
            self.store.get_item(
                self.vertical_x1a,
                revision=ModuleStoreEnum.RevisionOption.published_only
            )

        # make sure draft version still exists
        draft_xblock = self.store.get_item(
            self.vertical_x1a,
            revision=ModuleStoreEnum.RevisionOption.draft_only
        )
        self.assertIsNotNone(draft_xblock)

    @ddt.data(('draft', 1, 0), ('split', 4, 0))
    @ddt.unpack
    def test_compute_publish_state(self, default_ms, max_find, max_send):
        """
        Test the compute_publish_state method
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # start off as Private
        item = self.store.create_child(self.user_id, self.writable_chapter_location, 'problem', 'test_compute_publish_state')
        item_location = item.location
        mongo_store = self.store._get_modulestore_for_courseid(self._course_key_from_string(self.MONGO_COURSEID))
        with check_mongo_calls(mongo_store, max_find, max_send):
            self.assertEquals(self.store.compute_publish_state(item), PublishState.private)

        # Private -> Public
        self.store.publish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        self.assertEquals(self.store.compute_publish_state(item), PublishState.public)

        # Public -> Private
        self.store.unpublish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        self.assertEquals(self.store.compute_publish_state(item), PublishState.private)

        # Private -> Public
        self.store.publish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        self.assertEquals(self.store.compute_publish_state(item), PublishState.public)

        # Public -> Draft with NO changes
        # Note: This is where Split and Mongo differ
        self.store.convert_to_draft(item_location, self.user_id)
        item = self.store.get_item(item_location)
        self.assertEquals(
            self.store.compute_publish_state(item),
            PublishState.draft if default_ms == 'draft' else PublishState.public
        )

        # Draft WITH changes
        item.display_name = 'new name'
        item = self.store.update_item(item, self.user_id)
        self.assertTrue(self.store.has_changes(item))
        self.assertEquals(self.store.compute_publish_state(item), PublishState.draft)

    @ddt.data('draft', 'split')
    def test_auto_publish(self, default_ms):
        """
        Test that the correct things have been published automatically
        Assumptions:
            * we auto-publish courses, chapters, sequentials
            * we don't auto-publish problems
        """

        self.initdb(default_ms)

        # test create_course to make sure we are autopublishing
        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)
        self.assertEqual(self.store.compute_publish_state(test_course), PublishState.public)

        test_course_key = test_course.id

        # test create_item of direct-only category to make sure we are autopublishing
        chapter = self.store.create_item(self.user_id, test_course_key, 'chapter', 'Overview')
        self.assertEqual(self.store.compute_publish_state(chapter), PublishState.public)

        chapter_location = chapter.location

        # test create_child of direct-only category to make sure we are autopublishing
        sequential = self.store.create_child(self.user_id, chapter_location, 'sequential', 'Sequence')
        self.assertEqual(self.store.compute_publish_state(sequential), PublishState.public)

        # test update_item of direct-only category to make sure we are autopublishing
        sequential.display_name = 'sequential1'
        sequential = self.store.update_item(sequential, self.user_id)
        self.assertEqual(self.store.compute_publish_state(sequential), PublishState.public)

        # test delete_item of direct-only category to make sure we are autopublishing
        self.store.delete_item(sequential.location, self.user_id, revision=ModuleStoreEnum.RevisionOption.all)
        chapter = self.store.get_item(chapter.location.for_branch(None))
        self.assertEqual(self.store.compute_publish_state(chapter), PublishState.public)

        # test create_child of NOT direct-only category to make sure we aren't autopublishing
        problem_child = self.store.create_child(self.user_id, chapter_location, 'problem', 'Problem_Child')
        self.assertEqual(self.store.compute_publish_state(problem_child), PublishState.private)

        # test create_item of NOT direct-only category to make sure we aren't autopublishing
        problem_item = self.store.create_item(self.user_id, test_course_key, 'problem', 'Problem_Item')
        self.assertEqual(self.store.compute_publish_state(problem_item), PublishState.private)

        # test update_item of NOT direct-only category to make sure we aren't autopublishing
        problem_item.display_name = 'Problem_Item1'
        problem_item = self.store.update_item(problem_item, self.user_id)
        self.assertEqual(self.store.compute_publish_state(problem_item), PublishState.private)

        # test delete_item of NOT direct-only category to make sure we aren't autopublishing
        self.store.delete_item(problem_child.location, self.user_id)
        chapter = self.store.get_item(chapter.location.for_branch(None))
        self.assertEqual(self.store.compute_publish_state(chapter), PublishState.public)

    @ddt.data('draft', 'split')
    def test_get_courses_for_wiki_shared(self, default_ms):
        """
        Test two courses sharing the same wiki
        """
        self.initdb(default_ms)

        # verify initial state - initially, we should have a wiki for the Mongo course
        wiki_courses = self.store.get_courses_for_wiki('999')
        self.assertIn(
            self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None),  # Branch agnostic
            wiki_courses
        )

        # set Mongo course to share the wiki with simple course
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        mongo_course.wiki_slug = 'simple'
        self.store.update_item(mongo_course, self.user_id)

        # now mongo_course should not be retrievable with old wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('999')
        self.assertEqual(len(wiki_courses), 0)

        # but there should be two courses with wiki_slug 'simple'
        wiki_courses = self.store.get_courses_for_wiki('simple')
        self.assertEqual(len(wiki_courses), 2)
        self.assertIn(
            self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None),
            wiki_courses
        )
        self.assertIn(self.course_locations[self.XML_COURSEID2].course_key, wiki_courses)

        # configure mongo course to use unique wiki_slug.
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        mongo_course.wiki_slug = 'MITx.999.2013_Spring'
        self.store.update_item(mongo_course, self.user_id)
        # it should be retrievable with its new wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('MITx.999.2013_Spring')
        self.assertEqual(len(wiki_courses), 1)
        self.assertIn(
            self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None),
            wiki_courses
        )
        # and NOT retriveable with its old wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('simple')
        self.assertEqual(len(wiki_courses), 1)
        self.assertNotIn(
            self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None),
            wiki_courses
        )
        self.assertIn(
            self.course_locations[self.XML_COURSEID2].course_key,
            wiki_courses
        )

    @ddt.data('draft', 'split')
    def test_branch_setting(self, default_ms):
        """
        Test the branch_setting context manager
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        problem_location = self.problem_x1a_1.for_branch(None)
        problem_original_name = 'Problem_x1a_1'

        course_key = problem_location.course_key
        problem_new_name = 'New Problem Name'

        def assertNumProblems(display_name, expected_number):
            """
            Asserts the number of problems with the given display name is the given expected number.
            """
            self.assertEquals(
                len(self.store.get_items(course_key.for_branch(None), settings={'display_name': display_name})),
                expected_number
            )

        def assertProblemNameEquals(expected_display_name):
            """
            Asserts the display_name of the xblock at problem_location matches the given expected value.
            """
            # check the display_name of the problem
            problem = self.store.get_item(problem_location)
            self.assertEquals(problem.display_name, expected_display_name)

            # there should be only 1 problem with the expected_display_name
            assertNumProblems(expected_display_name, 1)

        # verify Draft problem
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            self.assertTrue(self.store.has_item(problem_location))
            assertProblemNameEquals(problem_original_name)

        # verify Published problem doesn't exist
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            self.assertFalse(self.store.has_item(problem_location))
            with self.assertRaises(ItemNotFoundError):
                self.store.get_item(problem_location)

        # PUBLISH the problem
        self.store.publish(self.vertical_x1a, self.user_id)
        self.store.publish(problem_location, self.user_id)

        # verify Published problem
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            self.assertTrue(self.store.has_item(problem_location))
            assertProblemNameEquals(problem_original_name)

        # verify Draft-preferred
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            assertProblemNameEquals(problem_original_name)

        # EDIT name
        problem = self.store.get_item(problem_location)
        problem.display_name = problem_new_name
        self.store.update_item(problem, self.user_id)

        # verify Draft problem has new name
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            assertProblemNameEquals(problem_new_name)

        # verify Published problem still has old name
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assertProblemNameEquals(problem_original_name)
            # there should be no published problems with the new name
            assertNumProblems(problem_new_name, 0)

        # PUBLISH the problem
        self.store.publish(problem_location, self.user_id)

        # verify Published problem has new name
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assertProblemNameEquals(problem_new_name)
            # there should be no published problems with the old name
            assertNumProblems(problem_original_name, 0)

    def verify_default_store(self, store_type):
        # verify default_store property
        self.assertEquals(self.store.default_modulestore.get_modulestore_type(), store_type)

        # verify internal helper method
        store = self.store._get_modulestore_for_courseid()
        self.assertEquals(store.get_modulestore_type(), store_type)

        # verify store used for creating a course
        try:
            course = self.store.create_course("org", "course{}".format(uuid4().hex[:3]), "run", self.user_id)
            self.assertEquals(course.system.modulestore.get_modulestore_type(), store_type)
        except NotImplementedError:
            self.assertEquals(store_type, ModuleStoreEnum.Type.xml)

    @ddt.data(ModuleStoreEnum.Type.mongo, ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.xml)
    def test_default_store(self, default_ms):
        """
        Test the default store context manager
        """
        # initialize the mixed modulestore
        self._initialize_mixed()

        with self.store.default_store(default_ms):
            self.verify_default_store(default_ms)

    def test_default_store_nested(self):
        """
        Test the default store context manager, nested within one another
        """
        # initialize the mixed modulestore
        self._initialize_mixed()

        with self.store.default_store(ModuleStoreEnum.Type.mongo):
            self.verify_default_store(ModuleStoreEnum.Type.mongo)
            with self.store.default_store(ModuleStoreEnum.Type.split):
                self.verify_default_store(ModuleStoreEnum.Type.split)
                with self.store.default_store(ModuleStoreEnum.Type.xml):
                    self.verify_default_store(ModuleStoreEnum.Type.xml)
                self.verify_default_store(ModuleStoreEnum.Type.split)
            self.verify_default_store(ModuleStoreEnum.Type.mongo)

    def test_default_store_fake(self):
        """
        Test the default store context manager, asking for a fake store
        """
        # initialize the mixed modulestore
        self._initialize_mixed()

        fake_store = "fake"
        with self.assertRaisesRegexp(Exception, "Cannot find store of type {}".format(fake_store)):
            with self.store.default_store(fake_store):
                pass  # pragma: no cover

#=============================================================================================================
# General utils for not using django settings
#=============================================================================================================


def load_function(path):
    """
    Load a function by name.

    path is a string of the form "path.to.module.function"
    returns the imported python object `function` from `path.to.module`
    """
    module_path, _, name = path.rpartition('.')
    return getattr(import_module(module_path), name)


# pylint: disable=unused-argument
def create_modulestore_instance(engine, contentstore, doc_store_config, options, i18n_service=None):
    """
    This will return a new instance of a modulestore given an engine and options
    """
    class_ = load_function(engine)

    return class_(
        doc_store_config=doc_store_config,
        contentstore=contentstore,
        branch_setting_func=lambda: ModuleStoreEnum.Branch.draft_preferred,
        **options
    )
