from contextlib import contextmanager

from bok_choy.page_object import PageObject
from bok_choy.promise import EmptyPromise

from .course_page import CoursePage


class DiscussionPageMixin(object):

    def is_ajax_finished(self):
        return self.browser.execute_script("return jQuery.active") == 0


class DiscussionThreadPage(PageObject, DiscussionPageMixin):
    url = None

    def __init__(self, browser, thread_selector):
        super(DiscussionThreadPage, self).__init__(browser)
        self.thread_selector = thread_selector

    def _find_within(self, selector):
        """
        Returns a query corresponding to the given CSS selector within the scope
        of this thread page
        """
        return self.q(css=self.thread_selector + " " + selector)

    def is_browser_on_page(self):
        return self.q(css=self.thread_selector).present

    def _get_element_text(self, selector):
        """
        Returns the text of the first element matching the given selector, or
        None if no such element exists
        """
        text_list = self._find_within(selector).text
        return text_list[0] if text_list else None

    def _is_element_visible(self, selector):
        query = self._find_within(selector)
        return query.present and query.visible

    @contextmanager
    def _secondary_action_menu_open(self, ancestor_selector):
        """
        Given the selector for an ancestor of a secondary menu, return a context
        manager that will open and close the menu
        """
        self._find_within(ancestor_selector + " .action-more").click()
        EmptyPromise(
            lambda: self._is_element_visible(ancestor_selector + " .actions-dropdown"),
            "Secondary action menu opened"
        ).fulfill()
        yield
        if self._is_element_visible(ancestor_selector + " .actions-dropdown"):
            self._find_within(ancestor_selector + " .action-more").click()
            EmptyPromise(
                lambda: not self._is_element_visible(ancestor_selector + " .actions-dropdown"),
                "Secondary action menu closed"
            ).fulfill()

    def get_response_total_text(self):
        """Returns the response count text, or None if not present"""
        return self._get_element_text(".response-count")

    def get_num_displayed_responses(self):
        """Returns the number of responses actually rendered"""
        return len(self._find_within(".discussion-response"))

    def get_shown_responses_text(self):
        """Returns the shown response count text, or None if not present"""
        return self._get_element_text(".response-display-count")

    def get_load_responses_button_text(self):
        """Returns the load more responses button text, or None if not present"""
        return self._get_element_text(".load-response-button")

    def load_more_responses(self):
        """Clicks the load more responses button and waits for responses to load"""
        self._find_within(".load-response-button").click()

        EmptyPromise(
            self.is_ajax_finished,
            "Loading more Responses"
        ).fulfill()

    def has_add_response_button(self):
        """Returns true if the add response button is visible, false otherwise"""
        return self._is_element_visible(".add-response-btn")

    def click_add_response_button(self):
        """
        Clicks the add response button and ensures that the response text
        field receives focus
        """
        self._find_within(".add-response-btn").first.click()
        EmptyPromise(
            lambda: self._find_within(".discussion-reply-new textarea:focus").present,
            "Response field received focus"
        ).fulfill()

    def is_response_editor_visible(self, response_id):
        """Returns true if the response editor is present, false otherwise"""
        return self._is_element_visible(".response_{} .edit-post-body".format(response_id))

    def is_response_editable(self, response_id):
        """Returns true if the edit response button is present, false otherwise"""
        return self._is_element_visible(".response_{} .discussion-response .action-edit".format(response_id))

    def start_response_edit(self, response_id):
        """Click the edit button for the response, loading the editing view"""
        with self._secondary_action_menu_open(".response_{} .discussion-response".format(response_id)):
            self._find_within(".response_{} .discussion-response .action-edit".format(response_id)).first.click()
            EmptyPromise(
                lambda: self.is_response_editor_visible(response_id),
                "Response edit started"
            ).fulfill()

    def is_show_comments_visible(self, response_id):
        """Returns true if the "show comments" link is visible for a response"""
        return self._is_element_visible(".response_{} .action-show-comments".format(response_id))

    def show_comments(self, response_id):
        """Click the "show comments" link for a response"""
        self._find_within(".response_{} .action-show-comments".format(response_id)).first.click()
        EmptyPromise(
            lambda: self._is_element_visible(".response_{} .comments".format(response_id)),
            "Comments shown"
        ).fulfill()

    def is_add_comment_visible(self, response_id):
        """Returns true if the "add comment" form is visible for a response"""
        return self._is_element_visible("#wmd-input-comment-body-{}".format(response_id))

    def is_comment_visible(self, comment_id):
        """Returns true if the comment is viewable onscreen"""
        return self._is_element_visible("#comment_{} .response-body".format(comment_id))

    def get_comment_body(self, comment_id):
        return self._get_element_text("#comment_{} .response-body".format(comment_id))

    def is_comment_deletable(self, comment_id):
        """Returns true if the delete comment button is present, false otherwise"""
        with self._secondary_action_menu_open("#comment_{}".format(comment_id)):
            return self._is_element_visible("#comment_{} .action-delete".format(comment_id))

    def delete_comment(self, comment_id):
        with self.handle_alert():
            with self._secondary_action_menu_open("#comment_{}".format(comment_id)):
                self._find_within("#comment_{} .action-delete".format(comment_id)).first.click()
        EmptyPromise(
            lambda: not self.is_comment_visible(comment_id),
            "Deleted comment was removed"
        ).fulfill()

    def is_comment_editable(self, comment_id):
        """Returns true if the edit comment button is present, false otherwise"""
        with self._secondary_action_menu_open("#comment_{}".format(comment_id)):
            return self._is_element_visible("#comment_{} .action-edit".format(comment_id))

    def is_comment_editor_visible(self, comment_id):
        """Returns true if the comment editor is present, false otherwise"""
        return self._is_element_visible(".edit-comment-body[data-id='{}']".format(comment_id))

    def _get_comment_editor_value(self, comment_id):
        return self._find_within("#wmd-input-edit-comment-body-{}".format(comment_id)).text[0]

    def start_comment_edit(self, comment_id):
        """Click the edit button for the comment, loading the editing view"""
        old_body = self.get_comment_body(comment_id)
        with self._secondary_action_menu_open("#comment_{}".format(comment_id)):
            self._find_within("#comment_{} .action-edit".format(comment_id)).first.click()
            EmptyPromise(
                lambda: (
                    self.is_comment_editor_visible(comment_id) and
                    not self.is_comment_visible(comment_id) and
                    self._get_comment_editor_value(comment_id) == old_body
                ),
                "Comment edit started"
            ).fulfill()

    def set_comment_editor_value(self, comment_id, new_body):
        """Replace the contents of the comment editor"""
        self._find_within("#comment_{} .wmd-input".format(comment_id)).fill(new_body)

    def submit_comment_edit(self, comment_id, new_comment_body):
        """Click the submit button on the comment editor"""
        self._find_within("#comment_{} .post-update".format(comment_id)).first.click()
        EmptyPromise(
            lambda: (
                not self.is_comment_editor_visible(comment_id) and
                self.is_comment_visible(comment_id) and
                self.get_comment_body(comment_id) == new_comment_body
            ),
            "Comment edit succeeded"
        ).fulfill()

    def cancel_comment_edit(self, comment_id, original_body):
        """Click the cancel button on the comment editor"""
        self._find_within("#comment_{} .post-cancel".format(comment_id)).first.click()
        EmptyPromise(
            lambda: (
                not self.is_comment_editor_visible(comment_id) and
                self.is_comment_visible(comment_id) and
                self.get_comment_body(comment_id) == original_body
            ),
            "Comment edit was canceled"
        ).fulfill()


