"""
A ModuleStore that knows about a special version 'draft'. Modules
marked as 'draft' are read in preference to modules without the 'draft'
version by this ModuleStore (so, access to i4x://org/course/cat/name
returns the i4x://org/course/cat/name@draft object if that exists,
and otherwise returns i4x://org/course/cat/name).
"""

from datetime import datetime
import pymongo
from pytz import UTC

from xmodule.exceptions import InvalidVersionError
from xmodule.modulestore import PublishState
from xmodule.modulestore.exceptions import ItemNotFoundError, DuplicateItemError
from xmodule.modulestore.mongo.base import MongoModuleStore, DIRECT_ONLY_CATEGORIES, DRAFT, as_draft, \
    as_published
from opaque_keys.edx.locations import Location
from xmodule.modulestore.branch_setting import BranchSetting


def wrap_draft(item):
    """
    Sets `item.is_draft` to `True` if the item is a
    draft, and `False` otherwise. Sets the item's location to the
    non-draft location in either case
    """
    setattr(item, 'is_draft', item.location.revision == DRAFT)
    item.location = item.location.replace(revision=None)
    return item


class DraftModuleStore(MongoModuleStore):
    """
    This mixin modifies a modulestore to give it draft semantics.
    That is, edits made to units are stored to locations that have the revision DRAFT,
    and when reads are made, they first read with revision DRAFT, and then fall back
    to the baseline revision only if DRAFT doesn't exist.

    This module also includes functionality to promote DRAFT modules (and optionally
    their children) to published modules.
    """

    def get_item(self, usage_key, depth=0):
        """
        Returns an XModuleDescriptor instance for the item at usage_key.

        if BranchSetting is draft, returns either draft or published item, preferring draft
        else, returns only the published item

        usage_key: A :class:`.UsageKey` instance

        depth (int): An argument that some module stores may use to prefetch
            descendents of the queried modules for more efficient results later
            in the request. The depth is counted in the number of calls to
            get_children() to cache. None indicates to cache all descendents

        Raises:
            xmodule.modulestore.exceptions.InsufficientSpecificationError
            if any segment of the usage_key is None except revision

            xmodule.modulestore.exceptions.ItemNotFoundError if no object
            is found at that usage_key
        """
        if BranchSetting.is_draft() and (usage_key.category not in DIRECT_ONLY_CATEGORIES):
            try:
                return wrap_draft(super(DraftModuleStore, self).get_item(as_draft(usage_key), depth=depth))
            except ItemNotFoundError:
                return wrap_draft(super(DraftModuleStore, self).get_item(usage_key, depth=depth))
        else:
            return super(DraftModuleStore, self).get_item(usage_key, depth=depth)

    def get_parent_locations(self, location, revision=None):
        '''
        Find all locations that are the parents of this location in this
        course.  Needed for path_to_location().

        Returns w/ revision set. If a block has both a draft a non-draft parent, it returns both
        unless revision is set or the thread's branch is set to 'published'.
        '''
        if BranchSetting.is_published():
            revision = 'published'
        return super(DraftModuleStore, self).get_parent_locations(location, revision)

    def create_xmodule(self, location, definition_data=None, metadata=None, system=None, fields={}):
        """
        Create the new xmodule but don't save it. Returns the new module with a draft locator if
        the category allows drafts. If the category does not allow drafts, just creates a published module.

        :param location: a Location--must have a category
        :param definition_data: can be empty. The initial definition_data for the kvs
        :param metadata: can be empty, the initial metadata for the kvs
        :param system: if you already have an xmodule from the course, the xmodule.system value
        """
        assert BranchSetting.is_draft()

        if location.category not in DIRECT_ONLY_CATEGORIES:
            location = as_draft(location)
        return super(DraftModuleStore, self).create_xmodule(location, definition_data, metadata, system, fields)

    def get_items(self, course_key, settings=None, content=None, **kwargs):
        """
        Returns:
            list of XModuleDescriptor instances for the matching items within the course with
            the given course_key

            if BranchSetting is draft, returns both draft and publish items, preferring the draft ones
            else, returns only the published items

        NOTE: don't use this to look for courses as the course_key is required. Use get_courses instead.

        Args:
            course_key (CourseKey): the course identifier
            settings: not used
            content: not used
            kwargs (key=value): what to look for within the course.
                Common qualifiers are ``category`` or any field name. if the target field is a list,
                then it searches for the given value in the list not list equivalence.
                Substring matching pass a regex object.
                ``name`` is another commonly provided key (Location based stores)
        """

        draft_items = []
        if BranchSetting.is_draft():
            draft_items = [
                wrap_draft(item) for item in
                super(DraftModuleStore, self).get_items(course_key, revision='draft', **kwargs)
            ]
        draft_items_locations = {item.location for item in draft_items}
        non_draft_items = [
            item for item in
            super(DraftModuleStore, self).get_items(course_key, revision=None, **kwargs)
            # filter out items that are not already in draft
            if item.location not in draft_items_locations
        ]
        return draft_items + non_draft_items

    def convert_to_draft(self, location, user_id, delete_published=False):
        """
        Copy the subtree rooted at source_location and mark the copies as draft.

        :param location: the location of the source (its revision must be None)
        :param delete_published (Boolean): intended for use by unpublish

        Raises:
            InvalidVersionError: if the source can not be made into a draft
            ItemNotFoundError: if the source does not exist
            DuplicateItemError: if the source or any of its descendants already has a draft copy
        """
        assert BranchSetting.is_draft()

        if location.category in DIRECT_ONLY_CATEGORIES:
            raise InvalidVersionError(location)
        original = self.collection.find_one({'_id': location.to_deprecated_son()})
        if not original:
            raise ItemNotFoundError(location)

        def _internal_depth_first(root):
            """
            Convert the subtree
            """
            for child in root.get('definition', {}).get('children', []):
                child_loc = Location.from_deprecated_string(child)
                child_entry = self.collection.find_one({'_id': child_loc.to_deprecated_son()})
                if not child_entry:
                    raise ItemNotFoundError(child_loc)
                _internal_depth_first(child_entry)

            root['_id']['revision'] = DRAFT
            try:
                self.collection.insert(root)
            except pymongo.errors.DuplicateKeyError:
                raise DuplicateItemError(root['_id'])

            if delete_published:
                root['_id']['revision'] = None
                self.collection.remove(root)

        _internal_depth_first(original)
        self.refresh_cached_metadata_inheritance_tree(location.course_key)

        return wrap_draft(self._load_items(location.course_key, [original])[0])

    def update_item(self, xblock, user_id=None, allow_not_found=False, force=False):
        """
        See superclass doc.
        In addition to the superclass's behavior, this method converts the unit to draft if it's not
        already draft.
        """
        assert BranchSetting.is_draft()

        if xblock.location.category in DIRECT_ONLY_CATEGORIES:
            return super(DraftModuleStore, self).update_item(xblock, user_id, allow_not_found)

        draft_loc = as_draft(xblock.location)
        try:
            if not self.has_item(draft_loc):
                self.convert_to_draft(xblock.location, user_id)
        except ItemNotFoundError:
            if not allow_not_found:
                raise

        xblock.location = draft_loc
        super(DraftModuleStore, self).update_item(xblock, user_id, allow_not_found)
        # don't allow locations to truly represent themselves as draft outside of this file
        xblock.location = as_published(xblock.location)

    def delete_item(self, location, user_id, **kwargs):
        """
        Delete an item from this modulestore.
        The method determines which revisions to delete. It disconnects and deletes the subtree.
        * Deleting a DIRECT_ONLY block, deletes both draft and published children and removes from parent.
        * Deleting a specific version of block whose parent is DIRECT_ONLY, only removes it from parent if
        the other version of block does not exist. deletes only children of same version.
        * Other deletions remove from parent of same version and subtree of same version

        This method also side effects Course if a static tab is deleted.

        Args:
            location (UsageKey)
        """
        assert BranchSetting.is_draft()

        # VS[compat] cdodge: This is a hack because static_tabs also have references from the course module, so
        # if we add one then we need to also add it to the policy information (i.e. metadata)
        # we should remove this once we can break this reference from the course to static tabs
        if location.category == 'static_tab':
            item = self.get_item(location)
            course = self._get_course_for_item(item.scope_ids.usage_id)
            existing_tabs = course.tabs or []
            course.tabs = [tab for tab in existing_tabs if tab.get('url_slug') != location.name]
            self.update_item(course, '**replace_user**')

        direct_only_root = location.category in DIRECT_ONLY_CATEGORIES
        if location.revision is None:
            revision = 'published'
        else:
            revision = DRAFT

        # remove subtree from its parent
        parents = self.get_parent_locations(location, revision=revision)
        # 2 parents iff root has draft which was moved
        for parent in parents:
            if not direct_only_root and parent.category in DIRECT_ONLY_CATEGORIES:
                # see if other version of root exists
                alt_location = location.replace(revision=DRAFT if location.revision != DRAFT else None)
                if self.has_item(alt_location):
                    continue
            parent_block = super(DraftModuleStore, self).get_item(parent, 0)
            parent_block.children.remove(as_published(location))
            parent_block.location = parent  # if the revision is supposed to be draft, ensure it is
            self.update_item(parent_block, user_id)

        if direct_only_root:
            as_functions = [as_draft, as_published]
        elif revision == DRAFT or location.revision == DRAFT:
            as_functions = [as_draft]
        else:
            as_functions = [as_published]
        self._delete_subtree(location, as_functions)

    def _delete_subtree(self, location, as_functions):
        """
        Internal method for deleting all of the subtree whose revisions match the as_functions
        """
        # now do hierarchical removal
        def _internal_depth_first(current_loc):
            """
            Depth first deletion of nodes
            """
            for rev_func in as_functions:
                current_loc = rev_func(current_loc)
                current_entry = self.collection.find_one({'_id': current_loc.to_deprecated_son()})
                if current_entry is None:
                    continue  # already deleted or not in this version
                for child_loc in current_entry.get('definition', {}).get('children', []):
                    _internal_depth_first(child_loc)
                self.collection.remove({'_id': current_entry['_id']}, safe=self.collection.safe)

        _internal_depth_first(location)
        # recompute (and update) the metadata inheritance tree which is cached
        self.refresh_cached_metadata_inheritance_tree(location.course_key)

    def publish(self, location, user_id):
        """
        Publish the subtree rooted at location to the live course and remove the drafts.
        Such publishing may cause the deletion of previously published but subsequently deleted
        child trees. Overwrites any existing published xblocks from the subtree.

        Treats the publishing of non-draftable items as merely a subtree selection from
        which to descend.

        Raises:
            ItemNotFoundError: if any of the draft subtree nodes aren't found
        """
        assert BranchSetting.is_draft()

        def _internal_depth_first(root_location):
            """
            Depth first publishing from root
            """
            draft = self.get_item(root_location)

            if draft.has_children:
                for child_loc in draft.children:
                    _internal_depth_first(child_loc)

            if root_location.category in DIRECT_ONLY_CATEGORIES or not getattr(draft, 'is_draft', False):
                # ignore noop attempt to publish something that can't be or isn't currently draft
                return

            try:
                original_published = super(DraftModuleStore, self).get_item(root_location)
            except ItemNotFoundError:
                original_published = None

            draft.published_date = datetime.now(UTC)
            draft.published_by = user_id
            if draft.has_children:
                if original_published is not None:
                    # see if previously published children were deleted. 2 reasons for children lists to differ:
                    #   1) child deleted
                    #   2) child moved
                    for child in original_published.children:
                        if child not in draft.children:
                            # did child move?
                            rents = self.get_parent_locations(child)
                            if (len(rents) == 1 and as_published(rents[0]) == root_location):
                                # deleted from draft; so, delete now that we're publishing
                                self.delete_item(child, user_id)

            super(DraftModuleStore, self).update_item(draft, user_id)
            self.collection.remove({'_id': as_draft(root_location).to_deprecated_son()})

        _internal_depth_first(location)
        return self.get_item(as_published(location))

    def unpublish(self, location, user_id):
        """
        Turn the published version into a draft, removing the published version.

        NOTE: unlike publish, this gives an error if called above the draftable level as it's intended
        to remove things from the published version
        """
        assert BranchSetting.is_draft()
        self.convert_to_draft(location, user_id, delete_published=True)

    def _query_children_for_cache_children(self, course_key, items):
        # first get non-draft in a round-trip
        to_process_non_drafts = super(DraftModuleStore, self)._query_children_for_cache_children(course_key, items)

        to_process_dict = {}
        for non_draft in to_process_non_drafts:
            to_process_dict[Location._from_deprecated_son(non_draft["_id"], course_key.run)] = non_draft

        # now query all draft content in another round-trip
        query = {
            '_id': {'$in': [
                as_draft(course_key.make_usage_key_from_deprecated_string(item)).to_deprecated_son() for item in items
            ]}
        }
        to_process_drafts = list(self.collection.find(query))

        # now we have to go through all drafts and replace the non-draft
        # with the draft. This is because the semantics of the DraftStore is to
        # always return the draft - if available
        for draft in to_process_drafts:
            draft_loc = Location._from_deprecated_son(draft["_id"], course_key.run)
            draft_as_non_draft_loc = draft_loc.replace(revision=None)

            # does non-draft exist in the collection
            # if so, replace it
            if draft_as_non_draft_loc in to_process_dict:
                to_process_dict[draft_as_non_draft_loc] = draft

        # convert the dict - which is used for look ups - back into a list
        queried_children = to_process_dict.values()

        return queried_children

    def compute_publish_state(self, xblock):
        """
        Returns whether this xblock is 'draft', 'public', or 'private'.

        'draft' content is in the process of being edited, but still has a previous
            version visible in the LMS
        'public' content is locked and visible in the LMS
        'private' content is editable and not visible in the LMS
        """
        if getattr(xblock, 'is_draft', False):
            published_xblock_location = as_published(xblock.location)
            published_item = self.collection.find_one(
                {'_id': published_xblock_location.to_deprecated_son()}
            )
            if not published_item:
                return PublishState.private
            else:
                return PublishState.draft
        else:
            return PublishState.public
