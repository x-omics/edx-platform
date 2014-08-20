import re
import logging
import datetime
import json
from json.encoder import JSONEncoder

from opaque_keys.edx.locations import Location
from xmodule.modulestore.exceptions import ItemNotFoundError
from contentstore.utils import course_image_url
from models.settings import course_grading
from xmodule.fields import Date
from xmodule.modulestore.django import modulestore

class CourseDetails(object):
    def __init__(self, org, course_id, run):
        # still need these for now b/c the client's screen shows these 3 fields
        self.org = org
        self.course_id = course_id
        self.run = run
        self.start_date = None  # 'start'
        self.end_date = None  # 'end'
        self.enrollment_start = None
        self.enrollment_end = None
        self.syllabus = None  # a pdf file asset
        self.short_description = ""
        self.overview = ""  # html to render as the overview
        self.intro_video = None  # a video pointer
        self.effort = None  # int hours/week
        self.course_image_name = ""
        self.course_image_asset_path = ""  # URL of the course image

    @classmethod
    def fetch(cls, course_key):
        """
        Fetch the course details for the given course from persistence and return a CourseDetails model.
        """
        descriptor = modulestore().get_course(course_key)
        course_details = cls(course_key.org, course_key.course, course_key.run)

        course_details.start_date = descriptor.start
        course_details.end_date = descriptor.end
        course_details.enrollment_start = descriptor.enrollment_start
        course_details.enrollment_end = descriptor.enrollment_end
        course_details.course_image_name = descriptor.course_image
        course_details.course_image_asset_path = course_image_url(descriptor)

        temploc = course_key.make_usage_key('about', 'syllabus')
        try:
            course_details.syllabus = modulestore().get_item(temploc).data
        except ItemNotFoundError:
            pass

        temploc = course_key.make_usage_key('about', 'short_description')
        try:
            course_details.short_description = modulestore().get_item(temploc).data
        except ItemNotFoundError:
            pass

        temploc = course_key.make_usage_key('about', 'overview')
        try:
            course_details.overview = modulestore().get_item(temploc).data
        except ItemNotFoundError:
            pass

        temploc = course_key.make_usage_key('about', 'effort')
        try:
            course_details.effort = modulestore().get_item(temploc).data
        except ItemNotFoundError:
            pass

        temploc = course_key.make_usage_key('about', 'video')
        try:
            raw_video = modulestore().get_item(temploc).data
            course_details.intro_video = CourseDetails.parse_video_tag(raw_video)
        except ItemNotFoundError:
            pass

        return course_details

    @classmethod
    def update_about_item(cls, course_key, about_key, data, course, user):
        """
        Update the about item with the new data blob. If data is None, then
        delete the about item.
        """
        temploc = course_key.make_usage_key('about', about_key)
        store = modulestore()
        if data is None:
            store.delete_item(temploc, user.id)
        else:
            try:
                about_item = store.get_item(temploc)
            except ItemNotFoundError:
                about_item = store.create_xblock(course.runtime, course.id, 'about', about_key)
            about_item.data = data
            store.update_item(about_item, user.id)

    @classmethod
    def update_from_json(cls, course_key, jsondict, user):
        """
        Decode the json into CourseDetails and save any changed attrs to the db
        """
        module_store = modulestore()
        descriptor = module_store.get_course(course_key)

        dirty = False

        # In the descriptor's setter, the date is converted to JSON using Date's to_json method.
        # Calling to_json on something that is already JSON doesn't work. Since reaching directly
        # into the model is nasty, convert the JSON Date to a Python date, which is what the
        # setter expects as input.
        date = Date()

        if 'start_date' in jsondict:
            converted = date.from_json(jsondict['start_date'])
        else:
            converted = None
        if converted != descriptor.start:
            dirty = True
            descriptor.start = converted

        if 'end_date' in jsondict:
            converted = date.from_json(jsondict['end_date'])
        else:
            converted = None

        if converted != descriptor.end:
            dirty = True
            descriptor.end = converted

        if 'enrollment_start' in jsondict:
            converted = date.from_json(jsondict['enrollment_start'])
        else:
            converted = None

        if converted != descriptor.enrollment_start:
            dirty = True
            descriptor.enrollment_start = converted

        if 'enrollment_end' in jsondict:
            converted = date.from_json(jsondict['enrollment_end'])
        else:
            converted = None

        if converted != descriptor.enrollment_end:
            dirty = True
            descriptor.enrollment_end = converted

        if 'course_image_name' in jsondict and jsondict['course_image_name'] != descriptor.course_image:
            descriptor.course_image = jsondict['course_image_name']
            dirty = True

        if dirty:
            module_store.update_item(descriptor, user.id)

        # NOTE: below auto writes to the db w/o verifying that any of the fields actually changed
        # to make faster, could compare against db or could have client send over a list of which fields changed.
        for about_type in ['syllabus', 'overview', 'effort', 'short_description']:
            cls.update_about_item(course_key, about_type, jsondict[about_type], descriptor, user)

        recomposed_video_tag = CourseDetails.recompose_video_tag(jsondict['intro_video'])
        cls.update_about_item(course_key, 'video', recomposed_video_tag, descriptor, user)

        # Could just return jsondict w/o doing any db reads, but I put the reads in as a means to confirm
        # it persisted correctly
        return CourseDetails.fetch(course_key)

    @staticmethod
    def parse_video_tag(raw_video):
        """
        Because the client really only wants the author to specify the youtube key, that's all we send to and get from the client.
        The problem is that the db stores the html markup as well (which, of course, makes any sitewide changes to how we do videos
        next to impossible.)
        """
        if not raw_video:
            return None

        keystring_matcher = re.search(r'(?<=embed/)[a-zA-Z0-9_-]+', raw_video)
        if keystring_matcher is None:
            keystring_matcher = re.search(r'<?=\d+:[a-zA-Z0-9_-]+', raw_video)

        if keystring_matcher:
            return keystring_matcher.group(0)
        else:
            logging.warn("ignoring the content because it doesn't not conform to expected pattern: " + raw_video)
            return None

    @staticmethod
    def recompose_video_tag(video_key):
        # TODO should this use a mako template? Of course, my hope is that this is a short-term workaround for the db not storing
        # the right thing
        result = None
        if video_key:
            result = '<iframe width="560" height="315" src="//www.youtube.com/embed/' + \
                video_key + '?rel=0" frameborder="0" allowfullscreen=""></iframe>'
        return result


# TODO move to a more general util?
class CourseSettingsEncoder(json.JSONEncoder):
    """
    Serialize CourseDetails, CourseGradingModel, datetime, and old Locations
    """
    def default(self, obj):
        if isinstance(obj, (CourseDetails, course_grading.CourseGradingModel)):
            return obj.__dict__
        elif isinstance(obj, Location):
            return obj.dict()
        elif isinstance(obj, datetime.datetime):
            return Date().to_json(obj)
        else:
            return JSONEncoder.default(self, obj)
