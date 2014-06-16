"""
Unit tests for the container page.
"""

import re
from contentstore.utils import compute_publish_state
from contentstore.views.tests.utils import StudioPageTestCase
from xmodule.modulestore import PublishState
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.factories import ItemFactory


class ContainerPageTestCase(StudioPageTestCase):
    """
    Unit tests for the container page.
    """

    container_view = 'container_preview'
    reorderable_child_view = 'reorderable_container_child_preview'

    def setUp(self):
        super(ContainerPageTestCase, self).setUp()
        self.vertical = ItemFactory.create(parent_location=self.sequential.location,
                                           category='vertical', display_name='Unit')
        self.html = ItemFactory.create(parent_location=self.vertical.location,
                                        category="html", display_name="HTML")
        self.child_container = ItemFactory.create(parent_location=self.vertical.location,
                                                  category='split_test', display_name='Split Test')
        self.child_vertical = ItemFactory.create(parent_location=self.child_container.location,
                                                 category='vertical', display_name='Child Vertical')
        self.video = ItemFactory.create(parent_location=self.child_vertical.location,
                                        category="video", display_name="My Video")
        self.store = modulestore()

    def test_container_html(self):
        self._test_html_content(
            self.child_container,
            expected_section_tag=(
                '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '
                'data-locator="{0}" data-course-key="{0.course_key}">'.format(self.child_container.location)
            ),
            expected_breadcrumbs=(
                r'<a href="/unit/{}"\s*'
                r'class="navigation-link navigation-parent">Unit</a>\s*'
                r'<a href="#" class="navigation-link navigation-current">Split Test</a>'
            ).format(re.escape(unicode(self.vertical.location)))
        )

    def test_container_on_container_html(self):
        """
        Create the scenario of an xblock with children (non-vertical) on the container page.
        This should create a container page that is a child of another container page.
        """
        draft_container = ItemFactory.create(
            parent_location=self.child_container.location,
            category="wrapper", display_name="Wrapper"
        )
        ItemFactory.create(
            parent_location=draft_container.location,
            category="html", display_name="Child HTML"
        )

        def test_container_html(xblock):
            self._test_html_content(
                xblock,
                expected_section_tag=(
                    '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '
                    'data-locator="{0}" data-course-key="{0.course_key}">'.format(draft_container.location)
                ),
                expected_breadcrumbs=(
                    r'<a href="/unit/{unit}"\s*'
                    r'class="navigation-link navigation-parent">Unit</a>\s*'
                    r'<a href="/container/{split_test}"\s*'
                    r'class="navigation-link navigation-parent">Split Test</a>\s*'
                    r'<a href="#" class="navigation-link navigation-current">Wrapper</a>'
                ).format(
                    unit=re.escape(unicode(self.vertical.location)),
                    split_test=re.escape(unicode(self.child_container.location))
                )
            )

        # Test the draft version of the container
        test_container_html(draft_container)

        # Now publish the unit and validate again
        self.store.publish(self.vertical.location, 0)
        draft_container = self.store.publish(draft_container.location, 0)
        test_container_html(draft_container)

    def _test_html_content(self, xblock, expected_section_tag, expected_breadcrumbs):
        """
        Get the HTML for a container page and verify the section tag is correct
        and the breadcrumbs trail is correct.
        """
        html = self.get_page_html(xblock)
        publish_state = compute_publish_state(xblock)
        self.assertIn(expected_section_tag, html)
        # Verify the navigation link at the top of the page is correct.
        self.assertRegexpMatches(html, expected_breadcrumbs)

        # Verify the link that allows users to change publish status.
        if publish_state == PublishState.public:
            expected_message = 'you need to edit unit <a href="/unit/{}">Unit</a> as a draft.'
        else:
            expected_message = 'your changes will be published with unit <a href="/unit/{}">Unit</a>.'
        expected_unit_link = expected_message.format(self.vertical.location)
        self.assertIn(expected_unit_link, html)

    def test_public_container_preview_html(self):
        """
        Verify that a public xblock's container preview returns the expected HTML.
        """
        published_unit = self.store.publish(self.vertical.location, 0)
        published_child_container = self.store.publish(self.child_container.location, 0)
        published_child_vertical = self.store.publish(self.child_vertical.location, 0)
        self.validate_preview_html(published_unit, self.container_view,
                                   can_edit=False, can_reorder=False, can_add=False)
        self.validate_preview_html(published_child_container, self.container_view,
                                   can_edit=False, can_reorder=False, can_add=False)
        self.validate_preview_html(published_child_vertical, self.reorderable_child_view,
                                   can_edit=False, can_reorder=False, can_add=False)

    def test_draft_container_preview_html(self):
        """
        Verify that a draft xblock's container preview returns the expected HTML.
        """
        self.validate_preview_html(self.vertical, self.container_view,
                                   can_edit=True, can_reorder=True, can_add=True)
        self.validate_preview_html(self.child_container, self.container_view,
                                   can_edit=True, can_reorder=True, can_add=True)
        self.validate_preview_html(self.child_vertical, self.reorderable_child_view,
                                   can_edit=True, can_reorder=True, can_add=True)

    def test_public_child_container_preview_html(self):
        """
        Verify that a public container rendered as a child of the container page returns the expected HTML.
        """
        empty_child_container = ItemFactory.create(parent_location=self.vertical.location,
                                                   category='split_test', display_name='Split Test')
        published_empty_child_container = self.store.publish(empty_child_container.location, '**replace_user**')
        self.validate_preview_html(published_empty_child_container, self.reorderable_child_view,
                                   can_reorder=False, can_edit=False, can_add=False)

    def test_draft_child_container_preview_html(self):
        """
        Verify that a draft container rendered as a child of the container page returns the expected HTML.
        """
        empty_child_container = ItemFactory.create(parent_location=self.vertical.location,
                                                   category='split_test', display_name='Split Test')
        self.validate_preview_html(empty_child_container, self.reorderable_child_view,
                                   can_reorder=True, can_edit=True, can_add=False)
