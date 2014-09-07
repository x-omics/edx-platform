import django.test
from django.contrib.auth.models import User
from django.conf import settings
from django.http import Http404

from django.test.utils import override_settings

from student.models import CourseEnrollment
from course_groups.models import CourseUserGroup
from course_groups import cohorts

from xmodule.modulestore.django import modulestore, clear_existing_modulestores
from opaque_keys.edx.locations import SlashSeparatedCourseKey

from xmodule.modulestore.tests.django_utils import mixed_store_config

# NOTE: running this with the lms.envs.test config works without
# manually overriding the modulestore.  However, running with
# cms.envs.test doesn't.

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT
TEST_MAPPING = {'edX/toy/2012_Fall': 'xml'}
TEST_DATA_MIXED_MODULESTORE = mixed_store_config(TEST_DATA_DIR, TEST_MAPPING)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestCohorts(django.test.TestCase):

    @staticmethod
    def topic_name_to_id(course, name):
        """
        Given a discussion topic name, return an id for that name (includes
        course and url_name).
        """
        return "{course}_{run}_{name}".format(course=course.location.course,
                                              run=course.url_name,
                                              name=name)

    @staticmethod
    def config_course_cohorts(course, discussions,
                              cohorted,
                              cohorted_discussions=None,
                              auto_cohort=None,
                              auto_cohort_groups=None):
        """
        Given a course with no discussion set up, add the discussions and set
        the cohort config appropriately.

        Arguments:
            course: CourseDescriptor
            discussions: list of topic names strings.  Picks ids and sort_keys
                automatically.
            cohorted: bool.
            cohorted_discussions: optional list of topic names.  If specified,
                converts them to use the same ids as topic names.
            auto_cohort: optional bool.
            auto_cohort_groups: optional list of strings
                      (names of groups to put students into).

        Returns:
            Nothing -- modifies course in place.
        """
        def to_id(name):
            return TestCohorts.topic_name_to_id(course, name)

        topics = dict((name, {"sort_key": "A",
                              "id": to_id(name)})
                      for name in discussions)

        course.discussion_topics = topics

        d = {"cohorted": cohorted}
        if cohorted_discussions is not None:
            d["cohorted_discussions"] = [to_id(name)
                                         for name in cohorted_discussions]

        if auto_cohort is not None:
            d["auto_cohort"] = auto_cohort
        if auto_cohort_groups is not None:
            d["auto_cohort_groups"] = auto_cohort_groups

        course.cohort_config = d

    def setUp(self):
        """
        Make sure that course is reloaded every time--clear out the modulestore.
        """
        clear_existing_modulestores()
        self.toy_course_key = SlashSeparatedCourseKey("edX", "toy", "2012_Fall")

    def test_is_course_cohorted(self):
        """
        Make sure cohorts.is_course_cohorted() correctly reports if a course is cohorted or not.
        """
        course = modulestore().get_course(self.toy_course_key)
        self.assertFalse(course.is_cohorted)
        self.assertFalse(cohorts.is_course_cohorted(course.id))

        self.config_course_cohorts(course, [], cohorted=True)

        self.assertTrue(course.is_cohorted)
        self.assertTrue(cohorts.is_course_cohorted(course.id))

        # Make sure we get a Http404 if there's no course
        fake_key = SlashSeparatedCourseKey('a', 'b', 'c')
        self.assertRaises(Http404, lambda: cohorts.is_course_cohorted(fake_key))

    def test_get_cohort_id(self):
        """
        Make sure that cohorts.get_cohort_id() correctly returns the cohort id, or raises a ValueError when given an
        invalid course key.
        """
        course = modulestore().get_course(self.toy_course_key)
        self.assertFalse(course.is_cohorted)

        user = User.objects.create(username="test", email="a@b.com")
        self.assertIsNone(cohorts.get_cohort_id(user, course.id))

        self.config_course_cohorts(course, [], cohorted=True)
        cohort = CourseUserGroup.objects.create(name="TestCohort",
                                                course_id=course.id,
                                                group_type=CourseUserGroup.COHORT)
        cohort.users.add(user)
        self.assertEqual(cohorts.get_cohort_id(user, course.id), cohort.id)

        self.assertRaises(
            ValueError,
            lambda: cohorts.get_cohort_id(user, SlashSeparatedCourseKey("course", "does_not", "exist"))
        )

    def test_get_cohort(self):
        """
        Make sure cohorts.get_cohort() does the right thing when the course is cohorted
        """
        course = modulestore().get_course(self.toy_course_key)
        self.assertEqual(course.id, self.toy_course_key)
        self.assertFalse(course.is_cohorted)

        user = User.objects.create(username="test", email="a@b.com")
        other_user = User.objects.create(username="test2", email="a2@b.com")

        self.assertIsNone(cohorts.get_cohort(user, course.id), "No cohort created yet")

        cohort = CourseUserGroup.objects.create(name="TestCohort",
                                                course_id=course.id,
                                                group_type=CourseUserGroup.COHORT)

        cohort.users.add(user)

        self.assertIsNone(cohorts.get_cohort(user, course.id),
                          "Course isn't cohorted, so shouldn't have a cohort")

        # Make the course cohorted...
        self.config_course_cohorts(course, [], cohorted=True)

        self.assertEquals(cohorts.get_cohort(user, course.id).id, cohort.id,
                          "Should find the right cohort")

        self.assertEquals(cohorts.get_cohort(other_user, course.id), None,
                          "other_user shouldn't have a cohort")

    def test_auto_cohorting(self):
        """
        Make sure cohorts.get_cohort() does the right thing when the course is auto_cohorted
        """
        course = modulestore().get_course(self.toy_course_key)
        self.assertFalse(course.is_cohorted)

        user1 = User.objects.create(username="test", email="a@b.com")
        user2 = User.objects.create(username="test2", email="a2@b.com")
        user3 = User.objects.create(username="test3", email="a3@b.com")

        cohort = CourseUserGroup.objects.create(name="TestCohort",
                                                course_id=course.id,
                                                group_type=CourseUserGroup.COHORT)

        # user1 manually added to a cohort
        cohort.users.add(user1)

        # Make the course auto cohorted...
        self.config_course_cohorts(course, [], cohorted=True,
                                   auto_cohort=True,
                                   auto_cohort_groups=["AutoGroup"])

        self.assertEquals(cohorts.get_cohort(user1, course.id).id, cohort.id,
                          "user1 should stay put")

        self.assertEquals(cohorts.get_cohort(user2, course.id).name, "AutoGroup",
                          "user2 should be auto-cohorted")

        # Now make the group list empty
        self.config_course_cohorts(course, [], cohorted=True,
                                   auto_cohort=True,
                                   auto_cohort_groups=[])

        self.assertEquals(cohorts.get_cohort(user3, course.id), None,
                          "No groups->no auto-cohorting")

        # Now make it different
        self.config_course_cohorts(course, [], cohorted=True,
                                   auto_cohort=True,
                                   auto_cohort_groups=["OtherGroup"])

        self.assertEquals(cohorts.get_cohort(user3, course.id).name, "OtherGroup",
                          "New list->new group")
        self.assertEquals(cohorts.get_cohort(user2, course.id).name, "AutoGroup",
                          "user2 should still be in originally placed cohort")

    def test_auto_cohorting_randomization(self):
        """
        Make sure cohorts.get_cohort() randomizes properly.
        """
        course = modulestore().get_course(self.toy_course_key)
        self.assertFalse(course.is_cohorted)

        groups = ["group_{0}".format(n) for n in range(5)]
        self.config_course_cohorts(course, [], cohorted=True,
                                   auto_cohort=True,
                                   auto_cohort_groups=groups)

        # Assign 100 users to cohorts
        for i in range(100):
            user = User.objects.create(username="test_{0}".format(i),
                                       email="a@b{0}.com".format(i))
            cohorts.get_cohort(user, course.id)

        # Now make sure that the assignment was at least vaguely random:
        # each cohort should have at least 1, and fewer than 50 students.
        # (with 5 groups, probability of 0 users in any group is about
        # .8**100= 2.0e-10)
        for cohort_name in groups:
            cohort = cohorts.get_cohort_by_name(course.id, cohort_name)
            num_users = cohort.users.count()
            self.assertGreater(num_users, 1)
            self.assertLess(num_users, 50)

    def test_get_course_cohorts(self):
        course1_key = SlashSeparatedCourseKey('a', 'b', 'c')
        course2_key = SlashSeparatedCourseKey('e', 'f', 'g')

        # add some cohorts to course 1
        cohort = CourseUserGroup.objects.create(name="TestCohort",
                                                course_id=course1_key,
                                                group_type=CourseUserGroup.COHORT)

        cohort = CourseUserGroup.objects.create(name="TestCohort2",
                                                course_id=course1_key,
                                                group_type=CourseUserGroup.COHORT)

        # second course should have no cohorts
        self.assertEqual(cohorts.get_course_cohorts(course2_key), [])

        course1_cohorts = sorted([c.name for c in cohorts.get_course_cohorts(course1_key)])
        self.assertEqual(course1_cohorts, ['TestCohort', 'TestCohort2'])

    def test_is_commentable_cohorted(self):
        course = modulestore().get_course(self.toy_course_key)
        self.assertFalse(course.is_cohorted)

        def to_id(name):
            return self.topic_name_to_id(course, name)

        # no topics
        self.assertFalse(cohorts.is_commentable_cohorted(course.id, to_id("General")),
                         "Course doesn't even have a 'General' topic")

        # not cohorted
        self.config_course_cohorts(course, ["General", "Feedback"],
                                   cohorted=False)

        self.assertFalse(cohorts.is_commentable_cohorted(course.id, to_id("General")),
                         "Course isn't cohorted")

        # cohorted, but top level topics aren't
        self.config_course_cohorts(course, ["General", "Feedback"],
                                   cohorted=True)

        self.assertTrue(course.is_cohorted)
        self.assertFalse(cohorts.is_commentable_cohorted(course.id, to_id("General")),
                         "Course is cohorted, but 'General' isn't.")

        self.assertTrue(
            cohorts.is_commentable_cohorted(course.id, to_id("random")),
            "Non-top-level discussion is always cohorted in cohorted courses.")

        # cohorted, including "Feedback" top-level topics aren't
        self.config_course_cohorts(course, ["General", "Feedback"],
                                   cohorted=True,
                                   cohorted_discussions=["Feedback"])

        self.assertTrue(course.is_cohorted)
        self.assertFalse(cohorts.is_commentable_cohorted(course.id, to_id("General")),
                         "Course is cohorted, but 'General' isn't.")

        self.assertTrue(
            cohorts.is_commentable_cohorted(course.id, to_id("Feedback")),
            "Feedback was listed as cohorted.  Should be.")

    def test_get_cohorted_commentables(self):
        """
        Make sure cohorts.get_cohorted_commentables() correctly returns a list of strings representing cohorted
        commentables.  Also verify that we can't get the cohorted commentables from a course which does not exist.
        """
        course = modulestore().get_course(self.toy_course_key)

        self.assertEqual(cohorts.get_cohorted_commentables(course.id), set())

        self.config_course_cohorts(course, [], cohorted=True)
        self.assertEqual(cohorts.get_cohorted_commentables(course.id), set())

        self.config_course_cohorts(
            course, ["General", "Feedback"],
            cohorted=True,
            cohorted_discussions=["Feedback"]
        )
        self.assertItemsEqual(
            cohorts.get_cohorted_commentables(course.id),
            set([self.topic_name_to_id(course, "Feedback")])
        )

        self.config_course_cohorts(
            course, ["General", "Feedback"],
            cohorted=True,
            cohorted_discussions=["General", "Feedback"]
        )
        self.assertItemsEqual(
            cohorts.get_cohorted_commentables(course.id),
            set([self.topic_name_to_id(course, "General"),
                 self.topic_name_to_id(course, "Feedback")])
        )
        self.assertRaises(
            Http404,
            lambda: cohorts.get_cohorted_commentables(SlashSeparatedCourseKey("course", "does_not", "exist"))
        )

    def test_get_cohort_by_name(self):
        """
        Make sure cohorts.get_cohort_by_name() properly finds a cohort by name for a given course.  Also verify that it
        raises an error when the cohort is not found.
        """
        course = modulestore().get_course(self.toy_course_key)

        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: cohorts.get_cohort_by_name(course.id, "CohortDoesNotExist")
        )

        cohort = CourseUserGroup.objects.create(
            name="MyCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )

        self.assertEqual(cohorts.get_cohort_by_name(course.id, "MyCohort"), cohort)

        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: cohorts.get_cohort_by_name(SlashSeparatedCourseKey("course", "does_not", "exist"), cohort)
        )

    def test_get_cohort_by_id(self):
        """
        Make sure cohorts.get_cohort_by_id() properly finds a cohort by id for a given
        course.
        """
        course = modulestore().get_course(self.toy_course_key)
        cohort = CourseUserGroup.objects.create(
            name="MyCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )

        self.assertEqual(cohorts.get_cohort_by_id(course.id, cohort.id), cohort)

        cohort.delete()

        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: cohorts.get_cohort_by_id(course.id, cohort.id)
        )

    def test_add_cohort(self):
        """
        Make sure cohorts.add_cohort() properly adds a cohort to a course and handles
        errors.
        """
        course = modulestore().get_course(self.toy_course_key)
        added_cohort = cohorts.add_cohort(course.id, "My Cohort")

        self.assertEqual(added_cohort.name, "My Cohort")
        self.assertRaises(
            ValueError,
            lambda: cohorts.add_cohort(course.id, "My Cohort")
        )
        self.assertRaises(
            ValueError,
            lambda: cohorts.add_cohort(SlashSeparatedCourseKey("course", "does_not", "exist"), "My Cohort")
        )

    def test_add_user_to_cohort(self):
        """
        Make sure cohorts.add_user_to_cohort() properly adds a user to a cohort and
        handles errors.
        """
        course_user = User.objects.create(username="Username", email="a@b.com")
        User.objects.create(username="RandomUsername", email="b@b.com")
        course = modulestore().get_course(self.toy_course_key)
        CourseEnrollment.enroll(course_user, self.toy_course_key)
        first_cohort = CourseUserGroup.objects.create(
            name="FirstCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )
        second_cohort = CourseUserGroup.objects.create(
            name="SecondCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )

        # Success cases
        # We shouldn't get back a previous cohort, since the user wasn't in one
        self.assertEqual(
            cohorts.add_user_to_cohort(first_cohort, "Username"),
            (course_user, None)
        )
        # Should get (user, previous_cohort_name) when moved from one cohort to
        # another
        self.assertEqual(
            cohorts.add_user_to_cohort(second_cohort, "Username"),
            (course_user, "FirstCohort")
        )

        # Error cases
        # Should get ValueError if user already in cohort
        self.assertRaises(
            ValueError,
            lambda: cohorts.add_user_to_cohort(second_cohort, "Username")
        )
        # UserDoesNotExist if user truly does not exist
        self.assertRaises(
            User.DoesNotExist,
            lambda: cohorts.add_user_to_cohort(first_cohort, "non_existent_username")
        )

    def test_get_course_cohort_names(self):
        """
        Make sure cohorts.get_course_cohort_names() properly returns a list of cohort
        names for a given course.
        """
        course = modulestore().get_course(self.toy_course_key)

        self.assertItemsEqual(
            cohorts.get_course_cohort_names(course.id),
            []
        )
        self.assertItemsEqual(
            cohorts.get_course_cohort_names(SlashSeparatedCourseKey('course', 'does_not', 'exist')),
            []
        )

        CourseUserGroup.objects.create(
            name="FirstCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )

        self.assertItemsEqual(
            cohorts.get_course_cohort_names(course.id),
            ["FirstCohort"]
        )

        CourseUserGroup.objects.create(
            name="SecondCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )

        self.assertItemsEqual(
            cohorts.get_course_cohort_names(course.id),
            ["FirstCohort", "SecondCohort"]
        )

    def test_delete_empty_cohort(self):
        """
        Make sure that cohorts.delete_empty_cohort() properly removes an empty cohort
        for a given course.
        """
        course = modulestore().get_course(self.toy_course_key)
        user = User.objects.create(username="Username", email="a@b.com")
        empty_cohort = CourseUserGroup.objects.create(
            name="EmptyCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )
        nonempty_cohort = CourseUserGroup.objects.create(
            name="NonemptyCohort",
            course_id=course.id,
            group_type=CourseUserGroup.COHORT
        )
        nonempty_cohort.users.add(user)

        cohorts.delete_empty_cohort(course.id, "EmptyCohort")

        # Make sure we cannot access the deleted cohort
        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: CourseUserGroup.objects.get(
                course_id=course.id,
                group_type=CourseUserGroup.COHORT,
                id=empty_cohort.id
            )
        )
        self.assertRaises(
            ValueError,
            lambda: cohorts.delete_empty_cohort(course.id, "NonemptyCohort")
        )
        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: cohorts.delete_empty_cohort(SlashSeparatedCourseKey('course', 'does_not', 'exist'), "EmptyCohort")
        )
        self.assertRaises(
            CourseUserGroup.DoesNotExist,
            lambda: cohorts.delete_empty_cohort(course.id, "NonExistentCohort")
        )
