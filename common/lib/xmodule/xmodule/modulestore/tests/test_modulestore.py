from nose.tools import assert_equals, assert_true, assert_false  # pylint: disable=E0611


def check_has_course_method(modulestore, locator, locator_key_fields):
    error_message = "Called has_course with query {0} and ignore_case is {1}."

    for ignore_case in [True, False]:

        # should find the course with exact locator
        assert_true(modulestore.has_course(locator, ignore_case))

        for key_field in locator_key_fields:
            if getattr(locator, key_field):
                locator_changes_that_should_not_be_found = [  # pylint: disable=invalid-name
                    # replace value for one of the keys
                    {key_field: 'fake'},
                    # add a character at the end
                    {key_field: getattr(locator, key_field) + 'X'},
                    # add a character in the beginning
                    {key_field: 'X' + getattr(locator, key_field)},
                ]
                for changes in locator_changes_that_should_not_be_found:
                    search_locator = locator.replace(**changes)
                    assert_false(
                        modulestore.has_course(search_locator),
                        error_message.format(search_locator, ignore_case)
                    )

                # test case [in]sensitivity
                locator_case_changes = [
                    {key_field: getattr(locator, key_field).upper()},
                    {key_field: getattr(locator, key_field).capitalize()},
                    {key_field: getattr(locator, key_field).capitalize().swapcase()},
                ]
                for changes in locator_case_changes:
                    search_locator = locator.replace(**changes)
                    # if ignore_case is true, the course would be found with a different-cased course locator.
                    # if ignore_case is false, the course should NOT found given an incorrectly-cased locator.
                    assert_equals(
                        modulestore.has_course(search_locator, ignore_case) is not None,
                        ignore_case,
                        error_message.format(search_locator, ignore_case)
                    )