class DiscussionSortPreferencePage(CoursePage):
    """
    Page that contain the discussion board with sorting options
    """
    def __init__(self, browser, course_id):
        super(DiscussionSortPreferencePage, self).__init__(browser, course_id)
        self.url_path = "discussion/forum"

    def is_browser_on_page(self):
        """
        Return true if the browser is on the right page else false.
        """
        return self.q(css="body.discussion .forum-nav-sort-control").present

    def get_selected_sort_preference(self):
        """
        Return the text of option that is selected for sorting.
        """
        options = self.q(css="body.discussion .forum-nav-sort-control option")
        return options.filter(lambda el: el.is_selected())[0].get_attribute("value")

    def change_sort_preference(self, sort_by):
        """
        Change the option of sorting by clicking on new option.
        """
        self.q(css="body.discussion .forum-nav-sort-control option[value='{0}']".format(sort_by)).click()

    def refresh_page(self):
        """
        Reload the page.
        """
        self.browser.refresh()


class DiscussionTabSingleThreadPage(CoursePage):
    def __init__(self, browser, course_id, thread_id):
        super(DiscussionTabSingleThreadPage, self).__init__(browser, course_id)
        self.thread_page = DiscussionThreadPage(
            browser,
            "body.discussion .discussion-article[data-id='{thread_id}']".format(thread_id=thread_id)
        )
        self.url_path = "discussion/forum/dummy/threads/" + thread_id

    def is_browser_on_page(self):
        return self.thread_page.is_browser_on_page()

    def __getattr__(self, name):
        return getattr(self.thread_page, name)


class InlineDiscussionPage(PageObject):
    url = None

    def __init__(self, browser, discussion_id):
        super(InlineDiscussionPage, self).__init__(browser)
        self._discussion_selector = (
            "body.courseware .discussion-module[data-discussion-id='{discussion_id}'] ".format(
                discussion_id=discussion_id
            )
        )

    def _find_within(self, selector):
        """
        Returns a query corresponding to the given CSS selector within the scope
        of this discussion page
        """
        return self.q(css=self._discussion_selector + " " + selector)

    def is_browser_on_page(self):
        return self.q(css=self._discussion_selector).present

    def is_discussion_expanded(self):
        return self._find_within(".discussion").present

    def expand_discussion(self):
        """Click the link to expand the discussion"""
        self._find_within(".discussion-show").first.click()
        EmptyPromise(
            self.is_discussion_expanded,
            "Discussion expanded"
        ).fulfill()

    def get_num_displayed_threads(self):
        return len(self._find_within(".discussion-thread"))

    def element_exists(self, selector):
        return self.q(css=self._discussion_selector + " " + selector).present


class InlineDiscussionThreadPage(DiscussionThreadPage):
    def __init__(self, browser, thread_id):
        super(InlineDiscussionThreadPage, self).__init__(
            browser,
            "body.courseware .discussion-module #thread_{thread_id}".format(thread_id=thread_id)
        )

    def expand(self):
        """Clicks the link to expand the thread"""
        self._find_within(".forum-thread-expand").first.click()
        EmptyPromise(
            lambda: bool(self.get_response_total_text()),
            "Thread expanded"
        ).fulfill()

    def is_thread_anonymous(self):
        return not self.q(css=".posted-details > .username").present


class DiscussionUserProfilePage(CoursePage):

    TEXT_NEXT = u'Next >'
    TEXT_PREV = u'< Previous'
    PAGING_SELECTOR = "a.discussion-pagination[data-page-number]"

    def __init__(self, browser, course_id, user_id, username, page=1):
        super(DiscussionUserProfilePage, self).__init__(browser, course_id)
        self.url_path = "discussion/forum/dummy/users/{}?page={}".format(user_id, page)
        self.username = username

    def is_browser_on_page(self):
        return (
            self.q(css='section.discussion-user-threads[data-course-id="{}"]'.format(self.course_id)).present
            and
            self.q(css='section.user-profile div.sidebar-username').present
            and
            self.q(css='section.user-profile div.sidebar-username').text[0] == self.username
        )

    def get_shown_thread_ids(self):
        elems = self.q(css="article.discussion-thread")
        return [elem.get_attribute("id")[7:] for elem in elems]

    def get_current_page(self):
        return int(self.q(css="nav.discussion-paginator li.current-page").text[0])

    def _check_pager(self, text, page_number=None):
        """
        returns True if 'text' matches the text in any of the pagination elements.  If
        page_number is provided, only return True if the element points to that result
        page.
        """
        elems = self.q(css=self.PAGING_SELECTOR).filter(lambda elem: elem.text == text)
        if page_number:
            elems = elems.filter(lambda elem: int(elem.get_attribute('data-page-number')) == page_number)
        return elems.present

    def get_clickable_pages(self):
        return sorted([
            int(elem.get_attribute('data-page-number'))
            for elem in self.q(css=self.PAGING_SELECTOR)
            if str(elem.text).isdigit()
        ])

    def is_prev_button_shown(self, page_number=None):
        return self._check_pager(self.TEXT_PREV, page_number)

    def is_next_button_shown(self, page_number=None):
        return self._check_pager(self.TEXT_NEXT, page_number)

    def _click_pager_with_text(self, text, page_number):
        """
        click the first pagination element with whose text is `text` and ensure
        the resulting page number matches `page_number`.
        """
        targets = [elem for elem in self.q(css=self.PAGING_SELECTOR) if elem.text == text]
        targets[0].click()
        EmptyPromise(
            lambda: self.get_current_page() == page_number,
            "navigated to desired page"
        ).fulfill()

    def click_prev_page(self):
        self._click_pager_with_text(self.TEXT_PREV, self.get_current_page() - 1)

    def click_next_page(self):
        self._click_pager_with_text(self.TEXT_NEXT, self.get_current_page() + 1)

    def click_on_page(self, page_number):
        self._click_pager_with_text(unicode(page_number), page_number)


class DiscussionTabHomePage(CoursePage, DiscussionPageMixin):

    ALERT_SELECTOR = ".discussion-body .forum-nav .search-alert"

    def __init__(self, browser, course_id):
        super(DiscussionTabHomePage, self).__init__(browser, course_id)
        self.url_path = "discussion/forum/"

    def is_browser_on_page(self):
        return self.q(css=".discussion-body section.home-header").present

    def perform_search(self, text="dummy"):
        self.q(css=".forum-nav-search-input").fill(text + chr(10))
        EmptyPromise(
            self.is_ajax_finished,
            "waiting for server to return result"
        ).fulfill()

    def get_search_alert_messages(self):
        return self.q(css=self.ALERT_SELECTOR + " .message").text

    def get_search_alert_links(self):
        return self.q(css=self.ALERT_SELECTOR + " .link-jump")

    def dismiss_alert_message(self, text):
        """
        dismiss any search alert message containing the specified text.
        """
        def _match_messages(text):
            return self.q(css=".search-alert").filter(lambda elem: text in elem.text)

        for alert_id in _match_messages(text).attrs("id"):
            self.q(css="{}#{} a.dismiss".format(self.ALERT_SELECTOR, alert_id)).click()
        EmptyPromise(
            lambda: _match_messages(text).results == [],
            "waiting for dismissed alerts to disappear"
        ).fulfill()
